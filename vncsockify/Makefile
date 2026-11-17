BRIDGE_NAME = vncsockify
SYSTEMD_PATH = /etc/systemd/system
TARGET_PATH = /srv/qemu

.PHONY: all clean install uninstall deb

all: deb

# Автоматическая сборка .deb пакета
deb:
	@echo "Чтение шаблона deb-пакета..."
	$(eval PKG_NAME := $(shell awk '/^Package:/ {print $$2}' DEBIAN/control))
	$(eval BASE_VER  := $(shell awk '/^Version:/ {print $$2}' DEBIAN/control))
	$(eval PKG_ARCH := $(shell awk '/^Architecture:/ {print $$2}' DEBIAN/control))
	$(eval GIT_REV  := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval PKG_VER  := $(BASE_VER).$(GIT_REV))
	$(eval BUILD_PATH := $(shell mktemp -d /tmp/deb-build.XXXXXX))

	# Формирование дерева каталогов установки внутри пакета
	@cp -r DEBIAN $(BUILD_PATH)
	@mkdir -p $(BUILD_PATH)$(TARGET_PATH)
	@mkdir -p $(BUILD_PATH)$(SYSTEMD_PATH)

	# Перенос скрипта в /srv/qemu
	@cp $(BRIDGE_NAME).py $(BUILD_PATH)$(TARGET_PATH)

	# Перенос юнита systemd
	@cp $(BRIDGE_NAME).service $(BUILD_PATH)$(SYSTEMD_PATH)

	# Подсчет дискового пространства Installed-Size в Кб
	$(eval INSTALLED_SIZE := $(shell du -s --block-size=1024 $(BUILD_PATH) | cut -f1))
	@echo "Installed-Size: $(INSTALLED_SIZE)" >> $(BUILD_PATH)/DEBIAN/control

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"
