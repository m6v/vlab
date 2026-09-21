# Инструкция по сборке проекта веб-панели

## Структура проекта

/opt/vlab/panel2/
├── index.html             # Главный веб-интерфейс (Панель инструктора с деревом хостов)
├── server.py              # Python-скрипт бэкенда (API, проверка портов, веб-сервер)
├── tokens.json            # Файл конфигурации виртуальных машин (их IP и VNC-порты)
├── websockify             # Скомпилированный Go-бинарник (быстрый прокси-сервер для трафика)
└── noVNC-1.6.0/           # Изолированный каталог оригинального noVNC, с удалением неиспользуемых файлов
    ├── vnc.html           # Базовая веб-страница экрана, которую загружает iframe панели
    └── core/              # Каталог с JavaScript-модулями декодирования графики и сети
        ├── base64.js
        ├── display.js
        ├── input/
        │   ├── devices.js
        │   ├── keyboard.js
        │   ├── keysym.js
        │   ├── keysymdef.js
        │   ├── mouse.js
        │   └── xtscancodes.js
        ├── rfb.js         # Главный скрипт протокола VNC (RFB)
        ├── websock.js     # Модуль работы с WebSocket-соединением
        └── ...            # (Остальные служебные JS-модули ядра noVNC)


## Сборка novnc
```
cd /opt/vlab/panel
wget https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz
tar -xf noVNC-1.6.0.tar.gz

# Очистка noVNC-1.6.0
cd /opt/vlab/panel/noVNC-1.6.0
# Удаление импортов стилей из каталога app
sed -i 's|@import "../app/styles/|/* @import "|g' vnc.html 2>/dev/null || true
rm -rf app/ utils/ tests/ docs/ vendor/ po/ snap/
find . -maxdepth 1 -type f ! -name 'vnc.html' -delete
```

## Компиляция кастомного websockify
Компиляцию выполняют на технологической ЭВМ с доступом в интернет или наличием компилятора go
```
# Установка компилятора go
wget https://go.dev/dl/go1.27.1.linux-amd64.tar.gz
tar -xzf go1.27.1.linux-amd64.tar.gz -C /opt
# Компиляция кастомного websockify с флагами -s и -w для исключения из бинарника отладочной информации и символов компиляции
cd <path_to_websockify>
/opt/go/bin/go build -mod=vendor -ldflags="-s -w" -o websockify websockify.go
```

## Установка
Установку выполняют в следующем порядке:
1. копируют все файлы в целевой каталог (opt/vlab/panel2)
2. копируют кастомный websockify в целевой каталог
3. создают и запускают юнит службы, выполнив команды
```
cat << EOF > /etc/systemd/system/vlab-panel2.service
[Unit]
Description=Instructor's Web Panel version 2
After=network.target libvirtd.service
Requires=libvirtd.service

[Service]
Type=simple

WorkingDirectory=/opt/vlab/panel2
ExecStart=/opt/vlab/panel2/server.py

User=root
Group=root

Restart=on-failure
RestartSec=5s

KillMode=control-group
TimeoutStopSec=10s

[Install]
WantedBy=multi-user.target
EOF

systemctl enable --now vlab-panel2
```

# Отличия новой версии от старой, основанной на novnc и websockify из extended репозитория Astra Linux


Ключевые технические отличия:
1. Новая архитектура с отказом от тяжелых системных пакетов и зависимостей в пользу легковесности и изолированности

2. Отсутствие внешних зависимостей (Zero Dependencies)
Было: Для запуска оригинального Python-варианта websockify требовалось установить в операционную систему Astra Linux 15 тяжелых пакетов (включая такие библиотеки как python3-numpy, python-yaml, компоненты OpenStack oslo.config, stevedore и др.).
Стало: В систему не устанавливается вообще ничего. Новый бинарный файл websockify написан на Go, скомпилирован в монолитный исполняемый код (static binary) и содержит внутри себя абсолютно все необходимые сетевые функции. Для бэкенда панели (server.py) используется только встроенная стандартная библиотека Python, которая есть в Astra Linux изначально.

3. Изменение способа работы с токенами виртуальных машин
Было: Старый Python-websockify умел выполнять сложные сценарии, сам читал файлы конфигураций через тяжелые плагины (флаг --target-config) и требовал интеграции с системными путями (вроде /usr/share/novnc).
Стало: Логика разделилась более грамотно. Go-websockify сфокусирован только на одной задаче — максимально быстро и стабильно передавать трафик. Он принимает простой файл tokens.txt через флаг -f. А всю «умную» работу (парсинг JSON, опрос портов виртуальных машин и генерацию этого файла токенов) взял на себя скрипт server.py.

4. Разделение портов и путей (маршрутизация трафика)
Было: Старый websockify работал на одном порту как для веб-страниц, так и для трафика VNC. Запросы шли по путаному пути с параметром ?path=websockify?token=....
Стало: Архитектура стала чище.
    Порт 8000 (server.py) отвечает только за веб-интерфейс (index.html, vnc.html) и внутреннее API (/api/tokens).
    Порт 8085 (websockify) отвечает только за передачу графики экрана. Запрос от noVNC теперь идет напрямую в корень порта (ws://localhost:8085/?token=...), что снизило нагрузку на сеть и убрало лишние сетевые ошибки.

5. Автономность и переносимость (портативность)
Было: Проект был «размазан» по всей операционной системе. Конфигурация лежала в /etc/novnc/, скрипты noVNC в /usr/share/novnc/, а для работы требовались права root (sudo) для чтения системных папок.
Стало: Весь проект теперь — это одна изолированная папка (например, /opt/vlab-panel/). Скрипт, бинарник, конфигурационный JSON и папка с HTML-страницами лежат вместе. Проект можно просто скопировать и перенести на другую ЭВМ с Astra Linux, где он запустится без доролнительных настроек и прав root.

Общий размер проекта 9.1 МБ
