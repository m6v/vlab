# Переменные сборки
CC = go build
FLAGS = -ldflags="-s -w"
DAEMON_NAME = wcsrv
BRIDGE_NAME = vnc2ws

# Данные для генерации DEB-пакета
PKG_NAME = vlab-webengine
BASE_VERSION = 1.0

# Системные пути для установки
BIN_DIR = /usr/local/bin

.PHONY: all build clean install uninstall deps deb

all: build

# Проверяем наличие зависимостей. Если файлов нет — инициализируем и качаем
deps:
	@if [ ! -f go.mod ]; then \
		echo "Инициализация Go-модуля..."; \
		go mod init $(BRIDGE_NAME); \
	fi
	@if [ ! -f go.sum ]; then \
		echo "Проверка и скачивание зависимостей..." [0.13]; \
		GOPROXY=direct go get github.com/gorilla/websocket [0.13]; \
		GOPROXY=direct go mod tidy; \
	fi

# Компиляция бинарников (зависит от deps и исходных кодов)
build: deps $(BRIDGE_NAME).go
	@echo "Компиляция прокси... "
	$(CC) $(FLAGS) -o $(BRIDGE_NAME) $(BRIDGE_NAME).go

# Очистка папки проекта от собранных файлов и пакетов
clean:
	@echo "Очистка временных файлов сборки..."
	rm -f $(BRIDGE_NAME) *.deb
	@echo "Очищено"

# Локальная установка исполняемых файлов в систему (требует sudo)
install: build
	@echo "Копирование бинарников в $(BIN_DIR)..."
	install -m 0755 $(BRIDGE_NAME) $(BIN_DIR)/$(BRIDGE_NAME)
	install -m 0755 $(DAEMON_NAME) $(BIN_DIR)/$(DAEMON_NAME)
	@echo "Установка завершена"

# Удаление исполняемых файлов из системы (требует sudo)
uninstall:
	@echo "Удаление исполняемых файлов из $(BIN_DIR)..."
	rm -f $(BIN_DIR)/$(BRIDGE_NAME) $(BIN_DIR)/$(DAEMON_NAME)
	@echo "Удалено"

# Автоматическая сборка легитимного .deb пакета в директории /tmp
deb: build
	@echo "Calculating version number from git commit count..."
	$(eval COMMIT_COUNT := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval VERSION := $(BASE_VERSION).$(COMMIT_COUNT))
	@echo "Target package version defined as: $(VERSION)"

	@echo "Creating debian package structure in /tmp..."
	$(eval BUILD_DIR := $(shell mktemp -d -t debbuild.XXXXXX))
	@echo "Temporary build directory created at: $(BUILD_DIR)"

	# 1. Формирование структуры каталогов дерева установки
	mkdir -p $(BUILD_DIR)/DEBIAN
	mkdir -p $(BUILD_DIR)/srv/qemu
	mkdir -p $(BUILD_DIR)/etc/systemd/system

	# 2. Перенос исполняемых файлов, скриптов и верстки в /srv/qemu
	cp $(BRIDGE_NAME) $(BUILD_DIR)/srv/qemu/
	cp $(DAEMON_NAME) $(BUILD_DIR)/srv/qemu/
	cp index.html $(BUILD_DIR)/srv/qemu/

	# Копируем шаблон конфигурации в /srv/qemu/ внутри /tmp
	cp tokens.default $(BUILD_DIR)/srv/qemu/

	# 3. Перенос конфигураций юнитов системных служб
	cp $(BRIDGE_NAME).service $(BUILD_DIR)/etc/systemd/system/
	cp $(DAEMON_NAME).service $(BUILD_DIR)/etc/systemd/system/

	# 4. Сборка динамического управляющего файла control
	cp control $(BUILD_DIR)/DEBIAN/control
	@echo "Version: $(VERSION)" >> $(BUILD_DIR)/DEBIAN/control

	# Автоматический подсчет дискового пространства Installed-Size в Кб
	$(eval INSTALLED_SIZE := $(shell du -s --block-size=1024 $(BUILD_DIR) | cut -f1))
	@echo "Installed-Size: $(INSTALLED_SIZE)" >> $(BUILD_DIR)/DEBIAN/control

	# 5. Интеграция скриптов автоматизации стадий postinst и prerm
	cp postinst $(BUILD_DIR)/DEBIAN/
	cp prerm $(BUILD_DIR)/DEBIAN/

	# 6. Компиляция бинарного пакета силами dpkg-deb в корень проекта
	dpkg-deb --build $(BUILD_DIR) $(PKG_NAME)_$(VERSION)_amd64.deb

	# 7. Финальное удаление временных файлов сборщика из /tmp
	rm -rf $(BUILD_DIR)
	@echo "Package compiled successfully: $(PKG_NAME)_$(VERSION)_amd64.deb"
