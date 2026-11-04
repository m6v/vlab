package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"html/template"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"regexp"
	"strings"
	"time"
	"github.com/gorilla/websocket"
)

const (
	daemonSock   = "/tmp/qemu/vmctrl.sock"
	templatePath = "index.html"
)

type PageData struct {
	VMName    string
	IsRunning bool
}

var (
	tokensPath  string
	tokenPlugin string
	webRoot     string // Переменная вместо константы
	listenAddr  string
	tokenRegexp = regexp.MustCompile(`^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)`)
	upgrader    = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
)

func main() {
	// Поддержка официальных флагов websockify + наш кастомный --webroot
	flag.StringVar(&tokenPlugin, "token-plugin", "", "Плагин аутентификации (для совместимости)")
	flag.StringVar(&tokensPath, "tokens", "", "Путь к файлу сопоставления токенов (базе портов)")
	flag.StringVar(&webRoot, "webroot", "noVNC-1.7.0", "Путь к директории с файлами noVNC")
	
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s [адрес:порт] --tokens=ПУТЬ --webroot=ПУТЬ\n", os.Args[0])
		flag.PrintDefaults()
	}

	// Отделяем позиционный адрес, если он указан в начале (совместимость с websockify)
	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		listenAddr = os.Args[1]
		os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
	}

	flag.Parse()

	if tokensPath == "" {
		log.Println("ERROR: Не указан обязательный параметр --tokens")
		flag.Usage()
		os.Exit(1)
	}

	// Если адрес не был перехвачен в начале, берем оставшиеся аргументы или дефолт
	if listenAddr == "" {
		args := flag.Args()
		if len(args) > 0 {
			listenAddr = args[0]
		} else {
			listenAddr = "0.0.0.0:8080"
		}
	}

    // Валидация адреса
	host, port, err := net.SplitHostPort(listenAddr)
	if err != nil {
		log.Fatalf("FATAL: Некорректный формат адреса прослушивания '%s': %v (Ожидается формат хост:порт или :порт)", listenAddr, err)
	}

	// Дополнительно проверяем, что хост является валидным IP-адресом (если он указан и это не доменное имя)
	if host != "" && host != "0.0.0.0" && host != "localhost" && net.ParseIP(host) == nil {
		log.Printf("WARNING: Адрес хоста '%s' не является стандартным IP. Убедитесь, что это корректное доменное имя.", host)
	}
	log.Printf("DEBUG: Адрес прослушивания успешно верифицирован. Хост: '%s', Порт: '%s'", host, port)

	// Проверяем физическое наличие папки webroot перед стартом сервера
	if _, err := os.Stat(webRoot); os.IsNotExist(err) {
		log.Fatalf("FATAL: Указанная директория webroot '%s' не существует!", webRoot)
	}

	log.Printf("INFO: Websockify-compatible VNC interface started on %s", listenAddr)
	log.Printf("INFO: Tokens database loaded from: %s", tokensPath)
	log.Printf("INFO: Using noVNC static root from: %s", webRoot)

	http.HandleFunc("/", handleIndex)
	http.HandleFunc("/start", handleStart)
	http.HandleFunc("/ws", handleWebSocketProxy)
	
	// Динамически подставляем переменную webRoot в файловый сервер
	http.Handle("/novnc/", http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot))))

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Web server failed: %v", err)
	}
}

func getTargetAddr(token string) (string, error) {
	file, err := os.Open(tokensPath)
	if err != nil {
		return "", fmt.Errorf("cannot open tokens file: %v", err)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if strings.HasPrefix(line, token+":") {
			matches := tokenRegexp.FindStringSubmatch(line)
			if len(matches) > 3 {
				return fmt.Sprintf("%s:%s", matches[2], matches[3]), nil
			}
		}
	}
	return "", fmt.Errorf("token '%s' not found in database", token)
}

func isVMRunning(name string) bool {
	cmdUser := exec.Command("loginctl", "list-sessions", "--no-legend")
	out, err := cmdUser.Output()
	if err != nil {
		return false
	}
	
	var activeUser string
	lines := strings.Split(string(out), "\n")
	for _, line := range lines {
		if strings.Contains(line, "seat0") {
			fields := strings.Fields(line)
			if len(fields) >= 3 {
				activeUser = fields[2]
			}
		}
	}

	if activeUser == "" || activeUser == "root" {
		return false
	}

	monitorSock := fmt.Sprintf("/tmp/qemu/%s/%s.sock", activeUser, name)
	
	if _, err := os.Stat(monitorSock); os.IsNotExist(err) {
		return false
	}

	conn, err := net.DialTimeout("unix", monitorSock, 150*time.Millisecond)
	if err != nil {
		return false
	}
	conn.Close()
	return true
}

func handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}

	vm := r.URL.Query().Get("vm")
	if vm == "" {
		http.Error(w, "Missing 'vm' parameter. Use /?vm=name", http.StatusBadRequest)
		return
	}

	tmpl, err := template.ParseFiles(templatePath)
	if err != nil {
		log.Printf("ERROR: Failed to load template %s: %v", templatePath, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	data := PageData{
		VMName:    vm,
		IsRunning: isVMRunning(vm),
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_ = tmpl.Execute(w, data)
}

func handleStart(w http.ResponseWriter, r *http.Request) {
	vm := r.URL.Query().Get("vm")
	if vm == "" {
		http.Error(w, "Missing 'vm' parameter", http.StatusBadRequest)
		return
	}

	conn, err := net.Dial("unix", daemonSock)
	if err != nil {
		log.Printf("ERROR: Daemon offline: %v", err)
		http.Error(w, "Daemon offline", http.StatusInternalServerError)
		return
	}
	defer conn.Close()

	_, _ = fmt.Fprintf(conn, "%s\n", vm)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("OK"))
}

func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		token = r.URL.Query().Get("path")
	}

	if token == "" {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	vncTarget, err := getTargetAddr(token)
	if err != nil {
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	reqProtocol := r.Header.Get("Sec-WebSocket-Protocol")
	responseHeader := make(http.Header)
	if strings.Contains(reqProtocol, "binary") {
		responseHeader.Set("Sec-WebSocket-Protocol", "binary")
	}

	wsConn, err := upgrader.Upgrade(w, r, responseHeader)
	if err != nil {
		return
	}
	defer wsConn.Close()

	tcpConn, err := net.DialTimeout("tcp", vncTarget, 2*time.Second)
	if err != nil {
		return
	}
	defer tcpConn.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go func() {
		defer cancel()
		for {
			msgType, reader, err := wsConn.NextReader()
			if err != nil {
				return
			}
			if msgType == websocket.BinaryMessage {
				_, _ = io.Copy(tcpConn, reader)
			}
		}
	}()

	buf := make([]byte, 65536)
	for {
		select {
		case <-ctx.Done():
			return
		default:
			n, err := tcpConn.Read(buf)
			if err != nil {
				return
			}
			if n > 0 {
				_ = wsConn.WriteMessage(websocket.BinaryMessage, buf[:n])
			}
		}
	}
}
