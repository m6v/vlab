#!/usr/bin/bash



# Создание моста br0, если отсутствует
if ! nmcli connection show br0 >/dev/null 2>&1; then
    nmcli connection add type bridge con-name br0 ifname br0 ipv4.method manual ipv4.addresses 10.10.0.253/24
fi

# Установка параметров моста
nmcli connection modify br0 bridge.stp no ipv4.method manual ipv4.addresses 10.10.0.253/24

# Поднятие моста, если не активен
if ! nmcli -t -f DEVICE connection show --active | grep -q "^br0$"; then
    nmcli connection up br0
fi


# Создание моста br1, если отсутствует
if ! nmcli connection show br1 >/dev/null 2>&1; then
    nmcli connection add type bridge con-name br1 ifname br1 ipv4.method manual ipv4.addresses 10.0.0.253/24
fi

# Установка параметров моста
nmcli connection modify br1 bridge.stp no ipv4.method manual ipv4.addresses 10.0.0.253/24

# Поднятие моста, если не активен
if ! nmcli -t -f DEVICE connection show --active | grep -q "^br1$"; then
    nmcli connection up br1
fi


# Настройка хелпера (qemu-bridge-helper)
chown root:root /usr/lib/qemu/qemu-bridge-helper
chmod 4755 /usr/lib/qemu/qemu-bridge-helper

mkdir -p /etc/qemu
cat << EOF > /etc/qemu/bridge.conf
allow br0
allow br1
EOF
chmod 0644 /etc/qemu/bridge.conf
