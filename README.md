# Подготовительные действия

Создание образов дисков
```
qemu-img create -f qcow2 arm-abi.qcow2 16G
qemu-img create -f qcow2 srv-szi.qcow2 32G
```

Настройка пула
```
# Создать каталог пула
mkdir -p ~/.local/share/libvirt/images"
# Определить и создать пул "default", указывающий на стандартную домашнюю папку ВМ
virsh -с qemu:///session pool-define-as default dir --target ~/.local/share/libvirt/images
# Активировать пул
virsh -с qemu:///session pool-start default
# Включить автозапуск пула при каждом старте сессии
virsh -с qemu:///session pool-autostart default
# Настроить конфиг для запуска ВМ в сессии пользователя
echo 'security_driver = "none"' >> ~/.config/libvirt/qemu.conf
```

Создание сети
```
# Зарегистрировать сеть intnet в системной сессии libvirt
echo "<network><name>intnet</name><bridge name='virbr0' stp='on' delay='0'/></network>" | sudo virsh net-define /dev/stdin
# Включить автоматический запуск сети при загрузке ПК
sudo virsh net-autostart intnet
# Запустить сеть прямо сейчас
sudo virsh net-start intnet
```

Настройка разрешения на подключение машин в пользовательской сессии, к мостам virbr0 и virbr1
```
sudo mkdir -p /etc/qemu
echo "allow virbr0" | sudo tee -a /etc/qemu/bridge.conf
echo "allow virbr1" | sudo tee -a /etc/qemu/bridge.conf
sudo chmod 4755 /usr/lib/qemu/qemu-bridge-helper
```

## Создание виртуальных машин
```
# Если нужно настраивать шаблон, то
domain_name=test envsubst < arm-abi.xml | virsh -c qemu:///session define /dev/stdin
# Если настройка не нужна, то
cat arm-abi.xml | virsh -c qemu:///session define /dev/stdin
# или
virsh -c qemu:///session define <(cat arm-abi.xml)
```

## Удаление виртуальных машин
```
# Принудительно остановить все запущенные машины в сессии
virsh -c qemu:///session list --name | xargs -I {} virsh -c qemu:///session destroy "{}"

# Удалить регистрацию (конфигурацию) всех машин
virsh -c qemu:///session list --all --name | xargs -I {} virsh -c qemu:///session undefine "{}" --nvram
```

# Инструкция по созданию образа ВМ "Рубикон"
ВМ "Рубикон" использует образ, поставляемый разработчиком.

# Инструкция по созданию образа ВМ "АРМ-О"

Установку ОС "Astra Linux SE" выполняют с настройками по умолчанию, при выборе компонентов отмечают:
- Консольные утилиты
- Средство удаленного управления ssh

Статический адрес интерфейса eth0 настраивают, выполнив команду
```bash
cat << EOF >> /etc/network/interfaces
auto eth0
iface eth0 inet static
    address 10.0.0.3
    netmask 255.255.255.0
    gateway 10.0.0.1
EOF
```

Отключают неиспользуемые службы, выполнив команду
```bash
sistemctl disable avahi-daemon cups parsec-kiosk2 ufw wpa_supplicant
```

Для устранения ошибки "No module named pkg_resources" при запуске службы `astra-event-diagnostics-healthcheck.service` устанавливают пакет `python3-pkg-resources`, выполнив команду
```bash
apt install python3-pkg-resources
```

Устанавливают docker, выполнив команду
```bash
apt install docker.io docker-compose
usermod -aG docker administrator
```

Отключают проверки уязвимостей OpenSCAP в Docker, выполнив команду
```bash
mkdir -p /etc/docker
cat << EOF > /etc/docker/daemon.json
{
  "astra-sec-level": 6
}
EOF
systemctl restart docker
```

Загружают образ `heywoodlh/vulnerable`, выполнив команду
```
docker pull heywoodlh/vulnerable
```
> Сведения о загруженном образе можно получить, выполнив команду `docker image inspect heywoodlh/vulnerable`


Сохраняют загруженный образ в архив, выполнив команду
```
docker save heywoodlh/vulnerable | gzip > vulhub_metasploitable3.tar.gz
```
> Размер полученного архива 1109205130 байт

Если загрузка образа выполнялась на другой ЭВМ, устанавливают предварительно скачанный и загруженный в архив образ `heywoodlh/vulnerable`, выполнив команду
```bash
mount /dev/sr0 /media/cdrom
docker load -i /media/cdrom/vulhub_metasploitable3.tar.gz
```

