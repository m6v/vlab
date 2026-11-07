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

async def ws_handler(websocket):
    uri = getattr(websocket.request, 'uri', websocket.request.path or "")
    query_str = uri.split('?', 1)[1] if '?' in uri else ""
    
    params = parse_qs(query_str)
    token_list = params.get('token', params.get('path', []))
    token = token_list[0].strip() if token_list and token_list[0] else None
    
    if not token:
        forwarded_uri = websocket.request.headers.get('X-Forwarded-Uri', '')
        if forwarded_uri and '?' in forwarded_uri:
            token_list = parse_qs(forwarded_uri.split('?', 1)[1]).get('token', [])
            token = token_list[0].strip() if token_list and token_list[0] else None

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

    async def close_all():
        nonlocal is_closed
        if not is_closed:
            is_closed = True
            try: writer.close(); await writer.wait_closed()
            except Exception: pass
            try: await websocket.close()
            except Exception: pass

    async def keep_alive_pinger():
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed: await websocket.ping()
        except Exception: await close_all()

    async def pipeline_browser_to_vm():
        try:
            async for message in websocket:
                if isinstance(message, bytes) and len(message) > 0 and not is_closed:
                    writer.write(message); await writer.drain()
        except Exception: pass
        finally: await close_all()

    async def pipeline_vm_to_browser():
        try:
            while not is_closed:
                data = await reader.read(65536)
                if not data: break
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception: pass
        finally: await close_all()

    await asyncio.gather(keep_alive_pinger(), pipeline_browser_to_vm(), pipeline_vm_to_browser(), return_exceptions=True)

def modify_handshake_headers(connection, request):
    conn_header = request.headers.get('Connection', '')
    if 'keep-alive' in conn_header.lower():
        request.headers['Connection'] = 'Upgrade'
        if 'Upgrade' not in request.headers:
            request.headers['Upgrade'] = 'websocket'
    return None 

async def main(listen_host, listen_port):
    async with ws_serve(ws_handler, listen_host, listen_port, subprotocols=["binary"], process_request=modify_handshake_headers):
        logging.info(f"VNC Bridge active on port {listen_port}")
        await asyncio.Future()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("listen_target", nargs="?", default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    host, port = args.listen_target.rsplit(':', 1) if ":" in args.listen_target else ("0.0.0.0", args.listen_target)
    
    try: asyncio.run(main(host, int(port)))
    except KeyboardInterrupt: pass
