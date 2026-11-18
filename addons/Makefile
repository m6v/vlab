BIN_PATH = /usr/local/bin
SYSTEMD_PATH = /etc/systemd/system

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

	@mkdir -p $(BUILD_PATH)$(BIN_PATH)
	@cp timemachine.py $(BUILD_PATH)$(BIN_PATH)

	@mkdir -p $(BUILD_PATH)$(SYSTEMD_PATH)
	@cp vlab-httpd.service timemachine.service $(BUILD_PATH)$(SYSTEMD_PATH)

	@echo "Сборка deb-пакета..."
	@dpkg-deb --build --root-owner-group $(BUILD_PATH) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(BUILD_PATH)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"