Создают файл конфигурации (манифест) контейнера
>TODO Заменить docker-compose.yml на compose.yaml и проверить в Астре
```
mkdir -p /opt/vulhub/metasploitable3
cat << EOF > /opt/vulhub/metasploitable3/docker-compose.yml
version: '3.3'

services:
  vulnerable:
    image: heywoodlh/vulnerable
    container_name: metasploitable3
    stdin_open: true # Оставить поток ввода открытым
    tty: true        # Выделить псевдотерминал
    ports:
      - "21:21"      # FTP
      - "2222:22"    # SSH
      - "80:80"      # HTTP
      - "445:445"    # SMB
      - "631:631"    # CUPS
      - "3000:3000"  # Web App / Node
      - "3500:3500"  # App Port
      - "6697:6697"  # IRC SSL
      - "3306:3306"  # MySQL
      - "8181:8181"  # Web Management / Proxy
EOF
```
> Согласно актуальной спецификации Docker Compose, правильными считаются следующие имена (в порядке приоритета, в котором их ищет Docker):
compose.yaml — современный стандарт, рекомендованный Docker.
compose.yml
docker-compose.yaml — классический вариант, который использовался годами и до сих пор остается самым популярным.
docker-compose.yml

Выполняют тестовый запуск контейнера, выполнив команду
```bash
sudo docker-compose -f /opt/vulhub/metasploitable3/docker-compose.yml up -d
docker ps
# В выводе должна быть строка со сведениями о запущенном контейнере
ef371694d2f3   heywoodlh/vulnerable   "/usr/bin/supervisord"   16 minutes ago   Up 26 seconds   0.0.0.0:21->21/tcp, :::21->21/tcp, 0.0.0.0:80->80/tcp, :::80->80/tcp, 0.0.0.0:445->445/tcp, :::445->445/tcp, 0.0.0.0:631->631/tcp, :::631->631/tcp, 0.0.0.0:3000->3000/tcp, :::3000->3000/tcp, 0.0.0.0:3306->3306/tcp, :::3306->3306/tcp, 0.0.0.0:3500->3500/tcp, :::3500->3500/tcp, 0.0.0.0:6697->6697/tcp, :::6697->6697/tcp, 0.0.0.0:8181->8181/tcp, :::8181->8181/tcp, 0.0.0.0:2222->22/tcp, :::2222->22/tcp   metasploitable3
```

Создают юнит автозапуска контейнера metasploitable3, выполнив команду
```bash
cat << EOF > /etc/systemd/system/metasploitable3.service
[Unit]
Description=Metasploitable3 Container
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/vulhub/metasploitable3

# Запуск контейнера из готового локального образа (--no-build)
ExecStart=/usr/bin/docker-compose up -d --no-build

# Остановка (stop) с сохранением состояния контейнера при выключении или перезагрузке
ExecStop=/usr/bin/docker-compose stop

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now metasploitable3.service
```

# Инструкция по созданию образа ВМ "АРМ управления СКЗИ"

Создают чистый образ диска
```bash
qemu-img create -f qcow2 /var/lib/libvirt/images/arm-ipc.qcow2 16G
```

Запускают установку Windows командой
```bash
qemu-system-x86_64 \
  -name arm-ipc \
  -m 4096 \
  -smp 2 \
  -enable-kvm \
  -cpu host \
  -machine q35 \
  -rtc base=localtime,clock=host \
  -vga virtio \
  -vnc 0.0.0.0:4 \
  -device qemu-xhci,id=usb \
  -device usb-tablet,bus=usb.0 \
  -device usb-kbd,bus=usb.0 \
  -drive file=/var/lib/libvirt/images/arm-ipc.qcow2,format=qcow2,cache=writeback \
  -drive file=/var/lib/libvirt/images/en-us_windows_10_iot_enterprise_ltsc_2021_x64_dvd_257ad90f.iso,media=cdrom,readonly=on \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,mac=52:54:00:12:34:56 \
  -monitor unix:/tmp/win10-monitor.sock,server,nowait \
  -boot order=d \
  -daemonize
```
Подключаются любым клиентом vnc по адресу `localhost:5904`. При установке указывают имя пользователя `admin` и пароль `11111111`, при выборе параметров приватности отключают все переключатели.

Размер образа после установки 9,455,730,688 байт

