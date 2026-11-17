# Кастомный NTP-сервер с возможностью использования смещения и HTTP-сервер

Исходная реализация предполагала использование chrony внутри контейнера systemd-nspawn. Текущая реализация основана на кастомном мервере времени timemachine.py, который реализует протокол NTP и не требует каких-либо манипуляций с временем хоста.

## Структура проекта
```
├── etc
│   └── systemd
│       └── system
│           ├── vlab-httpd.service
│           └── vlab-timemachine.service
└── usr
    └── local
        └── bin
            └── timemachine.py

```
Смещение времени относительно текущего (в секундах) задается в команде запуска внутри юнита vlab-timemachine.service. Для доступа к серверам из контейнера виртуальному мосту должен быть присвоен ip-адрес, который указывают в аргументах запуска служб.

Нужное смещение времени можно вычислить, например, следующей командой
```bash
echo $(( $(date -d "2020-01-01 00:00:00" +%s) - $(date +%s) ))
```

## Настройка клиентов

### Настройка конфигурации
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

### Принудительное обновление времени
```
chronyc -a makestep
```
