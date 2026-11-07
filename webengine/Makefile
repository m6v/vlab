# Единый источник правды для универсальных компонентов
SERVER_NAME = vnc2ws-server

# По умолчанию используем современную версию моста ws15
# Для сборки под Astra Linux запустите: make deb BRIDGE_VERSION=ws10
BRIDGE_VERSION ?= ws15
BRIDGE_NAME = vnc2ws-bridge-$(BRIDGE_VERSION)

# Данные для генерации DEB-пакета
PKG_NAME = vlab-webengine
BASE_VERSION = 1.2

# Единый системный путь для установки
TARGET_DIR = /srv/qemu

.PHONY: all build clean install uninstall deps deb release

all: build

# Проверка базовой среды интерпретатора
deps:
	@which python3 > /dev/null || (echo "[ERROR] python3 не найден в системе!"; exit 1)

# Проверяем наличие исходных файлов
build: deps $(BRIDGE_NAME).py $(SERVER_NAME).py index.html
	@echo "Исходные скрипты ($(BRIDGE_VERSION)) и компоненты UI верифицированы."

# Очистка папки проекта от собранных пакетов и временных файлов
clean:
	@echo "Очистка временных файлов сборщика и пакетов..."
	rm -f *.deb
	rm -rf __pycache__
	@echo "Пространство проекта очищено."

# Локальная установка скриптов в систему в директорию /srv/qemu (требует sudo)
install: build
	@echo "Создание директории $(TARGET_DIR)..."
	mkdir -p $(TARGET_DIR)
	@echo "Копирование исполняемых скриптов и верстки в $(TARGET_DIR)..."
	install -m 0755 $(BRIDGE_NAME).py $(TARGET_DIR)/vnc2ws-bridge.py
	install -m 0755 $(SERVER_NAME).py $(TARGET_DIR)/$(SERVER_NAME).py
	install -m 0644 index.html $(TARGET_DIR)/index.html
	@if [ -f tokens.default ]; then install -m 0644 tokens.default $(TARGET_DIR)/tokens.default; fi
	@echo "Локальная установка в $(TARGET_DIR) завершена. Исполняемый мост сохранен как vnc2ws-bridge.py"

# Удаление исполняемых файлов из системы (требует sudo)
uninstall:
	@echo "Удаление файлов из $(TARGET_DIR)..."
	rm -f $(TARGET_DIR)/vnc2ws-bridge.py $(TARGET_DIR)/$(SERVER_NAME).py $(TARGET_DIR)/index.html $(TARGET_DIR)/tokens.default
	@echo "Компоненты удалены."

# Автоматическая сборка легитимного .deb пакета
deb: build
	@echo "Calculating version number from git commit count..."
	$(eval COMMIT_COUNT := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval VERSION := $(BASE_VERSION).$(COMMIT_COUNT))
	@echo "Target package version defined as: $(VERSION)"

	@echo "Creating debian package structure in /tmp..."
	$(eval BUILD_DIR := $(shell mktemp -d -t debbuild.XXXXXX))
	@echo "Temporary build directory created at: $(BUILD_DIR)"

	# 1. Формирование дерева каталогов установки внутри пакета
	mkdir -p $(BUILD_DIR)/DEBIAN
	mkdir -p $(BUILD_DIR)$(TARGET_DIR)
	mkdir -p $(BUILD_DIR)/etc/systemd/system

	# 2. Перенос скриптов строго в /srv/qemu. На целевой системе файл будет называться просто vnc2ws-bridge.py
	cp $(BRIDGE_NAME).py $(BUILD_DIR)$(TARGET_DIR)/vnc2ws-bridge.py
	cp $(SERVER_NAME).py $(BUILD_DIR)$(TARGET_DIR)/$(SERVER_NAME).py
	chmod +x $(BUILD_DIR)$(TARGET_DIR)/vnc2ws-bridge.py
	chmod +x $(BUILD_DIR)$(TARGET_DIR)/$(SERVER_NAME).py
	
	# Перенос верстки и файлов конфигурации в /srv/qemu
	cp index.html $(BUILD_DIR)$(TARGET_DIR)/
	@if [ -f tokens.default ]; then cp tokens.default $(BUILD_DIR)$(TARGET_DIR)/; fi

	# 3. Перенос конфигураций юнитов systemd (юнит ссылается на общее имя vnc2ws-bridge.service)
	@if [ -f vnc2ws-bridge.service ]; then cp vnc2ws-bridge.service $(BUILD_DIR)/etc/systemd/system/; fi
	@if [ -f $(SERVER_NAME).service ]; then cp $(SERVER_NAME).service $(BUILD_DIR)/etc/systemd/system/; fi

	# 4. Сборка управляющего файла control
	@if [ -f control ]; then \
		cp control $(BUILD_DIR)/DEBIAN/control; \
	else \
		echo "Package: $(PKG_NAME)-$(BRIDGE_VERSION)" > $(BUILD_DIR)/DEBIAN/control; \
		echo "Architecture: amd64" >> $(BUILD_DIR)/DEBIAN/control; \
		echo "Maintainer: VLab Admin" >> $(BUILD_DIR)/DEBIAN/control; \
		echo "Description: Python Web Engine VNC Gateway ($(BRIDGE_VERSION))" >> $(BUILD_DIR)/DEBIAN/control; \
	fi
	@echo "Version: $(VERSION)" >> $(BUILD_DIR)/DEBIAN/control

	# Подсчет дискового пространства Installed-Size в Кб
	$(eval INSTALLED_SIZE := $(shell du -s --block-size=1024 $(BUILD_DIR) | cut -f1))
	@echo "Installed-Size: $(INSTALLED_SIZE)" >> $(BUILD_DIR)/DEBIAN/control

	# 5. Интеграция скриптов автоматизации стадий (postinst, prerm) при наличии
	@if [ -f postinst ]; then cp postinst $(BUILD_DIR)/DEBIAN/; chmod 0755 $(BUILD_DIR)/DEBIAN/postinst; fi
	@if [ -f prerm ]; then cp prerm $(BUILD_DIR)/DEBIAN/; chmod 0755 $(BUILD_DIR)/DEBIAN/prerm; fi

	# 6. Компиляция пакета силами dpkg-deb в корень проекта
	dpkg-deb --build $(BUILD_DIR) $(PKG_NAME)_$(BRIDGE_VERSION)_$(VERSION)_amd64.deb

	# 7. Удаление временных файлов сборщика из /tmp
	rm -rf $(BUILD_DIR)
	@echo "Package compiled successfully: $(PKG_NAME)_$(BRIDGE_VERSION)_$(VERSION)_amd64.deb"
