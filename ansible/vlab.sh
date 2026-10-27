#!/usr/bin/env bash

set -euo pipefail

# Определение реального каталога скрипта
SCRIPT_DIR=$(dirname "$(readlink -f '$0')")
echo $SCRIPT_DIR
# Перейти реальный каталог скрипта перед выполнением остального кода
cd "$SCRIPT_DIR" || exit 1

TITLE="Панель управления рабочими станциями УТК-СЗИ"
INVENTORY_FILE="hosts"
HINT="Управление: [Стрелки] - движение, [Tab] - кнопки, [Enter] - выбор"

# Перезапуск от root, если скрипт запущен от обычного пользователя
if [ "$(id -u)" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

if ! command -v ansible &> /dev/null; then
    echo "Установка Ansible..."
    sudo apt-get update
    sudo apt-get install -y ansible
fi

# Проверка наличия файла инвентаря
if [ ! -f "$INVENTORY_FILE" ]; then
    whiptail --title "Ошибка" --msgbox "Файл инвентаря '$INVENTORY_FILE' не найден!" 8 55
    exit 1
fi

# Список плейбуков с индексами, соответствующими пунктам меню
# Нулевой индекс пропускается, так как меню начинается с 1
PLAYBOOKS=(
    ""
    "site.yml"
    "create_user.yml"
    "define_user_pool.yml"
    "destroy_user_pool.yml"
    "delete_user.yml"
    "get_users.yml"
)


# Функция отображения меню выбора хостов из инвентаря (тип меню в первом аргументе)
select_host_from_inventory() {
    # По умолчанию использовать тип меню --radiolist
    local menu_type=${1:-}
    if [[ "$menu_type" != "--checklist" ]]; then
        menu_type="--radiolist"
    fi

    local menu_options=()
    # Замена палитры элементов выбора
    export NEWT_COLORS="checkbox=black,lightgray"

    while read -r name ip; do
        [ -n "$name" ] && [ -n "$ip" ] && menu_options+=("$name" "$ip" "off")
    done < <(awk -F'[ =]' '$2 == "ansible_host" { print $1, $3 }' "$INVENTORY_FILE")

    hosts=$(whiptail --title "$TITLE" \
                     --backtitle "$HINT" \
                     --ok-button "Выбрать" \
                     --cancel-button "Назад" \
                     $menu_type "Выберите целевые хосты:" \
                     18 65 10 \
                     "${menu_options[@]}" \
                     3>&1 1>&2 2>&3)  || return 1
    # Удаление кавычек и замена пробелов на запятые
    hosts=$(echo "$hosts" | tr -d '"' | tr ' ' ',')
    [ -z "$hosts" ] && return 1
    return 0
}

# Функция подготовки аргументов и запуска ansible-playbook
run_ansible() {
    local playbook="$1"
    shift 1

    local user=""
    local hosts=""
    # Разбор флагов с помощью getopts, -u (требует аргумент) и -l (требует аргумент)
    local opt
    # Обязательный сброс индекса для getopts перед каждым вызовом
    OPTIND=1
    while getopts "u:l:" opt; do
        case "$opt" in
            u) user="$OPTARG" ;;
            l) hosts="$OPTARG" ;;
            *) ;; # Пропуск неизвестных флагов
        esac
    done

    clear

    # Сборка аргументов в массив Ansible
    local ansible_args=()
    ansible_args+=("-e" "ansible_user=$ansible_user")
    ansible_args+=("-e" "ansible_password=$ansible_password")
    ansible_args+=("-e" "ansible_sudo_pass=$ansible_password")

    # Добавление аргумента user, если $user не пустая
    if [ -n "$user" ]; then
        ansible_args+=("-e" "user=$user")
    fi

    # Передача строки хостов в ограничение Ansible
    ansible_args+=("-l$hosts")

    # Запуск плейбука
    ansible-playbook "$playbook" "${ansible_args[@]}" || true

    read -n 1 -s -r -p "Нажмите любую клавишу..."
    echo
}

