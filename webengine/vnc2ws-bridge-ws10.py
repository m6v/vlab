#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import sys
import argparse
from urllib.parse import urlparse, parse_qs
import websockets

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')

# Путь к файлу конфигурации токенов
TOKENS_PATH = ""

def get_target_addr(token):
    """Ищет адрес ВМ по токену в конфигурационном файле"""
    try:
        with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
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

async def handler(websocket, path):
    """Основной обработчик WebSocket соединения (совместим с websockets v10.4)"""
    parsed_url = urlparse(path)
    query_params = parse_qs(parsed_url.query)
    
    # Извлечение токена как строки
    token_list = query_params.get('token', query_params.get('path', []))
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
        logging.info(f"[{token}] TCP соединение с виртуальной машиной успешно установлено")
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
                if hasattr(writer, 'wait_closed'):
                    await writer.wait_closed()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass
            logging.info(f"[{token}] Все транспортные каналы успешно закрыты")

    # Серверный пингер к браузеру под API websockets v10.4
    async def server_ping():
        try:
            while not is_closed:
                await asyncio.sleep(15)  # Каждые 15 секунд
                if not is_closed:
                    res = websocket.ping() 
                    if asyncio.isfuture(res) or asyncio.iscoroutine(res):
                        await asyncio.wait_for(res, timeout=10.0)
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
                    logging.info(f"[{token}] Виртуальная машина штатно закрыла TCP соединение")
                    break
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception as e:
            logging.error(f"[{token}] Ошибка исходящего потока данных: {e}")
        finally:
            await close_connections()

    await asyncio.gather(server_ping(), inbound_pipeline(), outbound_pipeline(), return_exceptions=True)


def modify_handshake_headers(path, request_headers):
    """Исправляет заголовок Connection под API websockets v10.4"""
    conn_header = request_headers.get('Connection', '')
    if 'keep-alive' in conn_header.lower():
        request_headers['Connection'] = 'Upgrade'
        if 'Upgrade' not in request_headers:
            request_headers['Upgrade'] = 'websocket'
    return None


async def main(listen_host, listen_port):
    from websockets import serve as ws_serve

    logging.info(f"Using websockets v.{websockets.__version__} from {os.path.dirname(websockets.__file__)}")

    async with ws_serve(
        handler, 
        listen_host, listen_port, 
        subprotocols=["binary"],
        process_request=modify_handshake_headers
    ):
        logging.info(f"VNC to WebSocket bridge initialized on {listen_host}:{listen_port}")
        await asyncio.Event().wait()


def valid_listen_address(netloc):
    """Унифицированный валидатор формата HOST:PORT для argparse"""
    try:
        if ":" in netloc:
            host, port_str = netloc.rsplit(':', 1)
            port = int(port_str)
        else:
            host = "0.0.0.0"
            port = int(netloc)
        if not (1 <= port <= 65535):
            raise ValueError
        return (host, port) 
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' is not a valid listen address.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Legacy VNC to WebSocket proxy bridge.")
    parser.add_argument("listen_target", nargs="?", type=valid_listen_address, default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    listen_host, listen_port = args.listen_target
    logging.info(f"Using websockets v.{websockets.__version__} from {os.path.dirname(websockets.__file__)} with token mapping configuration source {TOKENS_PATH}")

    try:
        asyncio.run(main(listen_host, listen_port))
    except KeyboardInterrupt:
        logging.info("Сервер остановлен пользователем")

