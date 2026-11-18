#!/usr/bin/bash

find . -type f -name "*.deb" -print0 | xargs -0 -o sudo apt install --reinstall
# Установка зависимым пакетам статуса 'auto' для автоматического удаления вместе с метапакетом
dpkg-deb -f ./vlab-full*.deb Depends | tr ',' '\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]].*//' | xargs sudo apt-mark auto
