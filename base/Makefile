TARGET_PATH = /srv/qemu
BIN_PATH = /usr/local/bin
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

	@cp preinst $(BUILD_PATH)/DEBIAN/preinst
	@chmod 755 $(BUILD_PATH)/DEBIAN/preinst
	@cp postinst $(BUILD_PATH)/DEBIAN/postinst
	@chmod 755 $(BUILD_PATH)/DEBIAN/postinst
	@cp postrm $(BUILD_PATH)/DEBIAN/postrm
	@chmod 755 $(BUILD_PATH)/DEBIAN/postrm

	@mkdir -p $(BUILD_PATH)/etc/skel
	@cp -r Desktop $(BUILD_PATH)/etc/skel

	@mkdir -p $(BUILD_PATH)$(TARGET_PATH)
	@cp -r config $(BUILD_PATH)$(TARGET_PATH)

	@mkdir -p $(BUILD_PATH)$(BIN_PATH)
	@cp vmctl $(BUILD_PATH)$(BIN_PATH)

	@mkdir -p $(BUILD_PATH)/usr/share
	@cp -r pixmaps $(BUILD_PATH)/usr/share

	@mkdir -p $(BUILD_PATH)/usr/share/bash-completion/completions
	@cp vmctl_completion $(BUILD_PATH)/usr/share/bash-completion/completions/vmctl

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"

clean:
	rm -rf $(BUILD_PATH)
	rm -f $(PKG_NAME)
