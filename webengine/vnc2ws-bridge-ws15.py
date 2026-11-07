#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import argparse
from urllib.parse import parse_qs
import websockets
from websockets.asyncio.server import serve as ws_serve

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

# Шаблон парсинга строк tokens.conf: Имя_ВМ:Локальный_IP:Порт_VNC
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')
TOKENS_PATH = ""

def get_target_addr(token):
    """
    Ищет IP и порт целевого VNC-сервера по переданному WebSocket-токену.

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

async def ws_handler(websocket):
    """
    Основной асинхронный воркер WebSocket соединения (совместим с websockets v13-15).
    
    ПРИМЕЧАНИЕ: Полный URI запроса извлекается из свойства request.uri, так как
    в новых версиях библиотеки request.path содержит только путь без параметров query.
    """
    uri = getattr(websocket.request, 'uri', websocket.request.path or "")
    _, _, query_str = uri.partition('?')
    
    # БЕЗОПАСНОЕ ИЗВЛЕЧЕНИЕ: Использование фолбэка [""] защищает индекс [0] от IndexError.
    # Текстовый маркер [0] прописан явно, метод .strip() применяется строго к строке.
    params = parse_qs(query_str)
    token_list = params.get('token', params.get('path', [""]))
    token = token_list[0].strip() if token_list and token_list[0] else None
    
    # ФОЛБЭК: Защита работы за корпоративными реверс-прокси
    if not token:
        forwarded_uri = websocket.request_headers.get('X-Forwarded-Uri', '')
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
            try: writer.close(); await writer.wait_closed()
            except Exception: pass
            try: await websocket.close()
            except Exception: pass
            logging.info(f"[{token}] Все транспортные каналы сессии штатно закрыты")

    async def keep_alive_pinger():
        """
        Внутренний серверный пингер. Отправляет пустые фреймы WebSocket каждые 15 секунд.

        ПРИМЕЧАНИЕ: Этот цикл предотвращает разрыв "молчаливых" сессий (без движения мыши)
        интеллектуальным сетевым оборудованием и файрволами по тайм-ауту простоя.
        """
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed: await websocket.ping()
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

def modify_handshake_headers(connection, request):
    """
    Перехватчик стадии WebSocket Handshake.

    КРИТИЧЕСКИЙ НЮАНС (Победа над HTTP 426): Если вышестоящий прокси-сервер прислал заголовок
    'Connection: keep-alive, Upgrade', библиотека websockets разорвет соединение. 
    Этот хук принудительно переписывает заголовок на чистый 'Upgrade', спасая рукопожатие.
    """
    conn_header = request.headers.get('Connection', '')
    if 'keep-alive' in conn_header.lower():
        request.headers['Connection'] = 'Upgrade'
        if 'Upgrade' not in request.headers:
            request.headers['Upgrade'] = 'websocket'
    return None 

async def main(listen_host, listen_port):
    """Инициализирует и запускает асинхронный сервер WebSocket шлюза."""
    async with ws_serve(ws_handler, listen_host, listen_port, subprotocols=["binary"], process_request=modify_handshake_headers):
        logging.info(f"VNC-to-WebSocket Bridge Engine active on port {listen_port}")
        await asyncio.Future()  # Бесконечное удержание асинхронного цикла событий (Event Loop)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-bridge: High-performance Network Proxy")
    parser.add_argument("listen_target", nargs="?", default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    host, port = args.listen_target.rsplit(':', 1) if ":" in args.listen_target else ("0.0.0.0", args.listen_target)
    
    try: asyncio.run(main(host, int(port)))
    except KeyboardInterrupt: pass
