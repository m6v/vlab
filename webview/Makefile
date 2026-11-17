SERVER_NAME = webview-server
PANEL_NAME = webview-panel
TARGET_PATH = /srv/qemu
SYSTEMD_PATH = /etc/systemd/system
PIXMAPS_PATH = /usr/share/pixmaps

.PHONY: all deb

all: deb

deb: 
	@echo "Чтение шаблона deb-пакета..."
	$(eval PKG_NAME := $(shell awk '/^Package:/ {print $$2}' DEBIAN/control))
	$(eval BASE_VER  := $(shell awk '/^Version:/ {print $$2}' DEBIAN/control))
	$(eval PKG_ARCH := $(shell awk '/^Architecture:/ {print $$2}' DEBIAN/control))
	$(eval GIT_REV  := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval PKG_VER  := $(BASE_VER).$(GIT_REV))
	$(eval BUILD_PATH := $(shell mktemp -d /tmp/deb-build.XXXXXX))

	@echo "Подготовка структуры deb-пакета..."
	@cp -r DEBIAN $(BUILD_PATH)

	@mkdir -p $(BUILD_PATH)$(TARGET_PATH)
	@cp -a console.html panel.html webpanel.conf $(SERVER_NAME).py $(PANEL_NAME).py $(BUILD_PATH)$(TARGET_PATH)

	@mkdir -p $(BUILD_PATH)$(PIXMAPS_PATH)
	@cp -a virt-manager.png $(BUILD_PATH)$(PIXMAPS_PATH)

	@mkdir -p $(BUILD_PATH)$(SYSTEMD_PATH)
	@cp $(SERVER_NAME).service $(PANEL_NAME).service $(BUILD_PATH)$(SYSTEMD_PATH)

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"

clean:
	rm -rf $(BUILD_PATH)
	rm -f $(PKG_NAME)
