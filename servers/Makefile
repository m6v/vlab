UNIT_NAME = vlab-servers
BUILD_DIR = /tmp/deb-build
SYSTEMD_DIR = /etc/systemd/system
NSPAWN_DIR = /etc/systemd/nspawn
STORAGE_DIR = /opt/vlab/storage
FAKETIME_FILE = /etc/faketime
INITIAL_DATE = 2026-01-15 03:00:00


.PHONY: all install clean restart stop enter status arch deb

all: install restart

install:
	@echo "Копирование конфигураций и скриптов..."
	@mkdir -p $(STORAGE_DIR)/upper
	@mkdir -p $(STORAGE_DIR)/work
	@mkdir -p $(STORAGE_DIR)/merged
	@mkdir -p $(NSPAWN_DIR)
	@cp -f $(UNIT_NAME).nspawn $(NSPAWN_DIR)/$(UNIT_NAME).nspawn
	@cp -f $(UNIT_NAME).service $(SYSTEMD_DIR)/$(UNIT_NAME).service

	@echo "Установка и настройка chrony в контейнере..."
	@cd /tmp && apt-get download chrony
	@mkdir -p $(STORAGE_DIR)/upper/usr/lib
	@ln -sfn usr/lib $(STORAGE_DIR)/upper/lib
	@dpkg-deb --fsys-tarfile /tmp/chrony*.deb | tar -x --keep-directory-symlink -C $(STORAGE_DIR)/upper
	@cp -f chrony.conf $(STORAGE_DIR)/upper/etc/chrony/chrony.conf

	@cp -f entrypoint.sh $(STORAGE_DIR)/upper/entrypoint.sh
	@chmod +x $(STORAGE_DIR)/upper/entrypoint.sh
	@echo $$(date -d "$(INITIAL_DATE)" +%s) > $(FAKETIME_FILE)
	@systemctl daemon-reload

enter:
	@echo "Запуск оболочки в контейнере..."
	@nsenter --target $$(machinectl show $(UNIT_NAME) -p Leader --value) --mount --net --uts --ipc --pid /bin/bash

restart:
	@echo "Перезапуск контейнера..."
	@systemctl stop $(UNIT_NAME) || true
	@systemctl reset-failed $(UNIT_NAME) 2>/dev/null || true
	@systemctl restart $(UNIT_NAME)
	@echo "Служба успешно запущена."

stop:
	@echo "Останов контейнера..."
	@systemctl stop $(UNIT_NAME) || true
	@echo "Служба успешно остановлена."

status:
	@-systemctl status $(UNIT_NAME) --no-pager
	@-journalctl -u $(UNIT_NAME) --no-pager -n 10

arch: stop
	@echo "Создание архива верхнего слоя оверлея контейнера..."
	@tar --numeric-owner -czpf storage.tar.gz -C $(STORAGE_DIR) .

clean:
	@echo "Удаление проекта..."
	@systemctl stop $(UNIT_NAME) || true
	@umount -l $(STORAGE_DIR)/merged 2>/dev/null || true
	@rm -f $(NSPAWN_DIR)/$(UNIT_NAME).nspawn
	@rm -f $(SYSTEMD_DIR)/$(UNIT_NAME).service
	@rm -f $(TIMESHIFT_FILE)
	@rm -rf $(STORAGE_DIR)
	@systemctl daemon-reload
	@echo "Очистка завершена."

deb:
	@echo "Чтение шаблона deb-пакета..."
	$(eval PKG_NAME := $(shell awk '/^Package:/ {print $$2}' control))
	$(eval BASE_VER  := $(shell awk '/^Version:/ {print $$2}' control))
	$(eval PKG_ARCH := $(shell awk '/^Architecture:/ {print $$2}' control))

	$(eval GIT_REV  := $(shell git rev-list --count HEAD 2>/dev/null || echo 0))
	$(eval PKG_VER  := $(BASE_VER).$(GIT_REV))

	$(eval TMP_BUILD_DIR := $(shell mktemp -d /tmp/deb-build.XXXXXX))

	@echo "Подготовка структуры deb-пакета..."
	@mkdir -p $(TMP_BUILD_DIR)/DEBIAN
	@cp control $(TMP_BUILD_DIR)/DEBIAN/control

	@cp postinst $(TMP_BUILD_DIR)/DEBIAN/postinst
	@chmod 755 $(TMP_BUILD_DIR)/DEBIAN/postinst
	@cp prerm $(TMP_BUILD_DIR)/DEBIAN/prerm
	@chmod 755 $(TMP_BUILD_DIR)/DEBIAN/prerm

	@mkdir -p $(TMP_BUILD_DIR)$(STORAGE_DIR)
	@cp -a $(STORAGE_DIR) $(TMP_BUILD_DIR)$(dir $(STORAGE_DIR))

	@mkdir -p $(TMP_BUILD_DIR)$(NSPAWN_DIR)
	@mkdir -p $(TMP_BUILD_DIR)$(SYSTEMD_DIR)
	@cp $(UNIT_NAME).nspawn $(TMP_BUILD_DIR)$(NSPAWN_DIR)/
	@cp $(UNIT_NAME).service $(TMP_BUILD_DIR)$(SYSTEMD_DIR)/

	@echo "Сборка deb-пакета..."
	@sudo dpkg-deb --build $(TMP_BUILD_DIR) $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb
	@rm -rf $(TMP_BUILD_DIR)
	@echo "Пакет успешно собран: $(PKG_NAME)_$(PKG_VER)_$(PKG_ARCH).deb"
