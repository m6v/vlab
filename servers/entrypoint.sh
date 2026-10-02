#!/bin/bash

FAKETIME_FILE="/etc/faketime"

start_time=$(date +%s)
# Если смещенное время не задано, использовать текущее время
target_time=$(cat "$FAKETIME_FILE" 2>/dev/null || echo "$start_time")

# Настройка сети контейнера
echo "Configuring container network interface host0..."
/sbin/ip addr add 10.10.0.254/24 dev host0 2>/dev/null
/sbin/ip link set dev host0 up 2>/dev/null

on_exit() {
    echo "Stopping background processes..."
    # Отправка сигнала SIGTERM (15) для мягкого завершения python3 и chronyd
    kill $(jobs -p) 2>/dev/null

    # Ожидание завершения фоновых процессов (Graceful Shutdown) или таймаута
    # (90 секунд по дефолту или значения, установленного в TimeoutStopSec)
    wait

    # Здесь нельзя использовать внешние утилиты, поэтому импользуем флаг %(...)T,
    # который умеет брать текущее Unix-время напрямую из памяти самого процесса
    if [ -f "$FAKETIME_FILE" ]; then
        printf -v stop_time '%(%s)T' -1
        # Увеличение смещенного времени на длительность работы контейнера
        echo $(( target_time + stop_time - start_time )) > "$FAKETIME_FILE"
        sync
    fi
    exit 0
}
trap "on_exit" EXIT SIGTERM SIGINT

# Запуск веб-сервера для раздачи репозитория Astra Linux в виртуальные машины УТК-СЗИ
# В sources.list добавить "deb http://10.0.0.254/alse/main 1.7_x86-84 contrib main non-free"
echo "Starting HTTP repository server on port 80..."
python3 -m http.server --directory /srv/repo 80 &

# В deban ошибка /bin/busybox: 7: exec: lighttpd: not found, в Astra Linux работает,
# но без автогенерации листинга директорий (Directory Listing), для apt этого достаточно
# echo "Starting BusyBox HTTP repository server on port 80..."
# /bin/busybox httpd -f -p 80 -h /srv/repo -c /dev/null &

# Запуск chronyd на переднем плане (-d) в фоне самого bash (&)
echo "Starting chronyd in debug mode with drift correction disabled..."
/usr/sbin/chronyd -d -x &

# Ожидание, чтобы chronyd успел инициализировать сокет контроля
sleep 1

# Установка смещенного времени
echo "Setting offset time to $(date -d "@$target_time" +"%Y-%m-%dT%H:%M:%S")..."
chronyc -a settime $(date -d "@$target_time" +"%Y-%m-%dT%H:%M:%S") 

# Удержание контейнера в запущенном состоянии
wait
