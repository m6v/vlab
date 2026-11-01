package main

import (
	"bufio"
	"context"
	"flag"
	"fmt"
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

const bufferSize = 65536

var tokensPath string

func main() {
	// Справка в строгом стиле websockify
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s --token-source=ФАЙЛ [АДРЕС:]ПОРТ\n\nOptions:\n", os.Args)
		flag.PrintDefaults()
	}

	// УСТАНОВЛЕН ДЕФОЛТНЫЙ ПУТЬ: /usr/share/spice-html5 подставляется автоматически
	webRoot := flag.String("web", "/usr/share/spice-html5", "Путь к паристому каталогу веб-интерфейса (web root)")
	flag.StringVar(&tokensPath, "token-source", "", "Путь к файлу сопоставления токенов (базе портов)")
	flag.Parse()

	if tokensPath == "" {
		log.Println("ERROR: Не указан обязательный параметр --token-source")
		flag.Usage()
		os.Exit(1)
	}

	args := flag.Args()
	if len(args) < 1 {
		log.Println("ERROR: Не указан сетевой порт для прослушивания")
		flag.Usage()
		os.Exit(1)
	}

	listenAddr := args[0]
	if !strings.Contains(listenAddr, ":") {
		listenAddr = ":" + listenAddr
	}

	log.Printf("INFO: Pure Go websockify alternative starting on %s", listenAddr)
	log.Printf("INFO: Website root directory: %s", *webRoot)
	log.Printf("INFO: Tokens database loaded from: %s", tokensPath)

	// Статический веб-сервер раздает файлы из папки
	http.Handle("/", http.FileServer(http.Dir(*webRoot)))
	
	// Точка входа для бинарного WebSocket-пайпа
	http.HandleFunc("/ws", handleWebSocketProxy)

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Server crashed: %v", err)
	}
}

// Извлекатель адреса из файла токенов
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

// Бинарный мост WebSocket <-> TCP Network Stream
func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		token = r.URL.Query().Get("path")
	}

	log.Printf("DEBUG: Incoming WebSocket connection for token: '%s'", token)

	targetAddr, err := getTargetAddr(token)
	if err != nil {
		log.Printf("ERROR: Authentication failed: %v", err)
		http.Error(w, "Unauthorized token", http.StatusUnauthorized)
		return
	}

	wsConn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("ERROR: WebSocket upgrade failed: %v", err)
		return
	}
	defer wsConn.Close()

	tcpConn, err := net.DialTimeout("tcp", targetAddr, 2*time.Second)
	if err != nil {
		log.Printf("ERROR: Target net stream %s is unreachable: %v", targetAddr, err)
		wsConn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1011, "Target unreachable"))
		return
	}
	defer tcpConn.Close()

	log.Printf("SUCCESS: Active bridge connected: WebClient <-> %s", targetAddr)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Поток А: Браузер -> Целевой TCP-порт
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

	// Поток Б: Целевой TCP-порт -> Браузер
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
