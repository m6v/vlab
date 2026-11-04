package main

import (
	"bufio"
	"io"
	"log"
	"net"
	"os"
	"os/exec"
	"strings"
)

const (
	socketPath  = "/run/vmctrl/vmctrl.sock"
	vmctrlPath  = "/usr/local/bin/vmctrl"
	logFileName = "vmctrl-daemon.log"
)

func main() {
	// 1. ПРИНУДИТЕЛЬНО создаем runtime-директорию на первой секунде запуска
	runDir := "/run/vmctrl"
	if err := os.MkdirAll(runDir, 0755); err != nil {
		log.Fatalf("FATAL: Cannot create run directory %s: %v", runDir, err)
	}

	// 2. Настройка логирования в файл и в консоль одновременно
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

	// 3. Очищаем старый файл сокета inside нашей готовой папки
	_ = os.Remove(socketPath)

	// 4. Запускаем прослушивание Unix-сокета
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		log.Fatalf("FATAL: Cannot create Unix socket %s: %v", socketPath, err)
	}
	defer listener.Close()

	// Выставляем права, чтобы vnc-bridge (если он будет работать от root) имел полный доступ
	_ = os.Chmod(socketPath, 0660)

	log.Printf("INFO: Administrative vmctrl-daemon successfully started on unix://%s", socketPath)

	for {
		conn, err := listener.Accept()
		if err != nil {
			continue
		}

		go func(c net.Conn) {
			defer c.Close()
			scanner := bufio.NewScanner(c)
			if scanner.Scan() {
				vmName := strings.TrimSpace(scanner.Text())
				if vmName == "" {
					return
				}

				log.Printf("EXEC: Processing console boot signal for target: %s", vmName)

				cmd := exec.Command(vmctrlPath, "start", vmName)
				cmd.Stdout = os.Stdout
				cmd.Stderr = os.Stderr
				
				// WEB_MODE=1 для того, чтобы ваш bash-скрипт не запускал remote-viewer на хосте
				cmd.Env = append(os.Environ(), 
					"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
					"WEB_MODE=1",
				)
				
				if err := cmd.Run(); err != nil {
					log.Printf("ERROR: Script exec failed for %s: %v", vmName, err)
				} else {
					log.Printf("SUCCESS: Script completed boot routine for %s", vmName)
				}
			}
		}(conn)
	}
}
