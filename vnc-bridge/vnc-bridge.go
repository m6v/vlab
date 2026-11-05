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
	defaultListen := fmt.Sprintf("%s:%s", "0.0.0.0", "8080")

	flag.StringVar(&tokensPath, "tokens", "", "Путь к файлу сопоставления токенов (базе портов VNC)")
	flag.StringVar(&webRoot, "webroot", "/usr/share/novnc", "Путь к директории со статикой noVNC")
	
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s [адрес:порт] --tokens=ПУТЬ --webroot=ПУТЬ\n", os.Args)
		flag.PrintDefaults()
	}

	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		listenAddr = os.Args[1]
		// Сдвиг среза аргументов для flag.Parse
		os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
	}

	flag.Parse()

	if tokensPath == "" {
		log.Println("[ERROR] Не указан обязательный параметр --tokens")
		flag.Usage()
		os.Exit(1)
	}

	if listenAddr == "" {
		listenAddr = defaultListen
	}

	if _, _, err := net.SplitHostPort(listenAddr); err != nil {
		log.Fatalf("[FATAL] Некорректный формат адреса прослушивания '%s': %v", listenAddr, err)
	}

	if _, err := os.Stat(webRoot); os.IsNotExist(err) {
		log.Fatalf("[FATAL] Директория webroot '%s' не существует!", webRoot)
	}

	log.Printf("[INFO] VNC to WebSocket bridge started on %s", listenAddr)
	log.Printf("[INFO] Tokens mapping file %s", tokensPath)
	log.Printf("[INFO] Serving noVNC static from %s", webRoot)

	mux := http.NewServeMux()
	mux.HandleFunc("/ws", handleWebSocketProxy)

	fileServer := http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot)))
	mux.HandleFunc("/novnc/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		
		if r.Method == "OPTIONS" {
			w.WriteHeader(http.StatusOK)
			return
		}
		fileServer.ServeHTTP(w, r)
	})

	server := &http.Server{
		Addr:         listenAddr,
		Handler:      mux,
		ReadTimeout:  15 * time.Minute,
		WriteTimeout: 60 * time.Minute,
		IdleTimeout:  120 * time.Minute,
	}

	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("[FATAL] Web server failed: %v", err)
	}
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
		log.Printf("[WARNING] Направление для токена '%s' не найдено: %v", token, err)
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
		log.Printf("[ERROR] WebSocket upgrade failed: %v", err)
		return
	}
	
	ctx, cancel := context.WithCancel(context.Background())
	
	var closeOnce sync.Once
	closeConnections := func() {
		cancel()
		_ = wsConn.Close()
	}
	defer closeOnce.Do(closeConnections)

	tcpConn, err := net.DialTimeout("tcp", vncTarget, 2*time.Second)
	if err != nil {
		log.Printf("[ERROR] Коннект к VNC сорвался: %v", err)
		return
	}
	defer tcpConn.Close()

	var wsMutex sync.Mutex

	go func() {
		defer closeOnce.Do(closeConnections)
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
			_ = tcpConn.SetReadDeadline(time.Now().Add(10 * time.Second))
			n, err := tcpConn.Read(buf)
			
			if err != nil {
				if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
					continue
				}
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

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		if matches := tokenRegexp.FindStringSubmatch(line); len(matches) > 3 {
			// Сравнение строки token со строкой matches[1] (первая группа захвата регулярного выражения)
			if matches[1] == token {
				// Сбор результирующего адреса из группы IP (matches[2]) и группы порта (matches[3])
				return fmt.Sprintf("%s:%s", matches[2], matches[3]), nil
			}
		}
	}
	return "", fmt.Errorf("token '%s' not found in config", token)
}