С указанными настройками внутри запущенного процесса qemu-system-x86_64 эмулируются DHCP-сервер и интерфейс с адресом 10.0.2.2.

Добавление русской раскладки клавиатуры

Откройте Settings (Параметры) через меню Пуск или комбинацией Win + I.
Перейдите в раздел Time & Language (Время и язык) - Language (Язык).
В секции Preferred languages нажмите Add a language (Добавить язык).
Найдите Russian (Русский), выберите его и нажмите Next - Install.
(Это добавит русскую раскладку. Переключение клавиатуры: Alt + Shift или Win + Space).

На хосте в каталоге с установочными файлами программы управления ЦУС "Континент" запускают http-сервер, выполнив команду
```bash
python3 -m http.server 8000
```

В виртуальной машине открывают браузер, вводят адрес 10.0.2.2:8000 и скачивают установочные файлы.

Устанавливают *.msi файл


Извлечь диск

```bash
# Посмотреть список блочных устройств, определить в нем привод с подключенным установочным образом windows, например, ide1-cd0
echo "info block" | nc -q 0 -U /tmp/win10-monitor.sock; echo
echo "eject ide1-cd0" | nc -q 0 -U /tmp/win10-monitor.sock
```

Смена диска
```bash
echo "change cdrom0 /var/lib/libvirt/images/новое_имя.iso" | nc -q 0 -U /tmp/win10-monitor.sock
```

Принудительное выключение машины
```bash
pkill -9 -f "qemu-system-x86_64.*arm-ipc"
```

# Инструкция по созданию образа ВМ "КШ с ЦУС" (ipc-master) и "КШ" (ipc-slave)

Перед запуском установки создадим два файла в вашей рабочей директории (например, /var/lib/libvirt/images/):


Образ виртуального USB-носителя (raw) создают, выполнив команду
```bash
qemu-img create -f raw /var/lib/libvirt/images/usb.raw 64M
# FAT32 рассчитана на тома от 512 МБ (хотя mkfs.vfat позволяет принудительно отформатировать 64 МБ). Если ВМ откажутся читать такой диск, при пересоздании указать FAT16 (mkfs.vfat -F 16), которая идеально подходит для маленьких объемов
mkfs.vfat -F 32 /var/lib/libvirt/images/usb.raw
```

## Образ виртуального диска ЦУС "АПКШ Континент" создают в следующем порядке

Чистый образ диска (ipc-master.qcow2) создают, выполнив команду
```bash
qemu-img create -f qcow2 /var/lib/libvirt/images/ipc-master.qcow2 2G
```

Или без использования libvirt и virsh

```bash
qemu-system-x86_64 \
  -name ipc-master \
  -m 512 \
  -smp 1 \
  -enable-kvm \
  -cpu host \
  -machine q35 \
  -device qemu-xhci,id=usb \
  -drive file=/var/lib/libvirt/images/ipc-master.qcow2,if=none,id=disk0,format=qcow2,cache=writeback \
  -device ide-hd,drive=disk0,bus=ide.1 \
  -drive file=/var/lib/libvirt/images/fw_3.9.1.2732_out_fsb_disk1.iso,media=cdrom,readonly=on \
  -drive file=/var/lib/libvirt/images/usb.raw,if=none,id=usbdisk,format=raw \
  -device usb-storage,bus=usb.0,drive=usbdisk,removable=on \
  -netdev user,id=net0 -device e1000,netdev=net0,mac=08:00:27:3e:be:f1 \
  -netdev user,id=net1 -device e1000,netdev=net1,mac=08:00:27:3e:be:f2 \
  -vnc 0.0.0.0:3 \
  -vga virtio \
  -serial pty \
  -boot order=cd \
  -no-reboot \
  -daemonize
```

Вариант с установкой в консоли (через UNIX-сокет, вместо pty)
>NB! Установка с CD-диска проходит нормально, а при последующей перезагрузке инициализируется ядро FreeBSD, которое переключает вывод на vidconsole и дальнейшая работа становится невозможной, так как в консоли не отображаются запросы системы к пользователю. 

