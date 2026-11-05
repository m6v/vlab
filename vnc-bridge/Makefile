# Переменные сборки
CC = go build
FLAGS = -ldflags="-s -w"
DAEMON_NAME = vmctrl-daemon
BRIDGE_NAME = vnc-bridge

# Системные пути для установки
BIN_DIR = /usr/local/bin

.PHONY: all build clean install uninstall deps

all: build

# Проверяем наличие зависимостей. Если файлов нет — инициализируем и качаем
deps:
	@if [ ! -f go.mod ]; then \
		echo "Инициализация Go-модуля..."; \
		go mod init $(BRIDGE_NAME); \
	fi
	@if [ ! -f go.sum ]; then \
		echo "Проверка и скачивание зависимостей..."; \
		GOPROXY=direct go get github.com/gorilla/websocket; \
		GOPROXY=direct go mod tidy; \
	fi

# Компиляция бинарников (зависит от deps и исходных кодов)
build: deps vmctrl-daemon.go vnc-bridge.go
	@echo "Компиляция веб-интерфейса прокси... "
	$(CC) $(FLAGS) -o $(BRIDGE_NAME) vnc-bridge.go

# Очистка папки проекта от собранных файлов
clean:
	@echo "Очистка временных файлов сборки..."
	rm -f $(DAEMON_NAME) $(BRIDGE_NAME)
	@echo "Очищено"

# Локальная установка бинарников в систему (требует sudo)
install: build
	@echo "Копирование бинарников в $(BIN_DIR)..."
	install -m 0755 $(BRIDGE_NAME) $(BIN_DIR)/$(BRIDGE_NAME)
	install -m 0755 vmctrl-daemon.py $(BIN_DIR)/vmctrl-daemon
	@echo "Установка завершена"

# Удаление бинарников из системы (требует sudo)
uninstall:
	@echo "Удаление бинарников из $(BIN_DIR)..."
	rm -f $(BIN_DIR)/$(BRIDGE_NAME)
	@echo "Удалено"
