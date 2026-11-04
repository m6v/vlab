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

type VMInfo struct {
	Name      string
	IsRunning bool
}

var (
	tokensPath  string
	webRoot     string
	listenAddr  string
	tokenRegexp = regexp.MustCompile(`^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)`)
	upgrader    = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
)

func main() {
	flag.StringVar(&tokensPath, "tokens", "", "Путь к файлу сопоставления токенов (базе портов)")
	flag.StringVar(&webRoot, "webroot", "noVNC-1.7.0", "Путь к директории с файлами noVNC")
	
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s [адрес:порт] --tokens=ПУТЬ --webroot=ПУТЬ\n", os.Args)
		flag.PrintDefaults()
	}

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

	if listenAddr == "" {
		listenAddr = "0.0.0.0:8080"
	}

	// Валидация корректности формата адреса
	if _, _, err := net.SplitHostPort(listenAddr); err != nil {
		log.Fatalf("FATAL: Некорректный формат адреса прослушивания '%s': %v", listenAddr, err)
	}

	if _, err := os.Stat(webRoot); os.IsNotExist(err) {
		log.Fatalf("FATAL: Директория webroot '%s' не существует!", webRoot)
	}

	log.Printf("INFO: VNC interface started on %s with tokens %s", listenAddr, tokensPath)

	http.HandleFunc("/start", handleStart)
	http.HandleFunc("/ws", handleWebSocketProxy)
	http.Handle("/novnc/", http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot))))
	http.HandleFunc("/", handleIndex)

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Web server failed: %v", err)
	}
}

func handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/favicon.ico" {
		var vmName string
		if referer := r.Header.Get("Referer"); referer != "" {
			if idx := strings.Index(referer, "vm="); idx != -1 {
				rawVM := referer[idx+3:]
				if endIdx := strings.Index(rawVM, "&"); endIdx != -1 {
					vmName = rawVM[:endIdx]
				} else {
					vmName = rawVM
				}
			}
		}
		vmName = strings.TrimSpace(vmName)

		iconsDir := "/usr/share/pixmaps"
		iconPath := fmt.Sprintf("%s/%s.png", iconsDir, vmName)

		if vmName == "" || isNotExist(iconPath) {
			iconPath = fmt.Sprintf("%s/default.png", iconsDir)
		}

		if isNotExist(iconPath) {
			http.NotFound(w, r)
			return
		}

		w.Header().Set("Content-Type", "image/png")
		w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
		w.Header().Set("Pragma", "no-cache")
		w.Header().Set("Expires", "0")

		http.ServeFile(w, r, iconPath)
		return
	}

	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}

	vm := r.URL.Query().Get("vm")
	if vm == "" {
		http.Error(w, "Missing 'vm' parameter. Use /?vm=name", http.StatusBadRequest)
		return
	}

	// Проверка наличия имени машины в базе tokens.conf
	if _, err := getTargetAddr(vm); err != nil {
		log.Printf("WARNING: Попытка доступа к незарегистрированной ВМ '%s'", vm)
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(fmt.Sprintf("Ошибка: Виртуальная машина '%s' не зарегистрирована на этом сервере.", vm)))
		return
	}

	// Отписовка экрана ВМ
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

func isNotExist(path string) bool {
	_, err := os.Stat(path)
	return os.IsNotExist(err)
}


func getTargetAddr(token string) (string, error) {
	file, err := os.Open(tokensPath)
	if err != nil {
		return "", fmt.Errorf("cannot open tokens file: %v", err)
	}
	defer file.Close()

	// Поиск точного совпадения ключа с двоеточием
	targetPrefix := token + ":"

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		if strings.HasPrefix(line, targetPrefix) {
			matches := tokenRegexp.FindStringSubmatch(line)
			if len(matches) > 3 {
				return fmt.Sprintf("%s:%s", matches[2], matches[3]), nil
			}
			return "", fmt.Errorf("token '%s' has invalid format in config", token)
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
