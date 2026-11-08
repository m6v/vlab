# Структура проекта

Проект обеспечивает сборку контейнера в котором функционирует сервер времени chronyd и http-сервер для доступа к main и update-main репозиторию Astra Linux 1.7

```
/
├── etc/
│   ├── faketime                        # Cмещенное время (Unixtime)
│   └── systemd/
│       ├── nspawn/
│       │   └── vlab-servers.nspawn     # Сетевой конфигурационный .nspawn файл (мост virbr0)
│       └── system/
│           └── vlab-servers.service    # Юнит-файл службы systemd
│
└── opt/
    └── vlab/
        └── servers/                    # Корневая директория проекта контейнера
            ├── work/                   # Рабочая директория оверлея
            ├── merged/                 # Директория монтирования оверлея
            └── upper/                  # Директория верхнего слоя оверлея
                ├── entrypoint.sh       # Скрипт инициализации сети и запуска chrony
                └── etc/
                    └── chrony/
                        └── chrony.conf # Конфиг chrony
```

- `faketime` — содержит смещенное время (unixtime) которое раздается клиентам (вычисляется перед остановом контейнера путем добавления времени работы контейнера к предыдцущему значению в `faketime`);
- `vlab-servers.nspawn` — связывает контейнер с мостом `virbr0` и фиксирует имя интерфейса;
- `entrypoint.sh` — скрипт контейнера (назначает IP 10.0.0.254 интерфейсу `host0`, запускает `chronyd -d -x` и через `chronyc settime` устанавливает смещенное время);
- `chrony.conf` — конфиг chrony внутри контейнера (включает режим `manual`, `local stratum 10`, открывает доступ для сети 10.0.0.0/24 и делает привязку к bindaddress 10.0.0.254).


Альтернативный вариант это запуск на хосте без контейнера, например, если мост имеет адрес `10.10.0.253`, то
```
python3 -m http.server 8000 --bind 10.10.0.253
```
в виртуалке будет доступен по `10.10.0.253:8000`


# Проверка настроек сети

> Если сетевое взаимодействие контейнера с хостом и клиентами отсутствует, нужно проверить к какому мосту подключен интерфейс контейнера `vb-vlab-servers`
Пример, когда вместо моста `virbr0` интерфейс подключился к мосту `br0`
```
7: vb-vlab-servers@if2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue master br0 state UP group default qlen 1000
    link/ether 6a:ef:48:dc:74:8b brd ff:ff:ff:ff:ff:ff link-netnsid 0
```
Это происходит из-за настроек `systemd-neyworkd`. В данном случае в `/etc/systemd/network` есть файл с конфигом настройки, в соответствии с которым все интерфейсы, с именем, удовлетворяющем шаблону `v[eb]-*`, подключаются к мосту `br0`
```
[Match]
Name=v[eb]-*
[Network]
Bridge=br0
```
Чтобы исправить ситуацию, нужно добавить в секцию `[Match]` параметр `Name=!vb-vlab-servers`, перезагрузить службы `systemd-neyworkd` и `vlab-servers`
Теперь видим, что интерфейс контейнера `vb-vlab-servers` подключен к мосту `virbr0`
```
9: vb-vlab-servers@if2: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue master virbr0 state UP group default qlen 1000
    link/ether 6a:ef:48:dc:74:8b brd ff:ff:ff:ff:ff:ff link-netnsid 0
```

# Поднятие моста
Для тестирования работы контейнера без установленного libvirt необходимо поднять мост вручную с помощью команд
```
# Создание моста
ip link add name virbr0 type bridge
ip link set dev virbr0 up
ip addr add 10.0.0.1/24 dev virbr0
# Удаление моста
ip link delete virbr0
```

## Установка сервера времени
```bash
sudo -i
# Установка контейнера
make install
# Вход в контейнер
nsenter --target $(machinectl show vlab-servers -p Leader --value) --mount --net --uts --ipc --pid /bin/bash
# Действия внутри контейнера
apt install chrony
# На запрос действия с измененным файлом настройки chrony.conf выбрать "сохранить измененную локальную версию"
exit
```

