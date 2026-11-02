PACKAGE_NAME := vlab-qemu
PROJECT_DIR := qemu
ARCH := all
DESTDIR ?= /

# Базовый префикс версии (например: 1.0., 2.1)
BASE_VERSION := 1.0.

# Вычисляем итоговый номер версии на основе количества коммитов
COMMITS_COUNT := $(shell git rev-list --count HEAD 2>/dev/null || echo "1")
VERSION := $(BASE_VERSION)$(COMMITS_COUNT)

DEB_FILE := $(PACKAGE_NAME)_$(VERSION)_$(ARCH).deb

.PHONY: all deb install clean help

all: deb

# 1. Сборка DEB-пакета
deb:
	@echo "Расчет размера установленных файлов..."
	@size=$$(du -sk --exclude='DEBIAN' $(PROJECT_DIR) | cut -f1); \
	echo "Обновление версии ($(VERSION)) и размера ($$size КБ) в $(PROJECT_DIR)/DEBIAN/control..."; \
	sed -i '/^Version:/d' $(PROJECT_DIR)/DEBIAN/control; \
	sed -i '/^Installed-Size:/d' $(PROJECT_DIR)/DEBIAN/control; \
	sed -i -e :a -e '/^\n*$$/{$$d;N;ba}' $(PROJECT_DIR)/DEBIAN/control; \
	echo "Version: $(VERSION)" >> $(PROJECT_DIR)/DEBIAN/control; \
	echo "Installed-Size: $$size" >> $(PROJECT_DIR)/DEBIAN/control; \
	echo "" >> $(PROJECT_DIR)/DEBIAN/control
	@echo "Выставление прав на исполнение для служебных файлов..."
	chmod 755 $(PROJECT_DIR)/DEBIAN/control
	@if [ -f $(PROJECT_DIR)/DEBIAN/postinst ]; then chmod 755 $(PROJECT_DIR)/DEBIAN/postinst; fi
	@if [ -f $(PROJECT_DIR)/DEBIAN/postrm ]; then chmod 755 $(PROJECT_DIR)/DEBIAN/postrm; fi
	@echo "Сборка пакета $(DEB_FILE)..."
	dpkg-deb --root-owner-group --build $(PROJECT_DIR) $(DEB_FILE)
	@echo "Пакет успешно создан: $(DEB_FILE)"

# 2. Прямая установка файлов в систему (минуя пакет)
install:
	@echo "Прямая установка файлов в $(DESTDIR)..."
	sudo rsync -av --exclude='DEBIAN' --chown=root:root $(PROJECT_DIR)/ $(DESTDIR)/
	@echo "Выполнение постинсталляционных шагов..."
	@if [ -f $(PROJECT_DIR)/DEBIAN/postinst ]; then \
		sudo $(PROJECT_DIR)/DEBIAN/postinst configure; \
	fi
	@echo "Установка успешно завершена."

# 3. Очистка (удаляет все версии собранного пакета по его имени)
clean:
	@echo "Удаление всех пакетов $(PACKAGE_NAME)..."
	rm -f $(PACKAGE_NAME)_*.deb

help:
	@echo "Доступные команды:"
	@echo "  make deb     - Собрать .deb пакет с версией из Git и автоподсчетом размера"
	@echo "  make install - Скопировать файлы напрямую в систему"
	@echo "  make clean   - Удалить все собранные версии пакета $(PACKAGE_NAME)"