Запускают виртуальную машину `ipc-master`
```bash
qemu-system-x86_64 \
  -name ipc-master \
  -m 512 \
  -smp 1 \
  -enable-kvm \
  -cpu host \
  -machine q35 \
  -drive file=/var/lib/libvirt/images/ipc-master.qcow2,if=none,id=disk0,format=qcow2,cache=writeback \
  -device ide-hd,drive=disk0,bus=ide.1 \
  -drive file=/var/lib/libvirt/images/fw_3.9.1.2732_out_fsb_disk1.iso,media=cdrom,readonly=on \
  -netdev user,id=net0 -device e1000,netdev=net0,mac=08:00:27:3e:be:f1 \
  -netdev user,id=net1 -device e1000,netdev=net1,mac=08:00:27:3e:be:f2 \
  -serial unix:/tmp/ipc.sock,server,nowait \
  -display none \
  -boot order=d \
  -no-reboot \
  -daemonize;
nc -U /tmp/ipc.sock
```

ждут загрузки

на запрос
```
Отсутствует электронный замок Соболь
Дальнейшая работа будет производиться без него
 продолжить? (y/n):
```
нажимают клавиши "y" и Enter

На запрос
```
Выберите вариант установки:

1: Шлюз
2: Шлюз с сервером доступа
3: ЦУС
4: ЦУС с сервером доступа
5: ДА
6: АРМ генерации ключей
7: Коммутатор

Введите номер варианта [1..7]:
```
нажимают клавиши "3" и Enter

На запрос
```
Выберите действие:

1: Установка
2: Восстановление
3: Тестирование

Введите номер варианта [1..3]:
```
нажимают клавиши "1" и Enter

На запрос
```
      Установка << Континент >>

 продолжить? (y/n):
```
нажимают клавиши "y" и Enter

На запрос
```
***************   ВНИМАНИЕ!   ***************
ВСЕ данные на жестком диске будут БЕЗВОЗВРАТНО ПОТЕРЯНЫ!

 продолжить? (y/n):
```
нажимают клавиши "y" и Enter

На запрос
```
Введите идентификатор криптошлюза:
```
вводят идентификатор 1111 и нажимают клавишу Enter

На запрос выбора варианта аппаратной платформы
```
Выберите вариант аппаратной платформы:

1: IPC-10 (S088)
2: IPC-10 (LN-010A)
3: IPC-10 (S185)
4: IPC-25 (92D9)
5: IPC-25 (S115)
6: IPC-R10
7: IPC-50 (LN-010C)
8: IPC-100 (92E3)
9: IPC-100 (S102)
10: IPC-R50
11: IPC-400 (S021)
12: IPC-R300
13: IPC-R550
14: IPC-500 (LN-015B)
15: IPC-500F (LN-015C)
16: IPC-600 (DV-030A)
17: IPC-800F (DV-030B)
18: IPC-1000 (S021)
19: IPC-1000F (S021)
20: IPC-1000F2 (S021)
21: IPC-1000F (DV-031B)
22: IPC-1000NF2 (DV-031F)
23: IPC-3020 (S021)
24: IPC-3034 (S021)
25: IPC-3034F (S021)
26: IPC-3000F (S021)
27: IPC-3000F (LN-021)
28: IPC-3000FC (LN-021A)
29: IPC-3000NF2 (LN-021E)
30: IPC-VM

Введите номер варианта [1..30]:```
Выбирают вариант 6 и нажимают Enter

Ждут окончания установки

Итоговый размер образа ipc-master.qcow2 295239680 байт

## Образ виртуального диска КШ "АПКШ Континент" создают в следующем порядке

Чистый образ диска (ipc-master.qcow2) создают, выполнив команду
```bash
qemu-img create -f qcow2 /var/lib/libvirt/images/ipc-slave.qcow2 2G
```

```bash
qemu-system-x86_64 \
  -name ipc-master \
  -m 512 \
  -smp 1 \
  -enable-kvm \
  -cpu host \
  -machine q35 \
  -drive file=/var/lib/libvirt/images/ipc-slave.qcow2,if=none,id=disk0,format=qcow2,cache=writeback \
  -device ide-hd,drive=disk0,bus=ide.1 \
  -drive file=/var/lib/libvirt/images/fw_3.9.1.2732_out_fsb_disk1.iso,media=cdrom,readonly=on \
  -netdev user,id=net0 -device e1000,netdev=net0,mac=08:00:27:3e:be:f1 \
  -netdev user,id=net1 -device e1000,netdev=net1,mac=08:00:27:3e:be:f2 \
  -serial unix:/tmp/ipc.sock,server,nowait \
  -display none \
  -boot order=d \
  -no-reboot \
  -daemonize;
nc -U /tmp/ipc.sock
```

