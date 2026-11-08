#!/usr/bin/env python3
import socket
import struct
import time
import argparse
import logging
import sys
import signal
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Смещение эпох 1900 -> 1970
NTP_DELTA = 2208988800

def parse_args():
    """Настройка и парсинг аргументов командной строки"""
    parser = argparse.ArgumentParser(description="NTP-сервер для УТК-СЗИ")
    parser.add_argument(
        "--addr", 
        default="10.10.0.253:123", 
        help="Адрес и порт для прослушивания в формате IP:PORT (по умолчанию: 10.10.0.253:123)"
    )
    parser.add_argument(
        "--offset", 
        type=float, 
        required=True, 
        help="Смещение времени относительно хоста в секундах (например, -216268800)"
    )
    return parser.parse_args()

def parse_address(addr_str):
    """Разбор строки адреса на IP и порт"""
    if ":" in addr_str:
        try:
            bind_ip, port_str = addr_str.rsplit(":", 1)
            return bind_ip, int(port_str)
        except ValueError:
            logging.error("Неверный формат порта в адресе: %s", addr_str)
            sys.exit(1)
    else:
        return addr_str, 123

def setup_socket(bind_ip, bind_port):
    """Создание и конфигурирование UDP сокета."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((bind_ip, bind_port))
        return s
    except PermissionError:
        logging.error("Недостаточно прав для прослушивания порта %d (запустите через sudo)", bind_port)
        sys.exit(1)
    except Exception as e:
        logging.error("Не удалось привязать сокет к %s:%d: %s", bind_ip, bind_port, e)
        sys.exit(1)

def main():
    args = parse_args()
    bind_ip, bind_port = parse_address(args.addr)
    
    start_monotonic = time.monotonic()
    base_fake_unix = time.time() + args.offset

    # Форматирование отображения смещения для лога
    offset_str = f"+{args.offset}" if args.offset > 0 else str(args.offset)

    logging.info("Запуск Timemachine NTP-сервера на %s:%d...", bind_ip, bind_port)
    logging.info("Установлено смещение относительно времени хоста %s сек.", offset_str)

    s = setup_socket(bind_ip, bind_port)

    # Обработка системных сигналов для корректного завершения процесса
    def shutdown_handler(signum, frame):
        logging.info("Получен сигнал %d. Сервер останавливается...", signum)
        s.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Подготовка константной части NTP-пакета (48 байт)
    response = bytearray(48)
    struct.pack_into("!BBbb", response, 0, 0x24, 2, 0, -20)
    struct.pack_into("!I", response, 4, 65)  # Root Delay
    struct.pack_into("!I", response, 8, 65)  # Root Dispersion
    response[12:16] = b'LOCL'                # Reference Identifier

    logging.info("Сервер готов к приему запросов.")

    while True:
        try:
            data, addr = s.recvfrom(1024)
            if len(data) < 48:
                continue
            
            # Атомарный расчет времени на основе монотонных часов хоста
            elapsed = time.monotonic() - start_monotonic
            current_fake_unix = base_fake_unix + elapsed
            
            ntp_time_raw = current_fake_unix + NTP_DELTA
            ntp_secs = int(ntp_time_raw)
            ntp_frac = int((ntp_time_raw - ntp_secs) * 4294967296)
            
            ref_secs = ntp_secs - 1
            ref_frac = ntp_frac

            # Сборка динамических полей времени
            struct.pack_into("!II", response, 16, ref_secs, ref_frac)
            # Копирование Originate Timestamp
            response[24:32] = data[40:48]
            struct.pack_into("!II", response, 32, ntp_secs, ntp_frac)
            struct.pack_into("!II", response, 40, ntp_secs, ntp_frac)
            
            s.sendto(response, addr)

            client_ip, client_port = addr
            sent_date_str = datetime.fromtimestamp(current_fake_unix).strftime('%Y-%m-%d %H:%M:%S')
            logging.info("Ответ с временем %s отправлен на %s:%d", sent_date_str, client_ip, client_port)
            
        except socket.error:
            # Игнорование сетевых аномалий при работе с tap-интерфейсами
            pass
        except Exception as e:
            logging.error("Непредвиденная ошибка в основном цикле: %s", e)

if __name__ == "__main__":
    main()
