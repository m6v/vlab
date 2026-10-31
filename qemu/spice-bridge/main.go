package main

import (
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

var (
    upgrader   = websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}
    portRegexp = regexp.MustCompile(`port=(59\d+)`) 
)

const (
    bufferSize  = 65536
    vmctrlPath  = "/usr/local/bin/vmctrl"
    logFileName = "qemu-gateway.log"
)

func main() {
    listenAddr := flag.String("listen", ":8089", "Адрес и порт сервера")
    webRoot := flag.String("web", "/usr/share/spice-html5", "Путь к файлам SPICE-HTML5")
    flag.Parse()

    // Автоматический выбор каталога логов на основе прав пользователя
    var logPath string
    if os.Getuid() == 0 {
	// Если запуск от root (systemd служба) — пишем в общий системный лог
	logPath = "/var/log/" + logFileName
    } else {
	// Если запуск от обычного пользователя — пишем в его домашний каталог
	homeDir, err := os.UserHomeDir()
	if err != nil {
	    logPath = logFileName
	} else {
	    logPath = homeDir + "/" + logFileName
	}
    }

    // Инициализация дублирования логов (в консоль и в файл)
    logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
    if err != nil {
	log.Printf("WARNING: Cannot open log file %s: %v. Logging to stdout only.", logPath, err)
    } else {
	defer logFile.Close()
	multiWriter := io.MultiWriter(os.Stdout, logFile)
	log.SetOutput(multiWriter)
    }

    log.Printf("INFO: Target log path initialized: %s", logPath)
    log.Printf("INFO: Server started on %s", *listenAddr)

    // ИНТЕЛЛЕКТУАЛЬНЫЙ ПЕРЕХВАТЧИК СИСТЕМНЫХ PNG-ИКОНОК LINUX
    http.HandleFunc("/favicon.ico", func(w http.ResponseWriter, r *http.Request) {
	referer := r.Header.Get("Referer")
	vmName := ""

	// Извлекаем имя машины (токен) из Referer страницы
	if idx := strings.Index(referer, "token="); idx != -1 {
	    vmName = referer[idx+6:]
	    if cIdx := strings.IndexAny(vmName, "& "); cIdx != -1 {
		vmName = vmName[:cIdx]
	    }
	}

	if vmName != "" {
	    // Массив стандартных каталогов Linux для поиска PNG-иконок машины
	    searchPaths := []string{
		fmt.Sprintf("/usr/share/pixmaps/%s.png", vmName),
		fmt.Sprintf("/usr/share/icons/hicolor/48x48/apps/%s.png", vmName),
		fmt.Sprintf("/usr/share/icons/hicolor/scalable/apps/%s.png", vmName),
		fmt.Sprintf("%s/%s.png", *webRoot, vmName),
	    }

	    for _, path := range searchPaths {
		if _, err := os.Stat(path); err == nil {
		    w.Header().Set("Content-Type", "image/png")
		    http.ServeFile(w, r, path)
		    return
		}
	    }
	}

	// Откат: если персональной иконки нет, ищем стандартный логотип в системе
	defaultPaths := []string{
	    "/usr/share/pixmaps/debian-logo.png",
	    "/usr/share/pixmaps/astra_logo.png",
	    fmt.Sprintf("%s/favicon.png", *webRoot),
	}

	for _, path := range defaultPaths {
	    if _, err := os.Stat(path); err == nil {
		w.Header().Set("Content-Type", "image/png")
		http.ServeFile(w, r, path)
		return
	    }
	}

	// Если файлов иконок вообще нет в ОС — глушим запрос статусом 204 (No Content) без ошибок 404
	w.WriteHeader(http.StatusNoContent)
    })

    // Интеллектуальный роутер главной страницы
    http.HandleFunc("/vmconsole.html", func(w http.ResponseWriter, r *http.Request) {
	vmName := r.URL.Query().Get("token")
	vmTitle := r.URL.Query().Get("title")
	if vmTitle == "" {
	    vmTitle = "Виртуальная машина"
	}

	targetAddr := fmt.Sprintf("127.0.0.1:%s", getSpicePort(vmName))

	// Тестируем порт: если он закрыт — динамически читаем и рендерим launcher.html с диска
	conn, err := net.DialTimeout("tcp", targetAddr, 300*time.Millisecond)
	if err != nil {
	    w.Header().Set("Content-Type", "text/html; charset=utf-8")
	    tmpl, err := template.ParseFiles(fmt.Sprintf("%s/launcher.html", *webRoot))
	    if err != nil {
		log.Printf("ERROR: Failed to load launcher.html: %v", err)
		http.Error(w, "Ошибка загрузки шаблона launcher.html", http.StatusInternalServerError)
		return
	    }
	    tmpl.Execute(w, map[string]string{"Title": vmTitle, "Token": vmName})
	    return
	}
	conn.Close()

	// Если порт открыт — отдаем оригинальный интерфейс SPICE
	http.ServeFile(w, r, fmt.Sprintf("%s/vmconsole.html", *webRoot))
    })

    // Системные эндпоинты
    http.Handle("/", http.FileServer(http.Dir(*webRoot)))
    http.HandleFunc("/start", handleVMStart)
    http.HandleFunc("/websockify", handleWebSocketProxy)

    if err := http.ListenAndServe(*listenAddr, nil); err != nil {
	log.Fatalf("FATAL: Server failed: %v", err)
    }
}

// Парсинг порта регулярным выражением
func getSpicePort(vmName string) string {
    confPath := fmt.Sprintf("/srv/qemu/config/%s.conf", vmName)
    content, err := os.ReadFile(confPath)
    if err != nil {
	return "5913" 
    }

    matches := portRegexp.FindStringSubmatch(string(content))
    if len(matches) > 1 {
	return matches[1] // Возвращаем СТРОКУ (индекс 1), а не весь слайс
    }
    return "5913"
}

// Вызов лаунчера
func handleVMStart(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Access-Control-Allow-Origin", "*")
    vmName := r.URL.Query().Get("vm")
    if vmName == "" {
	http.Error(w, "Missing VM name", http.StatusBadRequest)
	return
    }

    log.Printf("EXEC: %s start %s", vmctrlPath, vmName)
    cmd := exec.Command(vmctrlPath, "start", vmName)
    cmd.Env = append(os.Environ(), "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    
    if err := cmd.Run(); err != nil {
	log.Printf("ERROR: Exec failed %s: %v", vmctrlPath, err)
	http.Error(w, err.Error(), http.StatusInternalServerError)
	return
    }
    w.Write([]byte("OK"))
}

// Бинарное WebSocket -> TCP проксирование
func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
    vmName := r.URL.Query().Get("token")
    targetAddr := fmt.Sprintf("127.0.0.1:%s", getSpicePort(vmName))

    wsConn, err := upgrader.Upgrade(w, r, nil)
    if err != nil {
	return
    }
    defer wsConn.Close()

    tcpConn, err := net.DialTimeout("tcp", targetAddr, 2*time.Second)
    if err != nil {
	wsConn.WriteMessage(websocket.CloseMessage, websocket.FormatCloseMessage(1011, "ВМ выключена"))
	return
    }
    defer tcpConn.Close()

    log.Printf("PROXY: Connection established for %s to %s", vmName, targetAddr)

    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()

    // Поток браузер -> QEMU
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

    // Поток QEMU -> браузер
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
