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
	socketPath = "/tmp/vmctrl.sock"
	vmctrlPath = "/usr/local/bin/vmctrl"
	logFileName = "vmctrl-daemon.log"
)

func main() {
	// Настройка логирования в файл и в консоль одновременно
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

	// Очищаем старый файл сокета, если он остался после прошлого запуска
	_ = os.Remove(socketPath)

	// Начинаем слушать Unix-сокет
	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		log.Fatalf("FATAL: Cannot create Unix socket: %v", err)
	}
	defer listener.Close()

	// Выставляем права 0666, чтобы веб-сервер (работающий от обычного пользователя) мог писать в сокет
	_ = os.Chmod(socketPath, 0666)

	log.Printf("INFO: Administrative vmctrl-daemon successfully started on unix://%s", socketPath)

	for {
		conn, err := listener.Accept()
		if err != nil {
			continue
		}

		// Обрабатываем каждое входящее подключение в отдельном потоке (горутине)
		go func(c net.Conn) {
			defer c.Close()
			scanner := bufio.NewScanner(c)
			if scanner.Scan() {
				vmName := strings.TrimSpace(scanner.Text())
				if vmName == "" {
					return
				}

				log.Printf("EXEC: Processing console boot signal for target: %s", vmName)

				// Вызываем ваш системный bash-скрипт: /usr/local/bin/vmctrl start <имя_вм>
				cmd := exec.Command(vmctrlPath, "start", vmName)
				
				// Перенаправляем стандартный вывод скрипта в консоль демона для отладки
				cmd.Stdout = os.Stdout
				cmd.Stderr = os.Stderr
				
				// Жестко задаем PATH, чтобы скрипт внутри себя видел все системные утилиты
				cmd.Env = append(os.Environ(), "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
				
				if err := cmd.Run(); err != nil {
					log.Printf("ERROR: Script exec failed for %s: %v", vmName, err)
				} else {
					log.Printf("SUCCESS: Script completed boot routine for %s", vmName)
				}
			}
		}(conn)
	}
}
