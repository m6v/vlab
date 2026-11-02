# Установка зависимостей
apt install qemu-system-x86 qemu-utils
apt install virt-viewer --no-install-recommends --no-install-suggests

# Создание пула с базовами образами дисков и каталога с конфигурационными файлами виртуальных машин
```
mkdir -p /srv/qemu/{images,config}
cp src_dir/*.qcow2 /srv/qemu/imаges
cp src_dir/*.conf /srv/qemu/config
```

# Создание скелета домашнего каталога пользователей
```
# /etc/skel/Desktop - ярлыки запуска виртуальных машин

SKEL_POOL=/etc/skel/.local/share/qemu/images
mkdir -p $SKEL_POOL

# Создание пустых дисков
qemu-img create -f qcow2 $SKEL_POOL/arm-abi.qcow2 16G
qemu-img create -f qcow2 $SKEL_POOL/srv-szi.qcow2 32G
qemu-img create -f qcow2 $SKEL_POOL/backup.qcow2 32G

# Создание оверлейных дисков
SYSTEM_POOL=/src/qemu/images
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
cp src_dir/vmctrl /usr/local/bin
```

# Настройка сети хоста

## Создание и настройка моста br0 (10.10.0.253/24)
```
# Создание моста br0, если отсутствует
if ! nmcli connection show br0 >/dev/null 2>&1; then
    nmcli connection add type bridge con-name br0 ifname br0 ipv4.method manual ipv4.addresses 10.10.0.1/24
fi

# Установка параметров моста
nmcli connection modify br0 bridge.stp no ipv4.method manual ipv4.addresses 10.10.0.1/24

# Поднятие моста, если не активен
if ! nmcli -t -f DEVICE connection show --active | grep -q "^br0$"; then
    nmcli connection up br0
fi


```
## Создание и настройка моста br1 (10.0.0.253/24)
```
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