# Проверка состояния службы
```
sudo make status
● vlab-servers.service - Service Container for vlab
     Loaded: loaded (/etc/systemd/system/vlab-servers.service; disabled; preset: enabled)
     Active: active (running) since Sat 2026-08-15 10:04:51 MSK; 6s ago
 Invocation: 967e631c638140bd914ec43ec73e3cc9
    Process: 36391 ExecStartPre=/usr/bin/machinectl terminate vlab-servers (code=exited, status=1/FAILURE)
    Process: 36392 ExecStartPre=/usr/bin/umount -l ${STORAGE_ROOT}/merged (code=exited, status=32)
    Process: 36394 ExecStartPre=/usr/bin/mkdir -p ${STORAGE_ROOT}/upper ${STORAGE_ROOT}/work ${STORAGE_ROOT}/merged (code=exited, status=0/SUCCESS)
    Process: 36397 ExecStartPre=/usr/bin/mount -t overlay overlay -o lowerdir=/,upperdir=${STORAGE_ROOT}/upper,workdir=${STORAGE_ROOT}/work ${STORAGE_ROOT}/merged (code=exited, status=0/SUCCESS)
    Process: 36398 ExecStartPre=/usr/local/bin/timeshifter (code=exited, status=0/SUCCESS)
   Main PID: 36401 (systemd-nspawn)
      Tasks: 4 (limit: 18854)
     Memory: 12.3M (peak: 12.7M)
        CPU: 309ms
     CGroup: /system.slice/vlab-servers.service
             ├─payload
             │ ├─36404 /bin/sleep infinity
             │ ├─36432 python3 -m http.server --directory /srv/repo 80
             │ └─36433 /usr/sbin/chronyd -d -x
             └─supervisor
               └─36401 /usr/bin/systemd-nspawn --keep-unit --machine=vlab-servers -D /var/lib/machines/vlab-servers/merged /e…

авг 15 10:04:51 acer systemd-nspawn[36401]: 2026-08-15T07:04:51Z chronyd version 4.6.1 starting (+CMDMON +NTP +REF… -DEBUG)
авг 15 10:04:51 acer systemd-nspawn[36401]: 2026-08-15T07:04:51Z Disabled control of system clock
авг 15 10:04:51 acer systemd-nspawn[36401]: TimeMachine: Установка смещенного времени 2026-01-15T00:05:37...
авг 15 10:04:51 acer systemd-nspawn[36401]: 2026-08-15T07:04:51Z Making a slew of 18367154.827734
авг 15 10:04:51 acer systemd-nspawn[36401]: 2026-08-15T07:04:51Z System clock wrong by -18367154.827734 seconds
авг 15 10:04:51 acer systemd-nspawn[36401]: 200 OK
авг 15 10:04:51 acer systemd-nspawn[36401]: Clock was 18367154.00 seconds fast.  Frequency change = 0.00ppm, new f… 0.00ppm
авг 15 10:04:51 acer systemd-nspawn[36401]: 2026-08-15T07:04:51Z Making a slew of 0.000000
авг 15 10:04:51 acer systemd-nspawn[36401]: 200 OK
авг 15 10:04:51 acer systemd-nspawn[36401]: Serving HTTP on 0.0.0.0 port 80 (http://0.0.0.0:80/) ...
Hint: Some lines were ellipsized, use -l to show in full.
```

# Настройка клиентов

## Настройка конфигурации
```
sudo -i
apt install chrony

CONF_PATH="/etc/chrony/chrony.conf"

# Резервное копирование chrony.conf
cp "$CONF_PATH" "${CONF_PATH}.bak"

# Отключаение дефолтных серверовы и пулов
sed -i 's/^\s*\(server.*\)/# \1/' "$CONF_PATH"
sed -i 's/^\s*\(pool.*\)/# \1/' "$CONF_PATH"

# Разрешение безлимитного прыжка времени назад
if grep -q "makestep" "$CONF_PATH"; then
    sed -i 's/^\s*makestep.*/makestep 1 -1/' "$CONF_PATH"
else
    echo "makestep 1 -1" >> "$CONF_PATH"
fi

# Добавление сервера времени
echo "server 10.0.0.254 prefer iburst" >> "$CONF_PATH"

systemctl restart chrony
```

## Принудительное обновление времени
```
chronyc -a makestep
```

# Альтернативный вариант

Выполнить на хосте
```
sudo -i
apt install chrony
# Останавливаем системный chrony
sudo systemctl stop chrony
# Полностью отключаем его автозапуск при загрузке системы
sudo systemctl disable chrony

sudo cat <<EOF > /etc/chrony/chrony-fake.conf
local stratum 10
allow 10.10.0.0/24
bindaddress 10.10.0.253
port 123
makestep 1 -1
EOF

# Запуск в изолированном пространстве имен времени с фейковым временем
unshare --time --fork bash -c "date -s '2020-01-01 12:00:00' && chronyd -d -f /etc/chrony/chrony-fake.conf"
```

Выполнить в виртуальной машине
```
sudo -i
apt install chrony
# Закомментировать в /etc/chrony/chrony.conf все pool и server
echo "server 10.10.0.253 prefer iburst" >> /etc/chrony/chrony.conf

systemctl restart chrony
chronyc -a makestep

```
