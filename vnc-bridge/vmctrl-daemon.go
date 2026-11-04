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
	// Единый чистый периметр для сокетов управления
	socketPath  = "/tmp/qemu/vmctrl.sock"
	vmctrlPath  = "/usr/local/bin/vmctrl"
	logFileName = "vmctrl-daemon.log"
)

func main() {
	// 1. Принудительно создаем общую runtime-директорию с полными правами
	runDir := "/tmp/qemu"
	if err := os.MkdirAll(runDir, 0777); err != nil {
		log.Fatalf("FATAL: Cannot create run directory %s: %v", runDir, err)
	}

	// 2. Настройка одновременного логирования в файл и в консоль (stdout)
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

	// 3. Удаляем старый сокет, если он остался в папке после аварийного закрытия
	_ = os.Remove(socketPath)

	// 4. Создаем и запускаем UNIX-сокет управления [^1^]
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		log.Fatalf("FATAL: Cannot create Unix socket %s: %v", socketPath, err)
	}
	defer listener.Close()

	// Выставляем права 0666, чтобы vnc-bridge или пользователи могли передать команду запуска [^1^]
	_ = os.Chmod(socketPath, 0666)

	log.Printf("INFO: Administrative vmctrl-daemon successfully started on unix://%s", socketPath)

	for {
		conn, err := listener.Accept()
		if err != nil {
			continue
		}

		// Обработка каждого входящего сигнала в отдельном независимом потоке
		go func(c net.Conn) {
			defer c.Close()
			scanner := bufio.NewScanner(c)
			if scanner.Scan() {
				vmName := strings.TrimSpace(scanner.Text())
				if vmName == "" {
					return
				}

				log.Printf("EXEC: Processing console boot signal for target: %s", vmName)

				// Вызываем внешний bash-скрипт микро-оркестратора
				cmd := exec.Command(vmctrlPath, "start", vmName)
				cmd.Stdout = os.Stdout
				cmd.Stderr = os.Stderr
				
				// Передаем изолированный PATH и флаг WEB_MODE для подавления локального remote-viewer
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
