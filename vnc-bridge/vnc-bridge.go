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
	// Инициализация флагов (чистый websockify синтаксис)
	flag.StringVar(&tokensPath, "tokens", "", "Путь к файлу сопоставления токенов (базе портов VNC)")
	flag.StringVar(&webRoot, "webroot", "/usr/share/novnc", "Путь к директории со статикой noVNC")
	
	flag.Usage = func() {
		fmt.Fprintf(os.Stderr, "Использование: %s [адрес:порт] --tokens=ПУТЬ --webroot=ПУТЬ\n", os.Args)
		flag.PrintDefaults()
	}

	// Выделение позиционного адреса (если указан в начале строки аргументов)
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

	// Валидация сетевого адреса
	if _, _, err := net.SplitHostPort(listenAddr); err != nil {
		log.Fatalf("FATAL: Некорректный формат адреса прослушивания '%s': %v", listenAddr, err)
	}

	// Проверка наличия статики веб-интерфейса
	if _, err := os.Stat(webRoot); os.IsNotExist(err) {
		log.Fatalf("FATAL: Указанная директория webroot '%s' не существует!", webRoot)
	}

	log.Printf("INFO: Pure Smart Router vnc-bridge started on %s", listenAddr)
	log.Printf("INFO: Tokens mapping file: %s", tokensPath)
	log.Printf("INFO: Serving noVNC static from: %s", webRoot)

	// Сетевые маршруты шлюза
	http.HandleFunc("/ws", handleWebSocketProxy)
	http.Handle("/novnc/", http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot))))

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Web server failed: %v", err)
	}
}

func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	// Извлекаем токен (поддерживаем оба формата передачи websockify: ?token= или ?path=)
	token := r.URL.Query().Get("token")
	if token == "" {
		token = r.URL.Query().Get("path")
	}

	if token == "" {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte("Error: Missing token parameter"))
		return
	}

	// Ищем целевой хост:порт в базе токенов
	vncTarget, err := getTargetAddr(token)
	if err != nil {
		log.Printf("WARNING: Направление для токена '%s' не найдено: %v", token, err)
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(fmt.Sprintf("Error: Token '%s' not registered", token)))
		return
	}

	// КРИТИЧЕСКИЙ УЗЕЛ: Согласование субпротокола noVNC (Sec-WebSocket-Protocol)
	reqProtocol := r.Header.Get("Sec-WebSocket-Protocol")
	responseHeader := make(http.Header)
	if strings.Contains(reqProtocol, "binary") {
		responseHeader.Set("Sec-WebSocket-Protocol", "binary")
	}

	// Апгрейдим HTTP соединение до WebSocket
	wsConn, err := upgrader.Upgrade(w, r, responseHeader)
	if err != nil {
		log.Printf("ERROR: WebSocket upgrade failed: %v", err)
		return
	}
	
	// Каскадное закрытие сокетов для предотвращения Memory Leak
	var closeOnce sync.Once
	closeConnections := func() {
		_ = wsConn.Close()
	}
	defer closeOnce.Do(closeConnections)

	// Устанавливаем чистое синхронное TCP-соединение с VNC-сервером
	tcpConn, err := net.DialTimeout("tcp", vncTarget, 2*time.Second)
	if err != nil {
		log.Printf("ERROR: Коннект к VNC %s для токена '%s' сорвался: %v", vncTarget, token, err)
		return
	}
	defer tcpConn.Close()

	var wsMutex sync.Mutex
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Поток А (Горутина): Браузер (WebSocket) -> VNC Server (TCP)
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

	// Поток Б (Основной): VNC Server (TCP) -> Браузер (WebSocket)
	// Оптимизация: Аллокация буфера вынесена ЗА пределы бесконечного цикла
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
				// Защита от конкурентной одновременной записи в WebSocket
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
				// Возвращаем строго распарсенный хост:порт (например, 127.0.0.1:5913)
				return fmt.Sprintf("%s:%s", matches[2], matches[3]), nil
			}
			return "", fmt.Errorf("token '%s' has invalid format in config", token)
		}
	}

	return "", fmt.Errorf("token '%s' not found in config", token)
}
