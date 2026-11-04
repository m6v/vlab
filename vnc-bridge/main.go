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
	"regexp"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

const (
	webRoot      = "noVNC-1.7.0"
	daemonSock   = "/run/vmctrl/vmctrl.sock"
	templatePath = "index.html"
)

type PageData struct {
	VMName    string
	IsRunning bool
}

var (
	tokensPath  string
	tokenPlugin string
	listenAddr  string
	tokenRegexp = regexp.MustCompile(`^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)`)
	upgrader    = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
)

func main() {
	// Поддержка официальных флагов websockify
	flag.StringVar(&tokenPlugin, "token-plugin", "", "Плагин аутентификации (для совместимости используйте TokenFile)")
	flag.StringVar(&tokensPath, "tokens", "", "Путь к файлу сопоставления токенов (базе портов)")
	
	// Переопределяем стандартный Usage, чтобы он совпадал с websockify
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s [адрес:порт] --token-plugin=TokenFile --tokens=ПУТЬ_К_ФАЙЛУ\n", os.Args[0])
		flag.PrintDefaults()
	}
	flag.Parse()

	// Проверяем обязательный флаг базы токенов
	if tokensPath == "" {
		log.Println("ERROR: Не указан обязательный параметр --tokens")
		flag.Usage()
		os.Exit(1)
	}

	// Извлекаем позиционный аргумент адреса и порта (например, 0.0.0.0:8080)
	args := flag.Args()
	if len(args) > 0 {
		listenAddr = args[0]
	} else {
		// Дефолтное значение, если адрес не указан позиционно
		listenAddr = "0.0.0.0:8080"
	}

	log.Printf("INFO: Websockify-compatible VNC interface started on %s", listenAddr)
	log.Printf("INFO: Tokens database loaded from: %s", tokensPath)
	if tokenPlugin != "" {
		log.Printf("INFO: Active token plugin: %s", tokenPlugin)
	}

	http.HandleFunc("/", handleIndex)
	http.HandleFunc("/start", handleStart)
	http.HandleFunc("/ws", handleWebSocketProxy)
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
	monitorSock := fmt.Sprintf("/run/vmctrl/%s.sock", name)
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
	if err := tmpl.Execute(w, data); err != nil {
		log.Printf("ERROR: Failed to execute template: %v", err)
	}
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
		log.Printf("ERROR: Auth failed for token %s: %v", token, err)
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
				if _, err := io.Copy(tcpConn, reader); err != nil {
					return
				}
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
				if err := wsConn.WriteMessage(websocket.BinaryMessage, buf[:n]); err != nil {
					return
				}
			}
		}
	}
}