# Функция запроса имени пользователя
prompt_user_name() {
    # Если указан первый аргумент, использовать его как дефолтный логин
    user_name="${1:-}"
    while true; do
        if user_name=$(whiptail --title "$TITLE" \
            --backtitle "$HINT" \
            --ok-button "Ввод" \
            --cancel-button "Отмена" \
            --inputbox "\nВведите логин пользователя:" 10 55 "$user_name" \
            3>&1 1>&2 2>&3); then
            # Обработка нажатия "Ввод"
            [ -n "$user_name" ] && return 0
            # Уход на новую итерацию, если ввод пустой
            user_name=""
        else
            # Обработка нажатия "Отмена" или "Esc"
            return 1
        fi
    done
}

prompt_user_pass() {
    user_pass=$(whiptail --title "$TITLE" \
        --backtitle "$HINT" \
        --ok-button "Далее" \
        --cancel-button "Отмена" \
        --passwordbox "\nВведите пароль пользователя:" 10 55 \
        3>&1 1>&2 2>&3) || return 1
    # [ -z "$user_pass" ] && return 1 || return 0
    return 0
}

# Найти учетные записи, входящие в группу astra-admin или sudo
admins=$( (getent group 'astra-admin' || getent group 'sudo') | cut -d: -f4 )

# Найти администратора с наименьшим id
if [ -n "$admins" ]; then
    default_user="$(grep -E $admins /etc/passwd | sort -t: -k3,3n | head -n1 | cut -d: -f1)"
fi

# Вызвать диалог выбора пользователя
prompt_user_name "$default_user"
prompt_user_pass

ansible_user=$user_name
ansible_password=$user_pass

# Главный цикл панели управления
while true; do
    # Инициализация и принудительный сброс параметров перед каждым действием
    host=""
    user_name=""

    choice=$(whiptail --title "$TITLE" --backtitle "$HINT" --ok-button "Выбрать" --cancel-button "Выход" --menu "\nВыберите действие:" 15 65 7 \
        "1" "Инициализировать среду виртуализации" \
        "2" "Создать учетную запись пользователя" \
        "3" "Создать пул и виртуальные машины пользователя" \
        "4" "Удалить пул и виртуальные машины пользователя" \
        "5" "Удалить учетную запись пользователя" \
        "6" "Показать список пользователей" \
        "7" "Показать справку" 3>&1 1>&2 2>&3)

    if [ $? -ne 0 ] || [ -z "$choice" ]; then
        clear
        exit 0
    fi

    if [ "$choice" -eq 7 ]; then
        # Проверка наличия файла справки в текущем каталоге
        if [ -f "help.txt" ]; then
            whiptail --title "Справка" \
                     --backtitle "$HINT" \
                     --scrolltext \
                     --textbox "help.txt" 20 75
        else
            whiptail --title "Ошибка" \
                     --backtitle "$HINT" \
                     --msgbox "Файл help.txt не найден в текущем каталоге!" 8 50
        fi
        continue
    fi

    # Чтение имени плейбука по индексу выбранного пункта меню
    playbook="${PLAYBOOKS[$choice]}"

    # Определение типа меню выбора хоста и необходимости выбора имени пользователя
    case "$choice" in
        "2"|"3"|"4"|"5")
            menu_type="--radiolist"
            is_user_required=true
            ;;
        *)
            menu_type="--checklist"
            is_user_required=false
            ;;
    esac

    # Если хост(ы) не выбран(ы) переход в главное меню
    select_host_from_inventory $menu_type || continue
    # Вызов диалога с запросом имени пользователя, если оно требуется
    [ "$is_user_required" = true ] && { prompt_user_name || continue; }

    # Индивидуальное исключение для подтверждения удаления пользователя
    if [ "$choice" -eq 5 ]; then
        whiptail --title "Подтверждение" --backtitle "$HINT" --ok-button "Да" --cancel-button "Нет" --yesno "Удалить пользователя '$user_name' на '$hosts'?" 10 60 || continue
    fi
    # Вызов функции-обертки для запуска плейбука $playbook над хостами $hosts
    run_ansible "$playbook" -u "$user_name" -l "$hosts"
done