повторяют все действия аналогично созданию носителя ЦУС за исключением ответа на запрос выбора варианта установки
```
Выберите вариант установки:

1: Шлюз
2: Шлюз с сервером доступа
3: ЦУС
4: ЦУС с сервером доступа
5: ДА
6: АРМ генерации ключей
7: Коммутатор

Введите номер варианта [1..7]: 1
```
в котором выбирают вариант 1

Итоговый размер образа ipc-slave.qcow2 269090816 байт


Резюме:
отдельным диском в архив заложить
Disk "kontinent":
fw_3.9.1.2732_out_fsb_disk1.iso         2,868,590,592
ipc-master.qcow2                          295,239,680
ipc-slave.qcow2                           269,090,816
Data1.cab                                  66,532,989
ID_44091.lic                                      347
setup.exe                                 314,774,304
VersionInfo.txt                                    93
Континент. Подсистема управления.msi       87,327,744


rubicon.qcow2                           2,192,965,632

astra-1.7_x86-64 amd64.iso              3,851,223,040
main_update-1.7.6.15-15.11.24_17.20.iso 4,396,134,400



Принудительная остановка командой
```bash
pkill -9 -f "qemu-system-x86_64.*ipc-master"
```

# Инструкция по ручному развертыванию и использованию vlab

1. Создают каталог под правами root, выполнив команду
```bash
sudo mkdir -p /srv/ansible-vlab
```

2. Копируют туда файлы проекта, выполнив команду

3. Настраивают права доступа через общую группу
Обычно на таких серверах все администраторы уже входят в группу sudo или wheel
```bash
# Изменение владельца и прав
sudo chown -R root:sudo /srv/ansible-vlab
sudo chmod -R 770 /srv/ansible-vlab
```

4. Включают SGID-бит
Если один администратор создаст внутри проекта новый файл (например, новый плейбук или лог), по умолчанию владельцем файла станет его личная группа, и другие админы не смогут его отредактировать. Чтобы этого не произошло, выполните
```bash
sudo chmod g+s /srv/ansible-vlab
sudo find /srv/ansible-vlab -type d -exec chmod g+s {} +
```
Теперь любые новые файлы внутри этой папки будут автоматически наследоваться группой sudo, и вся команда сможет работать без багов с правами.

# Разное

## Монтирование образов на хосте
Чтобы примонтировать образ qcow2 на хосте Linux через сетевой блочный драйвер (NBD), выполняют следующие действия

Устанавливают необходимые утилиты
```bash
apt install qemu-utils nbd
```
Загружают модуль ядра NBD с поддержкой нескольких (max_part) устройств
```bash
modprobe nbd max_part=8
```
Подключают образ qcow2 к свободному блочному устройству (например, /dev/nbd0)
```bash
qemu-nbd --connect=/dev/nbd0 /path/to/disk.qcow2
```
Выводят разделы диска
```
lsblk /dev/nbd0
```
Создают точку монтирования и монтируют нужный раздел
```bash
mkdir -p /mnt/qcow2
mount /dev/nbd0p1 /mnt/qcow2
```

После использования примонтированного образа размонтируют образ и отключают сетевое блочное устройство
```bash
umount /mnt/qcow2
qemu-nbd --disconnect /dev/nbd0
```

Более простой и современный способ использовать утилиту guestmount из пакета libguestfs, которая не требует загрузки модулей ядра вроде NBD, работы от имени суперпользователя (sudo) и ручного поиска разделов.

Устанавливают необходимый пакет
```
apt install libguestfs-tools
```

Указывают какую именно операционную систему или раздел внутри qcow2 необходимо смонтировать, если внутри образа установлена стандартная ОС, утилита сама найдет корневой раздел
```
guestmount -a /path/to/disk.qcow2 -i --ro /mnt/qcow2
```
• -a — путь к файлу qcow2.
• -i — автоматический поиск ("инспекция") операционной системы внутри диска.
• --ro — монтирование в режиме только для чтения (рекомендуется для безопасности данных), если вам нужна запись, заменить на -w.

Если автоопределение не сработало, указать конкретный раздел (например, первый)
```
guestmount -a /path/to/disk.qcow2 -m /dev/sda1 --ro /mnt/qcow2
```

Когда работа с файлами завершена, отключите образ командой
```
guestunmount /mnt/qcow2
```

