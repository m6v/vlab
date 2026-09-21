package main

import (
	"bufio"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"io"
	"time"
	"golang.org/x/net/websocket"
)

var (
	sourceAddr *string
	tokenFile  *string
)

func init() {
    // Дефолтные значение параметров
	sourceAddr = flag.String("l", "127.0.0.1:8085", "http service address")
	tokenFile = flag.String("f", "/tmp/tokens.txt", "path to flat tokens file")
}

func getVncAddress(targetToken string) string {
	file, err := os.Open(*tokenFile)
	if err != nil {
		return ""
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || !strings.Contains(line, ":") {
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		if len(parts) < 2 {
			continue
		}
		if strings.TrimSpace(parts[0]) == targetToken {
			return strings.TrimSpace(parts[1])
		}
	}
	return ""
}

func proxyHandler(ws *websocket.Conn) {
	defer ws.Close()
	ws.PayloadType = 0x02 // Режим BinaryMessage для передачи графики noVNC

	req := ws.Request()
	token := req.URL.Query().Get("token")
	vncTarget := getVncAddress(token)
	if vncTarget == "" {
		log.Printf("Token '%s' not found in %s", token, *tokenFile)
		return
	}

	log.Printf("Connecting token %s to VNC: %s", token, vncTarget)

	vncConn, err := net.DialTimeout("tcp", vncTarget, 5*time.Second)
	if err != nil {
		log.Printf("Failed to connect to VNC Server %s: %s", vncTarget, err)
		return
	}
	defer vncConn.Close()

	errChan := make(chan error, 2)
	go func() {
		_, err := io.Copy(ws, vncConn)
		errChan <- err
	}()
	go func() {
		_, err := io.Copy(vncConn, ws)
		errChan <- err
	}()

	<-errChan
}

func main() {
	flag.Parse()
	log.SetFlags(log.LstdFlags | log.Lshortfile)

	log.Printf("Starting token-websockify on %s (config: %s)", *sourceAddr, *tokenFile)

	http.Handle("/websockify", websocket.Handler(proxyHandler))
	http.Handle("/", websocket.Handler(proxyHandler))

	log.Fatal(http.ListenAndServe(*sourceAddr, nil))
}
