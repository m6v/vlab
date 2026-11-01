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
	"time"
    "github.com/gorilla/websocket"
)

var (
	upgrader    = websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}
	tokenRegexp = regexp.MustCompile(`^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)`)
)

const (
	bufferSize  = 65536
	logFileName = "spice-bridge.log"
	daemonSock  = "/tmp/vmctrl.sock"
)

var tokensPath string

func main() {
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s --token-source=ФАЙЛ [АДРЕС:]ПОРТ\n\nOptions:\n", os.Args)
		flag.PrintDefaults()
	}

	webRoot := flag.String("web", "/usr/share/spice-html5", "Путь к папке веб-интерфейса (web root)")
	flag.StringVar(&tokensPath, "token-source", "", "Путь к файлу сопоставления токенов (базе портов)")
	flag.Parse()

	if tokensPath == "" {
		log.Fatal("ERROR: Не указан обязательный параметр --token-source")
	}

	args := flag.Args()
	if len(args) < 1 {
		log.Fatal("ERROR: Не указан сетевой порт для прослушивания")
	}

	listenAddr := args[0]
	if !strings.Contains(listenAddr, ":") {
		listenAddr = ":" + listenAddr
	}

	var logPath string
	if os.Getuid() == 0 {
		logPath = "/var/log/" + logFileName
	} else {
		homeDir, err := os.UserHomeDir()
		if err != nil {
			logPath = logFileName
		} else {
			logPath = homeDir + "/" + logFileName
		}
	}

	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		log.Printf("WARNING: Logging to stdout only: %v", err)
	} else {
		defer logFile.Close()
		log.SetOutput(io.MultiWriter(os.Stdout, logFile))
	}

	log.Printf("INFO: Pure Go websockify alternative starting on %s", listenAddr)
	log.Printf("INFO: Website root directory: %s", *webRoot)
	log.Printf("INFO: Tokens database loaded from: %s", tokensPath)

	http.Handle("/", http.FileServer(http.Dir(*webRoot)))
	http.HandleFunc("/ws", handleWebSocketProxy)

	http.HandleFunc("/start", func(w http.ResponseWriter, r *http.Request) {
		vmName := r.URL.Query().Get("vm")
		if vmName == "" {
			http.Error(w, "Missing VM name", http.StatusBadRequest)
			return
		}

		conn, err := net.Dial("unix", daemonSock)
		if err != nil {
			log.Printf("ERROR: Background vmctrl-daemon is offline: %v", err)
			http.Error(w, "Управляющий демон недоступен", http.StatusInternalServerError)
			return
		}
		defer conn.Close()

		fmt.Fprintf(conn, "%s\n", vmName)
		w.Write([]byte("OK"))
	})

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Server crashed: %v", err)
	}
}

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

func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		token = r.URL.Query().Get("path")
	}

	log.Printf("DEBUG: Incoming WebSocket connection for token: '%s'", token)

	if token == "" {
		log.Printf("ERROR: Authentication failed. Token parameters missing.")
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	targetAddr, err := getTargetAddr(token)
	if err != nil {
		log.Printf("ERROR: Authentication failed for token '%s': %v", token, err)
		w.WriteHeader(http.StatusUnauthorized)
		return
	}

	wsConn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer wsConn.Close()

	tcpConn, err := net.DialTimeout("tcp", targetAddr, 1*time.Second)
	if err != nil {
		log.Printf("ERROR: Target stream %s unreachable: %v", targetAddr, err)
		wsConn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1011, "Target unreachable"))
		return
	}
	defer tcpConn.Close()

	log.Printf("SUCCESS: Active bridge connected: WebClient <-> %s", targetAddr)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go func() {
		defer cancel()
		for {
			select {
			case <-ctx.Done():
				return
			default:
				msgType, data, err := wsConn.ReadMessage()
				if err != nil {
					return
				}
				if msgType == websocket.BinaryMessage {
					if _, err = tcpConn.Write(data); err != nil {
						return
					}
				}
			}
		}
	}()

	buffer := make([]byte, bufferSize)
	for {
		select {
		case <-ctx.Done():
			return
		default:
			n, err := tcpConn.Read(buffer)
			if err != nil {
				return
			}
			if n > 0 {
				if err = wsConn.WriteMessage(websocket.BinaryMessage, buffer[:n]); err != nil {
					return
				}
			}
		}
	}
}
