package main

import (
	"bufio"
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

	flag.StringVar(&tokensPath, "tokens", "", "Path to the tokens mapping configuration file")
	flag.StringVar(&webRoot, "webroot", "/usr/share/novnc", "Path to noVNC static web assets root directory")

	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Usage: %s [listen_address:port] --tokens=PATH --webroot=PATH\n", os.Args[0])
		flag.PrintDefaults()
	}

	if len(os.Args) > 1 && !strings.HasPrefix(os.Args[1], "-") {
		listenAddr = os.Args[1]
		os.Args = append([]string{os.Args[0]}, os.Args[2:]...)
	}

	flag.Parse()

	if tokensPath == "" {
		log.Println("[ERROR] Missing required parameter: --tokens")
		flag.Usage()
		os.Exit(1)
	}

	if listenAddr == "" {
		listenAddr = defaultListen
	}

	if _, _, err := net.SplitHostPort(listenAddr); err != nil {
		log.Fatalf("[FATAL] Invalid listen address format '%s': %v", listenAddr, err)
	}

	if _, err := os.Stat(webRoot); os.IsNotExist(err) {
		log.Fatalf("[FATAL] Specified webroot directory '%s' does not exist", webRoot)
	}

	log.Printf("[INFO] VNC to WebSocket bridge initialized on %s", listenAddr)
	log.Printf("[INFO] Token mapping configuration source: %s", tokensPath)
	log.Printf("[INFO] Serving static web assets from %s", webRoot)

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
		log.Fatalf("[FATAL] HTTP server runtime error: %v", err)
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
		log.Printf("[WARNING] Route targeting failed for token '%s': %v", token, err)
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
		log.Printf("[ERROR] WebSocket protocol upgrade failed: %v", err)
		return
	}

	tcpConn, err := net.DialTimeout("tcp", vncTarget, 5*time.Second)
	if err != nil {
		log.Printf("[ERROR] [%s] TCP connection establishment to backend VNC server '%s' failed: %v", token, vncTarget, err)
		_ = wsConn.Close()
		return
	}
	log.Printf("[INFO] [%s] Established TCP connection link to VNC backend target '%s'", token, vncTarget)

	var closeOnce sync.Once
	closeConnections := func() {
		_ = tcpConn.Close()
		_ = wsConn.Close()
		log.Printf("[INFO] [%s] Closed all transport channels (WebSocket and TCP connection pools)", token)
	}
	defer closeOnce.Do(closeConnections)

	_ = wsConn.SetReadDeadline(time.Now().Add(60 * time.Second))
	wsConn.SetPongHandler(func(string) error {
		_ = wsConn.SetReadDeadline(time.Now().Add(60 * time.Second))
		return nil
	})

	var wsMutex sync.Mutex

	// Inbound pipeline: Browser (WS) -> Virtual Machine (TCP)
	go func() {
		defer closeOnce.Do(closeConnections)
		for {
			msgType, reader, err := wsConn.NextReader()
			if err != nil {
				log.Printf("[INFO] [%s] Inbound WebSocket channel closed: %v", token, err)
				return
			}
			_ = wsConn.SetReadDeadline(time.Now().Add(60 * time.Second))

			if msgType == websocket.BinaryMessage {
				_, err = io.Copy(tcpConn, reader)
				if err != nil {
					log.Printf("[WARNING] [%s] Failed to write inbound data frame payload to TCP socket: %v", token, err)
					return
				}
			}
		}
	}()

	// Outbound pipeline: Virtual Machine (TCP) -> Browser (WS)
	buf := make([]byte, 65536)
	for {
		n, err := tcpConn.Read(buf)
		if err != nil {
			if err == io.EOF {
				log.Printf("[INFO] [%s] Backend VNC target issued orderly connection shutdown (EOF)", token)
			} else {
				log.Printf("[ERROR] [%s] Outbound TCP socket data stream read fault: %v", token, err)
			}
			return
		}

		if n > 0 {
			wsMutex.Lock()
			err = wsConn.WriteMessage(websocket.BinaryMessage, buf[:n])
			wsMutex.Unlock()
			if err != nil {
				log.Printf("[WARNING] [%s] Failed to transmit frame payload to WebSocket client connection: %v", token, err)
				return
			}
		}
	}
}

func getTargetAddr(token string) (string, error) {
	file, err := os.Open(tokensPath)
	if err != nil {
		return "", fmt.Errorf("failed to open configuration source file: %v", err)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		if matches := tokenRegexp.FindStringSubmatch(line); len(matches) > 3 {
			if matches[1] == token {
				return fmt.Sprintf("%s:%s", matches[2], matches[3]), nil
			}
		}
	}
	return "", fmt.Errorf("specified metadata token identifier was not matched")
}
