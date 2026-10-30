# Создание скелета
```
mkdir -p /etc/skel/.local/share/qemu/images

# /etc/skel/.local/share/qemu - конфиги виртуальных машин
# /etc/skel/Desktop - ярлыки запуска виртуальных машин

cd /etc/skel/.local/share/qemu/images
# Создание пустых дисков
qemu-img create -f qcow2 arm-abi.qcow2 16G
qemu-img create -f qcow2 srv-szi.qcow2 32G
qemu-img create -f qcow2 backup.qcow2 32G

POOL=/var/lib/libvirt/images
# Создание оверлейных дисков
qemu-img create -f qcow2 -F qcow2 -b $POOL/arm-ipc.qcow2 arm-ipc.qcow2
qemu-img create -f qcow2 -F qcow2 -b $POOL/arm-o.qcow2 arm-o.qcow2
qemu-img create -f qcow2 -F qcow2 -b $POOL/ipc-master.qcow2 ipc-master.qcow2
qemu-img create -f qcow2 -F qcow2 -b $POOL/ipc-slave.qcow2 ipc-slave.qcow2
qemu-img create -f qcow2 -F qcow2 -b $POOL/rubicon.qcow2 rubicon.qcow2
```

# Копирование скрипта управления
```
cp vmctrl /usr/local/bin
```

# Настройка сети на хосте

## Создание и настройка моста br0 (10.10.0.1/24)
```
nmcli connection add type bridge con-name br0 ifname br0 ipv4.method manual ipv4.addresses 10.10.0.1/24
nmcli connection modify br0 bridge.stp no
nmcli connection up br0
```

## Создание и настройка моста br1 (10.0.0.1/24)
```
nmcli connection add type bridge con-name br1 ifname br1 ipv4.method manual ipv4.addresses 10.0.0.1/24
nmcli connection modify br1 bridge.stp no
nmcli connection up br1
```

## Настройка хелпера (qemu-bridge-helper)
```
chown root:root /usr/lib/qemu/qemu-bridge-helper
chmod 4755 /usr/lib/qemu/qemu-bridge-helper

mkdir -p /etc/qemu
cat << EOF > /etc/qemu/bridge.conf
allow br0
allow br1
EOF
chmod 0644 /etc/qemu/bridge.conf
```

Упаковать все в deb-пакет и отказаться от ansible
