package main

import (
	"context"
	"html/template"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

const (
	listenAddr = ":8080"
	webRoot    = "noVNC-1.7.0"
	daemonSock = "/tmp/vmctrl.sock"

	// Текущие параметры для вашей ВМ
	vmName       = "srv-szi"
	vncTarget    = "127.0.0.1:5912"
	qemuMonitor  = "/tmp/srv-szi.sock"
	templatePath = "index.html"
)

type PageData struct {
	VMName    string
	IsRunning bool
}

// Настройка апгрейдера
var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

func main() {
	log.Printf("INFO: VNC interface started for VM %s on %s", vmName, listenAddr)

	http.HandleFunc("/", handleIndex)
	http.HandleFunc("/start", handleStart)
	http.HandleFunc("/ws", handleWebSocketProxy)
	http.Handle("/novnc/", http.StripPrefix("/novnc/", http.FileServer(http.Dir(webRoot))))

	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Web server failed: %v", err)
	}
}

func isVMRunning() bool {
	conn, err := net.DialTimeout("unix", qemuMonitor, 150*time.Millisecond)
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

	tmpl, err := template.ParseFiles(templatePath)
	if err != nil {
		log.Printf("ERROR: Failed to load template %s: %v", templatePath, err)
		http.Error(w, "Internal Server Error", http.StatusInternalServerError)
		return
	}

	data := PageData{
		VMName:    vmName,
		IsRunning: isVMRunning(),
	}

	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := tmpl.Execute(w, data); err != nil {
		log.Printf("ERROR: Failed to execute template: %v", err)
	}
}

func handleStart(w http.ResponseWriter, r *http.Request) {
	conn, err := net.Dial("unix", daemonSock)
	if err != nil {
		log.Printf("ERROR: Daemon %s is offline: %v", daemonSock, err)
		http.Error(w, "Daemon offline", http.StatusInternalServerError)
		return
	}
	defer conn.Close()

	if _, err := conn.Write([]byte(vmName + "\n")); err != nil {
		log.Printf("ERROR: Failed to write to socket: %v", err)
		http.Error(w, "Write error", http.StatusInternalServerError)
		return
	}

	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte("OK"))
}

func handleWebSocketProxy(w http.ResponseWriter, r *http.Request) {
	log.Printf("INFO: Incoming connection request on URI: %s", r.URL.Path)

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

	tcpConn, err := net.DialTimeout("tcp", vncTarget, 2*time.Second)
	if err != nil {
		log.Printf("ERROR: Dial QEMU VNC %s failed: %v", vncTarget, err)
		return
	}
	defer tcpConn.Close()

	log.Printf("SUCCESS: Bridge established with QEMU VNC!")

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Поток 1: Браузер (noVNC WebSocket) -> QEMU (Чистый TCP)
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

	// Поток 2: QEMU (Чистый TCP) -> Браузер (noVNC WebSocket)
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
