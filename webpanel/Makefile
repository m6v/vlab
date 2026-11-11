UNIT_NAME = vlab-webpanel
TARGET_PATH = /srv/qemu
SYSTEMD_PATH = /etc/systemd/system
PIXMAPS_PATH = /usr/share/pixmaps

.PHONY: all deb

all: deb

deb: 
	@echo "Чтение шаблона deb-пакета..."
	$(eval PKG_NAME := $(shell awk '/^Package:/ {print $$2}' control))
	$(eval BASE_VER  := $(shell awk '/^Version:/ {print $$2}' control))
	$(eval PKG_ARCH := $(shell awk '/^Architecture:/ {print $$2}' control))
	$(eval GIT_REV  := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval PKG_VER  := $(BASE_VER).$(GIT_REV))
	$(eval BUILD_PATH := $(shell mktemp -d /tmp/deb-build.XXXXXX))

	@echo "Подготовка структуры deb-пакета..."
	@mkdir -p $(BUILD_PATH)/DEBIAN
	@cp control $(BUILD_PATH)/DEBIAN/control

	@cp postinst $(BUILD_PATH)/DEBIAN/postinst
	@chmod 755 $(BUILD_PATH)/DEBIAN/postinst
	@cp prerm $(BUILD_PATH)/DEBIAN/prerm
	@chmod 755 $(BUILD_PATH)/DEBIAN/prerm

	@mkdir -p $(BUILD_PATH)$(TARGET_PATH)
	@cp -a panel.html viewer.html vnc2ws-webpanel.py $(BUILD_PATH)$(TARGET_PATH)
	@mkdir -p $(BUILD_PATH)$(PIXMAPS_PATH)
	@cp -a virt-manager.png $(BUILD_PATH)$(PIXMAPS_PATH)

	@mkdir -p $(BUILD_PATH)$(SYSTEMD_PATH)
	@cp $(UNIT_NAME).service $(BUILD_PATH)$(SYSTEMD_PATH)

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"

clean:
	rm -rf $(BUILD_PATH)
	rm -f $(PKG_NAME)
