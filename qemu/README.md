# Создание скелета
```

# /etc/skel/Desktop - ярлыки запуска виртуальных машин

SKEL_POOL=/etc/skel/.local/share/qemu/images
mkdir -p $SKEL_POOL

# Создание пустых дисков
qemu-img create -f qcow2 $SKEL_POOL/arm-abi.qcow2 16G
qemu-img create -f qcow2 $SKEL_POOL/srv-szi.qcow2 32G
qemu-img create -f qcow2 $SKEL_POOL/backup.qcow2 32G

SYSTEM_POOL=/var/lib/libvirt/images
# Создание оверлейных дисков
qemu-img create -f qcow2 -F qcow2 -b $SYSTEM_POOL/arm-ipc.qcow2 $SKEL_POOL/arm-ipc.qcow2
qemu-img create -f qcow2 -F qcow2 -b $SYSTEM_POOL/arm-o.qcow2 $SKEL_POOL/arm-o.qcow2
qemu-img create -f qcow2 -F qcow2 -b $SYSTEM_POOL/ipc-master.qcow2 $SKEL_POOL/ipc-master.qcow2
qemu-img create -f qcow2 -F qcow2 -b $SYSTEM_POOL/ipc-slave.qcow2 $SKEL_POOL/ipc-slave.qcow2
qemu-img create -f qcow2 -F qcow2 -b $SYSTEM_POOL/rubicon.qcow2 $SKEL_POOL/rubicon.qcow2
```

Если скелет нужно применить к уже существующему пользователю, в сессии пользователя используют команду
```
sudo rsync -av --ignore-existing /etc/skel/ /home/$USER/ && sudo chown -R $USER:$USER /home/$USER/
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

# Структура каталогов
```
/srv
  /qemu
    /config # шаблоны конфигурационных файлов виртуальных машин
    /images # базовые qcow2-образы и iso-образы Astra Linux
    /usr/share/spice-html5 # Spice Javascript client
/usr/local/bin/vmctrl # Программа управления виртуальными машинами
```
