# Переменные сборки
CC = go build
FLAGS = -ldflags="-s -w"
DAEMON_NAME = vmctrl-daemon
BRIDGE_NAME = vnc-bridge

# Системные пути для установки
BIN_DIR = /usr/local/bin

.PHONY: all build clean install uninstall

all: build

# Цель компиляции обоих бинарников
build:
	@echo "Компиляция управляющего демона..."
	$(CC) $(FLAGS) -o $(DAEMON_NAME) vmctrl-daemon.go
	@echo "Компиляция веб-интерфейса прокси... "
	$(CC) $(FLAGS) -o $(BRIDGE_NAME) vnc-bridge.go
	@echo "Сборка успешно завершена"

# Очистка папки проекта от собранных файлов
clean:
	@echo "Очистка временных файлов сборки..."
	rm -f $(DAEMON_NAME) $(BRIDGE_NAME)
	@echo "Очищено"

# Локальная установка бинарников в систему (требует sudo)
install: build
	@echo "Копирование бинарников в $(BIN_DIR)..."
	install -m 0755 $(DAEMON_NAME) $(BIN_DIR)/$(DAEMON_NAME)
	install -m 0755 $(BRIDGE_NAME) $(BIN_DIR)/$(BRIDGE_NAME)
	@echo "Установка завершена"

# Удаление бинарников из системы (требует sudo)
uninstall:
	@echo "Удаление бинарников из $(BIN_DIR)..."
	rm -f $(BIN_DIR)/$(DAEMON_NAME)
	rm -f $(BIN_DIR)/$(BRIDGE_NAME)
	@echo "Удалено"