## Уменьшение размера образов
Файлы qcow2 имеют свойство разрастаться так как при удалени файлов внутри виртуальной машины, сам файл .qcow2 на хосте не уменьшается автоматически. Чтобы вернуть свободное место хосту, образ нужно сжать.
Перед сжатием необходимо заполнить дисковое пространство нулями. Это можно сделать выполнив в запущенной виртуальной машине команды
```
dd if=/dev/zero of=~/zero.file bs=1M status=progress
rm ~/zero.file
```
либо примонтировав образ на хосте через guestmount или nbd и выполнив команды
```
# Если примонтировано в /mnt/qcow2
dd if=/dev/zero of=/mnt/qcow2/zero.file bs=1M status=progress
rm /mnt/qcow2/zero.file
```
Затем размонтируют образ командой ```guestunmount /mnt/qcow2``` или ```qemu-nbd --disconnect```

Утилита  `qemu-img` пересоберет образ, проигнорирует нулевые сектора и применит сжатие
```
qemu-img convert -O qcow2 -c original.qcow2 shrunken.qcow2
```
• -O qcow2 — формат выходного файла.
• -c — флаг сжатия (compression).


## Использование qemu-guest-agent
Если будет использоваться qemu-guest-agent, то добавить в конфиг ВМ канал:
```
<channel type="unix">
    <target type="virtio" name="org.qemu.guest_agent.0"/>
    <address type="virtio-serial" controller="0" bus="0" port="1"/>
</channel>
```
На ВМ установить пакет `qemu-guest-agent` и включить службу `qemu-guest-agent`.

После этих действий можно выполнять команды, например, qemu-agent-command
```bash
virsh -c qemu:///system qemu-agent-command "$DOMAIN_NAME" '{"execute":"guest-get-time"}'
```
## Настройки параметров
NB! При запуске ВМ с дисками в домашнем каталоге в убунте нужно в файл `/etc/libvirt/qemu.conf` добавить параметр `security_driver = "none"`, как обстоит ситуация в Астре уточнить!

Чтобы при запуске графического интерфейса virt-manager сразу отображалось и автоматически открывалось подключение к пользовательской сессии, нужно настроить параметры virt-manager.
В Debian 13 настройка выполнена с помощью следующих команд
```bash
# Установить команду gsettings
sudo apt install libglib2.0-bin

# Задать список подключений
gsettings set org.virt-manager.virt-manager.connections uris "['qemu:///session']"

# Включить для qemu:///session автоматическое подключение при старте
gsettings set org.virt-manager.virt-manager.connections autoconnect "['qemu:///session']"

```

Чтобы постоянно не указывать сессию настроить переменную LIBVIRT_DEFAULT_URI
```bash
export LIBVIRT_DEFAULT_URI="qemu:///session"
```

# Техническое резюме для сборки пропатченного пакета libvirt  в Astra Linux SE

Корень проблемы «Конец файла при чтении данных / Ошибка ввода-вывода (EOF)» в сессии пользователя (qemu:///session) при использовании сетевого подключения типа "Мост" — принудительный сброс сетевых прилегий (Linux Capabilities). Из-за этого эмулятор QEMU теряет право передать дескриптор моста virbr0 в SUID-хелпер.

Тех. поддержка Астры сказала, что при сборке в 1.7 использовалась версия с [https://github.com/libvirt/libvirt/releases/tag/v10.5.0]
```
wget https://github.com/libvirt/libvirt/archive/refs/tags/v10.5.0.tar.gz
```

Было (файл /src/util/virutil.c):
```
    for (i = 0; i <= CAP_LAST_CAP; i++) {
        if (capBits & (1ULL << i)) {
            capng_update(CAPNG_ADD,
                         CAPNG_EFFECTIVE|CAPNG_INHERITABLE|
                         CAPNG_PERMITTED|CAPNG_BOUNDING_SET,
                         i);
        }
    }
```
Стало (файл /src/util/virutil.c):
```
for (i = 0; i <= CAP_LAST_CAP; i++) {
        if (capBits & (1ULL << i)) {
            capng_update(CAPNG_ADD,
                         CAPNG_EFFECTIVE|CAPNG_INHERITABLE|
                         CAPNG_PERMITTED|CAPNG_BOUNDING_SET,
                         i);
        }
    }

    /* Принудительно сохранить сетевые права для работы qemu-bridge-helper в сессии пользователя */
    capng_update(CAPNG_ADD, CAPNG_EFFECTIVE|CAPNG_PERMITTED|CAPNG_INHERITABLE|CAPNG_BOUNDING_SET, CAP_NET_ADMIN);
```

Порядок компиляции описан в [https://libvirt.org/compiling.html].
