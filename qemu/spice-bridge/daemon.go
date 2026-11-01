package main

import (
	"bufio"
	"log"
	"net"
	"os"
	"os/exec"
	"strings"
)

const (
	socketPath = "/tmp/vmctrl.sock"
	vmctrlPath = "/usr/local/bin/vmctrl"
)

func main() {
	_ = os.Remove(socketPath)

	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		log.Fatalf("FATAL: Не удалось создать Unix-сокет: %v", err)
	}
	defer listener.Close()

	_ = os.Chmod(socketPath, 0666)

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
				
				cmd.Env = append(os.Environ(), "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
				
				if err := cmd.Run(); err != nil {
					log.Printf("ERROR: Script exec failed: %v", err)
				} else {
					log.Printf("SUCCESS: Script completed boot routine for %s", vmName)
				}
			}
		}(conn)
	}
}
