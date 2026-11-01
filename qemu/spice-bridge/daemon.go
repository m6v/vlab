package main

import (
	"log"
	"net/http"
	"os"
	"os/exec"
)

const (
	listenAddr = ":8085"
	vmctrlPath = "/usr/local/bin/vmctrl"
)

func main() {
	http.HandleFunc("/start", func(w http.ResponseWriter, r *http.Request) {
		// Разрешаем кросс-доменные запросы от фронтенда (CORS)
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET")

		vmName := r.URL.Query().Get("vm")
		if vmName == "" {
			http.Error(w, "ERROR: Missing VM name", http.StatusBadRequest)
			return
		}

		log.Printf("EXEC: Activating system launcher for target VM: %s", vmName)

		// Вызываем ваш админский Bash-инструмент управления QEMU
		cmd := exec.Command(vmctrlPath, "start", vmName)
		
		// Передаем системные пути, чтобы vmctrl внутри себя видел envsubst и qemu
		cmd.Env = append(os.Environ(), "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
		
		if err := cmd.Run(); err != nil {
			log.Printf("ERROR: System launcher execution failed: %v", err)
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}

		log.Printf("SUCCESS: VM %s initialized and started successfully", vmName)
		w.Write([]byte("OK"))
	})

	log.Printf("INFO: Administrative vmctrl-daemon successfully started on %s", listenAddr)
	if err := http.ListenAndServe(listenAddr, nil); err != nil {
		log.Fatalf("FATAL: Daemon crashed: %v", err)
	}
}
