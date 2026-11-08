# Единый источник правды для универсальных компонентов
SERVER_NAME = vnc2ws-server
BRIDGE_NAME = vnc2ws-bridge

# Данные для генерации DEB-пакета
PKG_NAME = vlab-webengine
BASE_VERSION = 1.2

# Единый путь для установки
TARGET_DIR = /srv/qemu

.PHONY: all clean install uninstall deb

all: build

# Очистка папки проекта от собранных пакетов и временных файлов
clean:
	@echo "Очистка временных файлов сборщика и пакетов..."
	rm -f *.deb
	rm -rf __pycache__
	@echo "Пространство проекта очищено."

# Локальная установка скриптов в систему в директорию /srv/qemu (требует sudo)
install:
	@echo "Создание директории $(TARGET_DIR)..."
	mkdir -p $(TARGET_DIR)
	@echo "Копирование исполняемых скриптов и верстки в $(TARGET_DIR)..."
	install -m 0755 $(BRIDGE_NAME).py $(TARGET_DIR)/$(BRIDGE_NAME).py
	install -m 0755 $(SERVER_NAME).py $(TARGET_DIR)/$(SERVER_NAME).py
	install -m 0644 index.html $(TARGET_DIR)/index.html
	@if [ -f tokens.default ]; then install -m 0644 tokens.default $(TARGET_DIR)/tokens.default; fi
	@echo "Локальная установка в $(TARGET_DIR) завершена."

# Удаление исполняемых файлов из системы (требует sudo)
uninstall:
	@echo "Удаление файлов из $(TARGET_DIR)..."
	rm -f $(TARGET_DIR)/$(BRIDGE_NAME).py $(TARGET_DIR)/$(SERVER_NAME).py $(TARGET_DIR)/index.html $(TARGET_DIR)/tokens.default
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

	# Формирование дерева каталогов установки внутри пакета
	mkdir -p $(BUILD_DIR)/DEBIAN
	mkdir -p $(BUILD_DIR)$(TARGET_DIR)
	mkdir -p $(BUILD_DIR)/etc/systemd/system

	# Перенос скриптов в /srv/qemu
	cp $(BRIDGE_NAME).py $(BUILD_DIR)$(TARGET_DIR)/$(BRIDGE_NAME).py
	cp $(SERVER_NAME).py $(BUILD_DIR)$(TARGET_DIR)/$(SERVER_NAME).py
	chmod +x $(BUILD_DIR)$(TARGET_DIR)/$(BRIDGE_NAME).py
	chmod +x $(BUILD_DIR)$(TARGET_DIR)/$(SERVER_NAME).py
	
	# Перенос верстки и файлов конфигурации в /srv/qemu
	cp index.html $(BUILD_DIR)$(TARGET_DIR)/
	@if [ -f tokens.default ]; then cp tokens.default $(BUILD_DIR)$(TARGET_DIR)/; fi

	# Перенос конфигураций юнитов systemd
	@if [ -f $(BRIDGE_NAME).service ]; then cp $(BRIDGE_NAME).service $(BUILD_DIR)/etc/systemd/system/; fi
	@if [ -f $(SERVER_NAME).service ]; then cp $(SERVER_NAME).service $(BUILD_DIR)/etc/systemd/system/; fi

	# Сборка управляющего файла control
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

	# Интеграция скриптов автоматизации стадий (postinst, prerm) при наличии
	@if [ -f postinst ]; then cp postinst $(BUILD_DIR)/DEBIAN/; chmod 0755 $(BUILD_DIR)/DEBIAN/postinst; fi
	@if [ -f prerm ]; then cp prerm $(BUILD_DIR)/DEBIAN/; chmod 0755 $(BUILD_DIR)/DEBIAN/prerm; fi

	# Компиляция пакета силами dpkg-deb в корень проекта
	dpkg-deb --build $(BUILD_DIR) $(PKG_NAME)_$(BRIDGE_VERSION)_$(VERSION)_amd64.deb

	# Удаление временных файлов сборщика из /tmp
	rm -rf $(BUILD_DIR)
	@echo "Package compiled successfully: $(PKG_NAME)_$(BRIDGE_VERSION)_$(VERSION)_amd64.deb"
