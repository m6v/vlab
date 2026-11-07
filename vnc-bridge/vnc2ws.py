#!/usr/bin/env python3
import argparse
import asyncio
import logging
import os
import re
import socket
from urllib.parse import urlparse, parse_qs
import websockets

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

logging.info(f"Using websockets v.{websockets.__version__} from {os.path.dirname(websockets.__file__)}")

TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')

# Глобальная переменная для пути к файлу конфигурации токенов
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

async def handler(websocket):
    """Основной обработчик WebSocket соединения"""
    path = websocket.request.path
    parsed_url = urlparse(path)
    query_params = parse_qs(parsed_url.query)
    
    # Извлекаем токен из query string (?vm= или ?token=)
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
        # Устанавливаем TCP линк с виртуальной машиной
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

    # Серверный пингер к браузеру для защиты от засыпания вкладок (v15+)
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
                    logging.info(f"[{token}] Виртуальная машина штатно закрыла TCP (EOF)")
                    break
                # Защита от медленных клиентов (таймаут записи 10 секунд)
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception as e:
            logging.error(f"[{token}] Ошибка исходящего потока данных: {e}")
        finally:
            await close_connections()

    # Параллельный запуск всех задач сессии
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


async def main(listen_host, listen_port):
    from websockets.asyncio.server import serve as ws_serve

    async with ws_serve(
        handler, 
        listen_host, listen_port, 
        subprotocols=["binary"],
        process_request=modify_handshake_headers
    ):
        logging.info(f"Python VNC to WebSocket bridge initialized on {listen_host}:{listen_port} (Safe Proxy Mode)")
        await asyncio.Future()

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
        raise argparse.ArgumentTypeError(
            f"'{netloc}' is not a valid listen address. Expected 'PORT' or 'HOST:PORT' (e.g., 8080 or 0.0.0.0:8080)"
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Universal lightweight VNC to WebSocket proxy bridge using tokens."
    )
    # Позиционный аргумент теперь проходит через унифицированный валидатор типа
    parser.add_argument(
        "listen_target", nargs="?", type=valid_listen_address, default="0.0.0.0:8080",
        help="Local address and port to listen on, e.g. '8080' or '0.0.0.0:8080'"
    )
    parser.add_argument(
        "--token-source", default="/srv/qemu/tokens.conf", 
        help="Path to tokens configuration file"
    )

    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    listen_host, listen_port = args.listen_target

    logging.info(f"Using websockets v.{websockets.__version__} from {os.path.dirname(websockets.__file__)}")
    logging.info(f"Token mapping configuration source: {TOKENS_PATH}")

    try:
        asyncio.run(main(listen_host, listen_port))
    except KeyboardInterrupt:
        logging.info("Сервер остановлен пользователем")
