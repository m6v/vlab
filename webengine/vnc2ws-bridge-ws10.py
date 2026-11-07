#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import argparse
from urllib.parse import parse_qs
import websockets
from websockets import serve as ws_serve

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Шаблон парсинга строк tokens.conf: Имя_ВМ:Локальный_IP:Порт_VNC
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')
TOKENS_PATH = ""

def get_target_addr(token):
    """
    Ищет IP и порт целевого VNC-сервера в файле tokens.conf по переданному токену.

    Args:
        token (str): Имя токена (имя ВМ), полученное из URL запроса.

    Returns:
        tuple (str, int) или None: Сетевые координаты бэкенда при успехе, иначе None.
    """
    try:
        with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                match = TOKEN_REGEXP.match(line)
                if match and match.group(1) == token:
                    return match.group(2), int(match.group(3))
    except Exception as e:
        logging.error(f"Ошибка чтения файла токенов: {e}")
    return None

async def ws_handler(websocket, path):
    """
    Главный асинхронный воркер WebSocket соединения (спецификация websockets v10.4).
    """
    current_path = websocket.path if (hasattr(websocket, 'path') and "?" in getattr(websocket, 'path', '')) else path
    
    # ИСПРАВЛЕНО: Безопасное строковое разделение через .partition('?').
    # Метод возвращает кортеж (до_знака, знак, после_знака). Нам нужна только часть ПОСЛЕ знака.
    _, separator, query_str = current_path.partition('?')
    
    # БЕЗОПАСНОЕ ИЗВЛЕЧЕНИЕ: Использование фолбэка [""] защищает индекс [0] от IndexError.
    # Текстовый маркер [0] прописан явно, метод .strip() применяется строго к строке.
    params = parse_qs(query_str)
    token_list = params.get('token', params.get('path', [""]))
    token = token_list[0].strip() if token_list and token_list[0] else None
    
    # ФОЛБЭК: Защита работы за корпоративными реверс-прокси
    if not token:
        forwarded_uri = websocket.request.headers.get('X-Forwarded-Uri', '')
        # Безопасно отсекаем путь, забирая только query-строку параметров
        _, _, fwd_query = forwarded_uri.partition('?')
        
        fwd_list = parse_qs(fwd_query).get('token', [""])
        token = fwd_list.strip()

    if not token:
        await websocket.close(1008, "Missing token")
        return

    vnc_target = get_target_addr(token)
    if not vnc_target:
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    logging.info(f"[{token}] Попытка подключения к VNC бэкенду {host}:{port}")
    try:
        # Устанавливаем сырой асинхронный TCP-линк с QEMU/KVM
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        logging.info(f"[{token}] Сетевой мост успешно скоммутирован")
    except Exception as e:
        logging.error(f"[{token}] Ошибка TCP подключения к VNC: {e}")
        return

    is_closed = False

    async def close_all():
        """Синхронное и безопасное закрытие всех открытых каналов связи сессии (TCP и WS)."""
        nonlocal is_closed
        if not is_closed:
            is_closed = True
            try: 
                writer.close()
                if hasattr(writer, 'wait_closed'): await writer.wait_closed()
            except Exception: pass
            try: await websocket.close()
            except Exception: pass
            logging.info(f"[{token}] Все транспортные каналы сессии штатно закрыты")

    async def keep_alive_pinger():
        """
        Внутренний серверный пингер. Отправляет пустые фреймы WebSocket каждые 15 секунд.

        ПРИМЕЧАНИЕ (ws10): В старой версии библиотеки websocket.ping() возвращает Future/Корутину.
        Мы обязаны дождаться выполнения через wait_for, чтобы вовремя обработать таймаут.
        """
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed: 
                    res = websocket.ping()
                    if asyncio.isfuture(res) or asyncio.iscoroutine(res):
                        await asyncio.wait_for(res, timeout=10.0)
        except Exception: await close_all()

    async def pipeline_browser_to_vm():
        """Входящий пайплайн передачи данных: Браузер (Клавиатура/Мышь) -> VNC бэкенд виртуальной машины."""
        try:
            async for message in websocket:
                if isinstance(message, bytes) and len(message) > 0 and not is_closed:
                    writer.write(message)
                    await writer.drain()  # Принудительно проталкиваем буфер в сетевой сокет ОС
        except Exception: pass
        finally: await close_all()

    async def pipeline_vm_to_browser():
        """Исходящий пайплайн передачи данных: VNC бэкенд (Графические фреймы) -> Браузер пользователя."""
        try:
            while not is_closed:
                data = await reader.read(65536)  # Оптимальный размер буфера чтения видеопотока
                if not data: break
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception: pass
        finally: await close_all()

    # Параллельный запуск всех асинхронных воркеров сессии в одном Event Loop
    await asyncio.gather(keep_alive_pinger(), pipeline_browser_to_vm(), pipeline_vm_to_browser(), return_exceptions=True)

def modify_handshake_headers(path, request_headers):
    """
    Перехватчик стадии WebSocket Handshake (спецификация websockets v10.4).

    КРИТИЧЕСКИЙ НЮАНС (Победа над HTTP 426): Если вышестоящий прокси-сервер прислал заголовок
    'Connection: keep-alive, Upgrade', легаси-библиотека разорвет соединение. 
    Этот хук принимает объект заголовков напрямую и принудительно переписывает его на чистый 'Upgrade'.
    """
    conn_header = request_headers.get('Connection', '')
    if 'keep-alive' in conn_header.lower():
        request_headers['Connection'] = 'Upgrade'
        if 'Upgrade' not in request_headers:
            request_headers['Upgrade'] = 'websocket'
    return None 

async def main(listen_host, listen_port):
    """Инициализирует и запускает фоновый легаси-сервер WebSocket шлюза."""
    async with ws_serve(ws_handler, listen_host, listen_port, subprotocols=["binary"], process_request=modify_handshake_headers):
        logging.info(f"VNC-to-WebSocket Legacy Bridge Engine active on port {listen_port}")
        # НЮАНС (ws10 / Python 3.7): Поскольку класса asyncio.Future() в старых версиях нет, 
        # для бесконечного удержания асинхронного цикла используется ожидание системного эвента.
        await asyncio.Event().wait()

def valid_listen_address(netloc):
    """Унифицированный валидатор сетевых хостов и портов для argparse"""
    try:
        host, port_str = netloc.rsplit(':', 1) if ":" in netloc else ("0.0.0.0", netloc)
        return host, int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' не является валидным сетевым адресом.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-bridge: High-performance Legacy Proxy")
    parser.add_argument("listen_target", nargs="?", default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    host, port = args.listen_target.rsplit(':', 1) if ":" in args.listen_target else ("0.0.0.0", args.listen_target)
    
    try: asyncio.run(main(host, int(port)))
    except KeyboardInterrupt: pass
