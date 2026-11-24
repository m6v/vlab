#!/usr/bin/bash
# Поис и (пере)установка всех deb-пакетов в текущем каталоге и подкаталогах
find . -type f -name "*.deb" -print0 | xargs -0 -o sudo apt install --reinstall
# Установка зависимым пакетам статуса 'auto' для автоматического удаления вместе с метапакетом
dpkg-deb -f ./vlab-full*.deb Depends | tr ',' '\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]].*//' | xargs sudo apt-mark auto

# Установка стороннего ПО
tar -xzf 3rdparty/noVNC-*.tar.gz -C /usr/share
ln -s /usr/share/noVNC-1.7.0 /usr/share/novnc
tar -xzf 3rdparty/websockets-*.tar.gz -C /usr/local/lib/python3.7/dist-packages/ --strip-components=2 --wildcards "*/src/websockets"
