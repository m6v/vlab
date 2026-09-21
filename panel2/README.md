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


## Сборка сторонних компонентов (novnc и websockify)
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

# Компиляция websockify
git clone https://github.com/novnc/websockify-other
cd websockify-other/golang
go mod init websockify
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o websockify
cp golang/websokify /opt/vlab/panel
```

- `CGO_ENABLED=0` — отключает зависимости от системных C-библиотек (glibc) (делает бинарник полностью автономным (static));
- `GOOS=linux` — указывает целевую операционную систему (Linux / Astra Linux);
- `GOARCH=amd64` — задает стандартную 64-битную архитектуру процессора (Intel/AMD);
- `-ldflags="-s -w"` — очищает бинарник от отладочной информации и символов компиляции, что уменьшает размер готового файла примерно в два раза (до ~6–8 МБ);
- `-o websockify` — задает имя выходного файла.

## Установка
1. Скопировать все файлы в целевой каталог (opt/vlab/panel2)
2. Создать и запустить юнит службы, выполнив команды
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

Главное отличие между новой и старой архитектурой заключается в полном отказе от тяжелых системных пакетов и зависимостей в пользу легковесности и изолированности.

Ключевые технические отличия:

1. Отсутствие внешних зависимостей (Zero Dependencies)
Было: Для запуска оригинального Python-варианта websockify требовалось установить в операционную систему Astra Linux 15 тяжелых пакетов (включая такие библиотеки как python3-numpy, python-yaml, компоненты OpenStack oslo.config, stevedore и др.).
Стало: В систему не устанавливается вообще ничего. Новый бинарный файл websockify написан на Go, скомпилирован в монолитный исполняемый код (static binary) и содержит внутри себя абсолютно все необходимые сетевые функции. Для бэкенда панели (server.py) используется только встроенная стандартная библиотека самого Python, которая есть в Astra Linux изначально.

2. Изменение способа работы с токенами виртуальных машин
Было: Старый Python-websockify умел выполнять сложные сценарии, сам читал файлы конфигураций через тяжелые плагины (флаг --target-config) и требовал интеграции с системными путями (вроде /usr/share/novnc).
Стало: Логика разделилась более грамотно. Go-websockify сфокусирован только на одной задаче — максимально быстро и стабильно передавать трафик. Он принимает простой файл tokens.txt через лаконичный флаг -f. А всю «умную» работу (парсинг JSON, опрос портов виртуальных машин и генерацию этого файла токенов) взял на себя скрипт server.py.

3. Разделение портов и путей (маршрутизация трафика)
Было: Старый websockify работал на одном порту как для веб-страниц, так и для трафика VNC. Запросы шли по путаному пути с параметром ?path=websockify?token=....
Стало: Архитектура стала чище.
    Порт 8000 (server.py) отвечает только за веб-интерфейс (index.html, vnc.html) и внутреннее API (/api/tokens).
    Порт 8085 (websockify) отвечает только за передачу графики экрана. Запрос от noVNC теперь идет напрямую в корень порта (ws://localhost:8085/?token=...), что снизило нагрузку на сеть и убрало лишние сетевые ошибки.

4. Автономность и переносимость (портативность)
Было: Проект был «размазан» по всей операционной системе. Конфигурация лежала в /etc/novnc/, скрипты noVNC в /usr/share/novnc/, а для работы требовались права root (sudo) для чтения системных папок.
Стало: Весь проект теперь — это одна изолированная папка (например, /opt/vlab-panel/). Скрипт, бинарник, конфигурационный JSON и папка с HTML-страницами лежат вместе. Проект можно просто скопировать и перенести на другую ЭВМ с Astra Linux, где он запустится без доролнительных настроек и прав root.

Общий размер проекта 7.1 МБ
