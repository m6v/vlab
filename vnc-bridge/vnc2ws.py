#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import sys
import argparse
from urllib.parse import urlparse, parse_qs

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')
TOKENS_PATH = ""

def get_target_addr(token):
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

async def handler(websocket):
    path = websocket.request.path
    parsed_url = urlparse(path)
    query_params = parse_qs(parsed_url.query)
    
    token_list = query_params.get('token', query_params.get('path', []))
    token = token_list[0] if token_list else None
    
    if not token:
        await websocket.close(1008, "Missing token")
        return

    vnc_target = get_target_addr(token)
    if not vnc_target:
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    logging.info(f"[{token}] Подключение к VNC {host}:{port}")

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
    except Exception as e:
        logging.error(f"[{token}] Ошибка TCP: {e}")
        return

    is_closed = False

    async def close_connections():
        nonlocal is_closed
        if not is_closed:
            is_closed = True
            try: writer.close(); await writer.wait_closed()
            except Exception: pass
            try: await websocket.close()
            except Exception: pass

    async def server_ping():
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed: await websocket.ping()
        except Exception: await close_connections()

    async def inbound_pipeline():
        try:
            async for message in websocket:
                if isinstance(message, bytes) and len(message) > 0:
                    writer.write(message); await writer.drain()
        except Exception: pass
        finally: await close_connections()

    async def outbound_pipeline():
        try:
            while not is_closed:
                data = await reader.read(65536)
                if not data: break
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception: pass
        finally: await close_connections()

    await asyncio.gather(server_ping(), inbound_pipeline(), outbound_pipeline(), return_exceptions=True)

def modify_handshake_headers(connection, request):
    conn_header = request.headers.get('Connection', '')
    if 'keep-alive' in conn_header.lower():
        request.headers['Connection'] = 'Upgrade'
        if 'Upgrade' not in request.headers:
            request.headers['Upgrade'] = 'websocket'
    return None 

async def main(listen_host, listen_port):
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    logging.info(f"Using websockets v.{websockets.__version__}")

    async with ws_serve(handler, listen_host, listen_port, subprotocols=["binary"], process_request=modify_handshake_headers):
        await asyncio.Future()

def valid_listen_address(netloc):
    try:
        if ":" in netloc:
            host, port_str = netloc.rsplit(':', 1)
            return (host, int(port_str))
        return ("0.0.0.0", int(netloc))
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' is not valid.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("listen_target", nargs="?", type=valid_listen_address, default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    listen_host, listen_port = args.listen_target
    logging.info(f"Using websockets v.{websockets.__version__} from {os.path.dirname(websockets.__file__)} with token mapping configuration source {TOKENS_PATH}")
    
    try: asyncio.run(main(listen_host, listen_port))
    except KeyboardInterrupt: pass
