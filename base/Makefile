TARGET_PATH = /srv/qemu
BIN_PATH = /usr/local/bin
PIXMAPS_PATH = /usr/share/pixmaps

.PHONY: all deb

all: deb

deb:
	@echo "Подготовка структуры deb-пакета..."
	$(eval BUILD_PATH := $(shell mktemp -d /tmp/deb-build.XXXXXX))
	@cp -r DEBIAN $(BUILD_PATH)

	@echo "Чтение шаблона deb-пакета..."
	$(eval PKG_NAME := $(shell awk '/^Package:/ {print $$2}' DEBIAN/control))
	$(eval BASE_VER  := $(shell awk '/^Version:/ {print $$2}' DEBIAN/control))
	$(eval PKG_ARCH := $(shell awk '/^Architecture:/ {print $$2}' DEBIAN/control))
	$(eval GIT_REV  := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval PKG_VER  := $(BASE_VER).$(GIT_REV))

	@mkdir -p $(BUILD_PATH)/etc/skel
	@cp -r Desktop $(BUILD_PATH)/etc/skel

	@mkdir -p $(BUILD_PATH)$(TARGET_PATH)
	@cp -r config $(BUILD_PATH)$(TARGET_PATH)

	@mkdir -p $(BUILD_PATH)$(BIN_PATH)
	@cp vmctl snapshoter $(BUILD_PATH)$(BIN_PATH)

	mkdir -p $(BUILD_PATH)/usr/lib/systemd/user
	@cp snapshoter.service $(BUILD_PATH)/usr/lib/systemd/user

	@mkdir -p $(BUILD_PATH)/usr/share
	@cp -r pixmaps $(BUILD_PATH)/usr/share

	@mkdir -p $(BUILD_PATH)/usr/share/bash-completion/completions
	@cp vmctl_completion $(BUILD_PATH)/usr/share/bash-completion/completions/vmctl

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"
