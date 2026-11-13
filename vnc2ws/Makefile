BRIDGE_NAME = vnc2ws-bridge
SYSTEMD_PATH = /etc/systemd/system
TARGET_PATH = /srv/qemu

.PHONY: all clean install uninstall deb

all: deb

# Локальная установка скриптов в систему в директорию /srv/qemu (требует sudo)
install:
	@echo "Создание директории $(TARGET_PATH)..."
	mkdir -p $(TARGET_PATH)
	@echo "Копирование исполняемых скриптов и верстки в $(TARGET_PATH)..."
	install -m 0755 $(BRIDGE_NAME).py $(TARGET_PATH)/$(BRIDGE_NAME).py
	@if [ -f tokens.default ]; then install -m 0644 tokens.default $(TARGET_PATH)/tokens.default; fi
	@echo "Локальная установка в $(TARGET_PATH) завершена."

# Удаление исполняемых файлов из системы (требует sudo)
uninstall:
	@echo "Удаление файлов из $(TARGET_PATH)..."
	rm -f $(TARGET_PATH)/$(BRIDGE_NAME).py $(TARGET_PATH)/index.html $(TARGET_PATH)/tokens.default
	@echo "Компоненты удалены."

# Автоматическая сборка .deb пакета
deb:
	@echo "Чтение шаблона deb-пакета..."
	$(eval PKG_NAME := $(shell awk '/^Package:/ {print $$2}' control))
	$(eval BASE_VER  := $(shell awk '/^Version:/ {print $$2}' control))
	$(eval PKG_ARCH := $(shell awk '/^Architecture:/ {print $$2}' control))
	$(eval GIT_REV  := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval PKG_VER  := $(BASE_VER).$(GIT_REV))
	$(eval BUILD_PATH := $(shell mktemp -d /tmp/deb-build.XXXXXX))

	# Формирование дерева каталогов установки внутри пакета
	mkdir -p $(BUILD_PATH)/DEBIAN
	mkdir -p $(BUILD_PATH)$(TARGET_PATH)
	mkdir -p $(BUILD_PATH)$(SYSTEMD_PATH)
	@cp control $(BUILD_PATH)/DEBIAN/control

	# Перенос скриптов в /srv/qemu
	cp $(BRIDGE_NAME).py $(BUILD_PATH)$(TARGET_PATH)/$(BRIDGE_NAME).py
	chmod +x $(BUILD_PATH)$(TARGET_PATH)/$(BRIDGE_NAME).py
	
	# Перенос файла с токенами в в /srv/qemu
	@if [ -f tokens.default ]; then cp tokens.default $(BUILD_PATH)$(TARGET_PATH)/; fi

	# Перенос конфигураций юнитов systemd
	@cp $(BRIDGE_NAME).service $(BUILD_PATH)$(SYSTEMD_PATH)

	# Подсчет дискового пространства Installed-Size в Кб
	$(eval INSTALLED_SIZE := $(shell du -s --block-size=1024 $(BUILD_PATH) | cut -f1))
	@echo "Installed-Size: $(INSTALLED_SIZE)" >> $(BUILD_PATH)/DEBIAN/control

	# Интеграция скриптов автоматизации стадий (postinst, prerm) при наличии
	@cp postinst $(BUILD_PATH)/DEBIAN/postinst
	@chmod 755 $(BUILD_PATH)/DEBIAN/postinst
	@cp prerm $(BUILD_PATH)/DEBIAN/prerm
	@chmod 755 $(BUILD_PATH)/DEBIAN/prerm

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"

# Очистка папки проекта от собранных пакетов и временных файлов
clean:
	@echo "Очистка временных файлов сборщика и пакетов..."
	rm -rf $(BUILD_PATH)
	rm -f $(PKG_NAME)
	rm -rf __pycache__
	@echo "Пространство проекта очищено."
