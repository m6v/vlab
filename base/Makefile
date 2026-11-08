PACKAGE_NAME := vlab-base
PROJECT_DIR := root
ARCH := all
DESTDIR ?= /

# Базовый префикс версии (например: 1.0., 2.1)
BASE_VERSION := 1.0.

# Вычисление итогового номера версии на основе количества коммитов
COMMITS_COUNT := $(shell git rev-list --count HEAD 2>/dev/null || echo "1")
VERSION := $(BASE_VERSION)$(COMMITS_COUNT)

DEB_FILE := $(PACKAGE_NAME)_$(VERSION)_$(ARCH).deb

.PHONY: all deb install skel2user clean help

all: deb

# Сборка DEB-пакета
deb:
	@echo "Расчет размера установленных файлов..."
	$(eval SIZE := $(shell du -sk --exclude='DEBIAN' $(PROJECT_DIR) | cut -f1))
	@echo "Обновление версии ($(VERSION)) и размера ($(SIZE) КБ) в $(PROJECT_DIR)/DEBIAN/control..."

	@grep -vE '^(Version|Installed-Size):' $(PROJECT_DIR)/DEBIAN/control > $(PROJECT_DIR)/DEBIAN/control.tmp
	@echo "Version: $(VERSION)" >> $(PROJECT_DIR)/DEBIAN/control.tmp
	@echo "Installed-Size: $(SIZE)" >> $(PROJECT_DIR)/DEBIAN/control.tmp
	@mv $(PROJECT_DIR)/DEBIAN/control.tmp $(PROJECT_DIR)/DEBIAN/control

	@echo "Выставление прав на файлы структуры DEBIAN..."
	@chmod 644 $(PROJECT_DIR)/DEBIAN/control
	@if [ -f $(PROJECT_DIR)/DEBIAN/preinst ]; then chmod 755 $(PROJECT_DIR)/DEBIAN/preinst; fi
	@if [ -f $(PROJECT_DIR)/DEBIAN/postinst ]; then chmod 755 $(PROJECT_DIR)/DEBIAN/postinst; fi
	@if [ -f $(PROJECT_DIR)/DEBIAN/postrm ]; then chmod 755 $(PROJECT_DIR)/DEBIAN/postrm; fi

	@echo "Сборка пакета $(DEB_FILE)..."
	@# Используем zstd для баланса скорости и сжатия (если ОС старая, можно заменить на -Zxz)
	dpkg-deb -Zgzip --root-owner-group --build $(PROJECT_DIR) $(DEB_FILE)
	@echo "Пакет успешно создан: $(DEB_FILE)"

# Прямая установка файлов в систему
install:
	@echo "Прямая установка файлов в $(DESTDIR)..."
	sudo rsync -av --exclude='DEBIAN' --chown=root:root $(PROJECT_DIR)/ $(DESTDIR)/
	@echo "Выполнение постинсталляционных действий..."
	@if [ -f $(PROJECT_DIR)/DEBIAN/postinst ]; then \
		sudo $(PROJECT_DIR)/DEBIAN/postinst configure; \
	fi
	@echo "Установка успешно завершена."

# Копирование шаблонов из /etc/skel/ в домашний каталог текущего пользователя
skel2user:
	@echo "Настройка окружения для пользователя $(USER)..."
	@echo "Копирование шаблонов из /etc/skel/ в $(HOME)..."
	sudo rsync -av --ignore-existing --chown=$(USER):$(USER) /etc/skel/ $(HOME)/
	@echo "Настройка каталога пользователя завершена."

clean:
	@echo "Удаление всех пакетов $(PACKAGE_NAME)..."
	rm -f $(PACKAGE_NAME)_*.deb

help:
	@echo "Доступные команды:"
	@echo "  make deb       - Собрать .deb пакет с версией из Git и автоподсчетом размера"
	@echo "  make install   - Скопировать файлы напрямую в систему"
	@echo "  make clean     - Удалить все собранные версии пакета $(PACKAGE_NAME)"
	@echo "  make skel2user - Скопировать шаблоны из /etc/skel/ в домашний каталог текущего пользователя"
