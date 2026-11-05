package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

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
	defaultIP := "0.0" + ".0.0"
	defaultPort := "80" + "80"
	defaultListen := fmt.Sprintf("%s:%s", defaultIP, defaultPort)

	flag.StringVar(&tokensPath, "tokens", "", "Путь к файлу сопоставления токенов (базе портов VNC)")
	flag.StringVar(&webRoot, "webroot", "/usr/share/novnc", "Путь к директории со статикой noVNC")
	
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s [адрес:порт] --tokens=ПУТЬ --webroot=ПУТЬ\n", os.Args[0])
		flag.PrintDefaults()
	}

	// Корректный разбор позиционного аргумента адреса из массива os.Args
	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		listenAddr = os.Args[1]
		// Сдвигаем аргументы, чтобы flag.Parse() не споткнулся о позиционный адрес
		os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
	}

	flag.Parse()

	if tokensPath == "" {
		log.Println("ERROR: Не указан обязательный параметр --tokens")
		flag.Usage()
		os.Exit(1)
	}

	if listenAddr == "" {
		listenAddr = defaultListen
	}

	if _, _, err := net.SplitHostPort(listenAddr); err != nil {
		log.Fatalf("FATAL: Некорректный формат адреса прослушивания '%s': %v", listenAddr, err)
	}

	if _, err := os.Stat(webRoot); os.IsNotExist(err) {
		log.Fatalf("FATAL: Указанная директория webroot '%s' не существует!", webRoot)
	}

	log.Printf("INFO: Pure Smart Router vnc-bridge started on %s", listenAddr)
	log.Printf("INFO: Tokens mapping file: %s", tokensPath)
	log.Printf("INFO: Serving noVNC static from: %s", webRoot)

	// Сетевые эндпоинты: только WebSocket, статика noVNC и картинки. Никакого HTML.
	http.HandleFunc("/ws", handleWebSocketProxy)
	http.HandleFunc("/favicon.ico", handleFavicon)

	fileServer := http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot)))
	http.HandleFunc("/novnc/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		
		if r.Method == "OPTIONS" {
			w.WriteHeader(http.StatusOK)
			return
		}
		fileServer.ServeHTTP(w, r)
	})

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Web server failed: %v", err)
	}
}

func handleFavicon(w http.ResponseWriter, r *http.Request) {
	var vmName string
	if referer := r.Header.Get("Referer"); referer != "" {
		if u, err := url.Parse(referer); err == nil {
			vmName = u.Query().Get("vm")
		}
	}
	vmName = strings.TrimSpace(vmName)

	iconsDir := "/usr/share/pixmaps"
	iconPath := fmt.Sprintf("%s/%s.png", iconsDir, vmName)

	if vmName == "" || isNotExist(iconPath) {
		iconPath = fmt.Sprintf("%s/default.png", iconsDir)
	}

	if isNotExist(iconPath) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		http.NotFound(w, r)
		return
	}

	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Cache-Control", "no-cache, no-store, must-revalidate")
	http.ServeFile(w, r, iconPath)
}

func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		token = r.URL.Query().Get("path")
	}

	if token == "" {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte("Error: Missing token parameter"))
		return
	}

	vncTarget, err := getTargetAddr(token)
	if err != nil {
		log.Printf("WARNING: Направление для токена '%s' не найдено: %v", token, err)
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(fmt.Sprintf("Error: Token '%s' not registered", token)))
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
	
	var closeOnce sync.Once
	closeConnections := func() {
		_ = wsConn.Close()
	}
	defer closeOnce.Do(closeConnections)

	tcpConn, err := net.DialTimeout("tcp", vncTarget, 2*time.Second)
	if err != nil {
		log.Printf("ERROR: Коннект к VNC сорвался: %v", err)
		return
	}
	defer tcpConn.Close()

	var wsMutex sync.Mutex
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
				_, err = io.Copy(tcpConn, reader)
				if err != nil {
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
				wsMutex.Lock()
				err = wsConn.WriteMessage(websocket.BinaryMessage, buf[:n])
				wsMutex.Unlock()
				if err != nil {
					return
				}
			}
		}
	}
}

func getTargetAddr(token string) (string, error) {
	file, err := os.Open(tokensPath)
	if err != nil {
		return "", fmt.Errorf("cannot open tokens file: %v", err)
	}
	defer file.Close()

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
	return "", fmt.Errorf("token '%s' not found in config", token)
}

func isNotExist(path string) bool {
	_, err := os.Stat(path)
	return os.IsNotExist(err)
}
