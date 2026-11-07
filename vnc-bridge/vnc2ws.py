#!/usr/bin/env python3
import asyncio
import logging
import os
import re
from urllib.parse import urlparse, parse_qs
import websockets

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

TOKENS_PATH = "/srv/qemu/tokens.conf"
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')

logging.info(f"Using websockets v.{websockets.__version__} from {os.path.dirname(websockets.__file__)}")

def get_target_addr(token):
    try:
        with open(TOKENS_PATH, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                match = TOKEN_REGEXP.match(line)
                if match and match.group(1) == token:
                    return match.group(2), int(match.group(3))
    except Exception as e:
        logging.error(f"Ошибка чтения файла токенов: {e}")
    return None

async def handler(websocket):
    """Основной обработчик WebSocket соединения"""
    path = websocket.request.path
    parsed_url = urlparse(path)
    query_params = parse_qs(parsed_url.query)
    token_list = query_params.get('token', query_params.get('path', [None]))
    token = token_list[0] if token_list else None
    
    if not token:
        logging.warning("Missing token parameter")
        await websocket.close(1008, "Missing token")
        return

    vnc_target = get_target_addr(token)
    if not vnc_target:
        logging.warning(f"[{token}] Token not registered")
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    logging.info(f"[{token}] Попытка подключения к VNC бэкенду {host}:{port}")

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), 
            timeout=5.0
        )
        logging.info(f"[{token}] TCP линк с ВМ успешно установлен")
    except Exception as e:
        logging.error(f"[{token}] Ошибка подключения к TCP VNC: {e}")
        return

    is_closed = False

    async def close_connections():
        nonlocal is_closed
        if not is_closed:
            is_closed = True
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass
            logging.info(f"[{token}] Все транспортные каналы успешно закрыты")

    # Серверный пингер к браузеру
    async def server_ping():
        try:
            while not is_closed:
                await asyncio.sleep(15)  # Каждые 15 секунд
                if not is_closed:
                    await websocket.ping() 
        except Exception as e:
            logging.info(f"[{token}] Ошибка отправки системного пинга: {e}")
            await close_connections()

    # Входящий пайплайн (Browser -> VM)
    async def inbound_pipeline():
        try:
            async for message in websocket:
                if isinstance(message, bytes) and len(message) > 0:
                    writer.write(message)
                    await writer.drain()
        except Exception as e:
            logging.info(f"[{token}] Входящий WebSocket канал закрыт: {e}")
        finally:
            await close_connections()

    # Исходящий пайплайн (VM -> Browser)
    async def outbound_pipeline():
        try:
            while not is_closed:
                data = await reader.read(65536)
                if not data:
                    logging.info(f"[{token}] Виртуальная машина штатно закрыла TCP")
                    break
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception as e:
            logging.error(f"[{token}] Ошибка исходящего потока данных: {e}")
        finally:
            await close_connections()

    await asyncio.gather(server_ping(), inbound_pipeline(), outbound_pipeline(), return_exceptions=True)

def modify_handshake_headers(connection, request):
    """
    Официальный перехватчик HTTP-запроса до начала WebSocket-валидации.
    Исправляет заголовок Connection, если прокси прислал keep-alive.
    """
    conn_header = request.headers.get('Connection', '')
    if 'keep-alive' in conn_header.lower():
        request.headers['Connection'] = 'Upgrade'
        if 'Upgrade' not in request.headers:
            request.headers['Upgrade'] = 'websocket'
    return None 

async def main():
    from websockets.asyncio.server import serve as ws_serve

    async with ws_serve(
        handler, 
        "0.0.0.0", 8080, 
        subprotocols=["binary"],
        process_request=modify_handshake_headers
    ):
        logging.info("Python VNC to WebSocket bridge initialized on 0.0.0.0:8080 (Safe Proxy Mode)")
        await asyncio.Future()  # Команда корректно отделена, опечатка устранена

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Сервер остановлен пользователем")
