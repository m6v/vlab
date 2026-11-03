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
	listenAddr   = ":8080"
	webRoot      = "noVNC-1.7.0"
	daemonSock   = "/tmp/vmctrl.sock"
	templatePath = "index.html"
)

// Структура для передачи состояния конкретной ВМ в HTML-шаблон
type PageData struct {
	VMName    string
	IsRunning bool
}

var (
	tokensPath  string
	tokenRegexp = regexp.MustCompile(`^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)`)
	upgrader    = websocket.Upgrader{
		CheckOrigin: func(r *http.Request) bool { return true },
	}
)

func main() {
	flag.StringVar(&tokensPath, "token-source", "", "Путь к файлу сопоставления токенов (базе портов)")
	flag.Parse()

	if tokensPath == "" {
		log.Fatal("ERROR: Не указан обязательный параметр --token-source")
	}

	log.Printf("INFO: Dynamic VNC interface started on %s", listenAddr)
	log.Printf("INFO: Tokens database loaded from: %s", tokensPath)

	http.HandleFunc("/", handleIndex)
	http.HandleFunc("/start", handleStart)
	http.HandleFunc("/ws", handleWebSocketProxy)
	http.Handle("/novnc/", http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot))))

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Web server failed: %v", err)
	}
}

// Динамический поиск целевого VNC-адреса по токену в файле websockify
func getTargetAddr(token string) (string, error) {
	file, err := os.Open(tokensPath)
	if err != nil {
		return "", fmt.Errorf("cannot open token source file: %v", err)
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

// Проверка работы конкретной ВМ по её имени через стандартный префикс сокета QEMU
func isVMRunning(name string) bool {
	monitorSock := fmt.Sprintf("/tmp/%s.sock", name)
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

	// Получаем имя ВМ из строки запроса (?vm=имя_машины)
	vm := r.URL.Query().Get("vm")
	if vm == "" {
		http.Error(w, "Missing 'vm' parameter in URL. Use /?vm=your-vm-name", http.StatusBadRequest)
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
		log.Printf("WARNING: Start request rejected: missing 'vm' parameter")
		http.Error(w, "Missing 'vm' parameter", http.StatusBadRequest)
		return
	}

	log.Printf("INFO: Received web boot signal for VM: %s", vm)

	// Подключаемся к системному демону
	conn, err := net.Dial("unix", daemonSock)
	if err != nil {
		log.Printf("ERROR: Cannot connect to system daemon at %s: %v (Is vmctrl-daemon running?)", daemonSock, err)
		http.Error(w, "System daemon offline", http.StatusInternalServerError)
		return
	}
	defer conn.Close()

	// Отправляем имя машины с символом переноса строки, так как демон использует Scanner.Scan()
	_, err = fmt.Fprintf(conn, "%s\n", vm)
	if err != nil {
		log.Printf("ERROR: Failed to write VM name to daemon socket: %v", err)
		http.Error(w, "Write error", http.StatusInternalServerError)
		return
	}

	log.Printf("SUCCESS: Boot command for %s successfully forwarded to system daemon", vm)
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("OK"))
}

func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	// Совместимость с websockify: извлекаем токен из параметров 'token' или 'path'
	token := r.URL.Query().Get("token")
	if token == "" {
		token = r.URL.Query().Get("path")
	}

	if token == "" {
		log.Printf("WARNING: Rejecting WebSocket request: token missing")
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	log.Printf("INFO: Incoming connection request for token: %s", token)

	// Ищем адрес VNC в базе тонеков websockify
	vncTarget, err := getTargetAddr(token)
	if err != nil {
		log.Printf("ERROR: Authentication failed for token %s: %v", token, err)
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
		log.Printf("ERROR: WebSocket upgrade failed: %v", err)
		return
	}
	defer wsConn.Close()

	log.Printf("INFO: WebSocket upgraded! Subprotocol: %s", wsConn.Subprotocol())
	log.Printf("INFO: Dialing QEMU VNC at %s...", vncTarget)

	tcpConn, err := net.DialTimeout("tcp", vncTarget, 2*time.Second)
	if err != nil {
		log.Printf("ERROR: Dial QEMU VNC %s failed: %v", vncTarget, err)
		return
	}
	defer tcpConn.Close()

	log.Printf("SUCCESS: Bridge established with QEMU VNC %s!", vncTarget)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Поток 1: Браузер -> QEMU
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

	// Поток 2: QEMU -> Браузер
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
