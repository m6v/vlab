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
	tokenRegexp = regexp.MustCompile(`^([a-zA-Z0-9_-]+):\s*([a-zA-Z0-9_.-]+):(\d+)`) // Изменено: поддерживает и IP, и домены/localhost
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

	// Подключение к VNC-серверу QEMU
	tcpConn, err := net.DialTimeout("tcp", vncTarget, 5*time.Second)
	if err != nil {
		log.Printf("[ERROR] [%s] Не удалось подключиться к VNC-серверу QEMU (%s): %v", token, vncTarget, err)
		_ = wsConn.Close()
		return
	}
	log.Printf("[INFO] [%s] Успешное TCP-подключение к VNC %s", token, vncTarget)

	// Структура для безопасного атомарного закрытия ресурсов из разных горутин
	var closeOnce sync.Once
	closeConnections := func() {
		_ = tcpConn.Close()
		_ = wsConn.Close()
		log.Printf("[INFO] [%s] Все соединения (WS и TCP) успешно заблокированы и закрыты", token)
	}
	defer closeOnce.Do(closeConnections)

	// Настройка таймаутов для WebSocket, чтобы noVNC и прокси не теряли друг друга
	_ = wsConn.SetReadDeadline(time.Now().Add(60 * time.Second))
	wsConn.SetPongHandler(func(string) error {
		_ = wsConn.SetReadDeadline(time.Now().Add(60 * time.Second))
		return nil
	})

	// Мьютекс для защиты от конкурентной записи в WebSocket
	var wsMutex sync.Mutex

	// Горутина чтения из браузера (WS) и записи в виртуальную машину (TCP)
	go func() {
		defer closeOnce.Do(closeConnections)
		for {
			msgType, reader, err := wsConn.NextReader()
			if err != nil {
				log.Printf("[INFO] [%s] WebSocket закрыт со стороны браузера или по таймауту: %v", token, err)
				return
			}

			// Продление жизни WS-соединения при активности пользователя
			_ = wsConn.SetReadDeadline(time.Now().Add(60 * time.Second))

			if msgType == websocket.BinaryMessage {
				_, err = io.Copy(tcpConn, reader)
				if err != nil {
					log.Printf("[WARNING] [%s] Ошибка записи данных в TCP-порт VNC: %v", token, err)
					return
				}
			}
		}
	}()

	// Горутина чтения из Виртуальной машины (TCP) и записи в браузер (WS)
	buf := make([]byte, 65536)
	for {
		n, err := tcpConn.Read(buf)
		if err != nil {
			if err == io.EOF {
				log.Printf("[INFO] [%s] VNC-сервер QEMU закрыл соединение", token)
			} else {
				log.Printf("[ERROR] [%s] Сбой при чтении из TCP-порта VNC: %v", token, err)
			}
			return
		}

		if n > 0 {
			wsMutex.Lock()
			err = wsConn.WriteMessage(websocket.BinaryMessage, buf[:n])
			wsMutex.Unlock()
			if err != nil {
				log.Printf("[WARNING] [%s] Не удалось отправить VNC-кадр в WebSocket: %v", token, err)
				return
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
			if matches[1] == token {
				return fmt.Sprintf("%s:%s", matches[2], matches[3]), nil
			}
		}
	}
	return "", fmt.Errorf("token '%s' not found in config", token)
}
