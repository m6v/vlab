#!/usr/bin/env python3
import os
import sys
import re
import socket
import logging
import argparse
import asyncio
import threading
import json
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import websockets
try:
    # Попытка использовать websockets v13-15+
    from websockets.asyncio.server import serve as ws_serve
    LEGACY_WS = False
except ModuleNotFoundError:
    # Переключение на websockets v10.4
    from websockets import serve as ws_serve
    LEGACY_WS = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "index.html")
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')
TOKENS_PATH = ""

def parse_tokens_file():
    """
    Читает оригинальный tokens.conf и НА ЛЕТУ строит структуру дерева для фронтенда.
    Парсит локальный /etc/hosts напрямую, исключая задержки сети при отсутствии DNS.
    """
    tree = {}
    if not os.path.exists(TOKENS_PATH) or not os.access(TOKENS_PATH, os.R_OK):
        logging.error(f"Файл конфигурации токенов недоступен: {TOKENS_PATH}")
        return tree

    # Быстрый ручной парсинг /etc/hosts в памяти без спама в сеть
    hosts_map = {}
    try:
        hosts_file = Path("/etc/hosts")
        if hosts_file.exists():
            with open(hosts_file, "r", encoding="utf-8") as hf:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    parts = line.split()
                    if len(parts) >= 2:
                        ip_key = parts[0]
                        # Берем первое доступное имя для этого IP
                        name_val = parts[1]
                        hosts_map[ip_key] = name_val
    except Exception as he:
        logging.debug(f"Не удалось прочитать /etc/hosts: {he}")

    try:
        with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                match = TOKEN_REGEXP.match(line)
                if match:
                    token_name = match.group(1)
                    ip_address = match.group(2)
                    port = match.group(3)

                    # Извлекаем имя из локального кэша /etc/hosts или подставляем структурированный IP
                    host_group = hosts_map.get(ip_address, ip_address)

                    if host_group not in tree:
                        tree[host_group] = {
                            "ip": ip_address,
                            "vms": []
                        }

                    # Быстрый опрос доступности VNC-порта
                    is_online = False
                    try:
                        with socket.create_connection((ip_address, int(port)), timeout=0.1):
                            is_online = True
                    except Exception: 
                        pass

                    tree[host_group]["vms"].append({
                        "token": token_name,
                        "status": "online" if is_online else "offline"
                    })
    except Exception as e:
        logging.error(f"Ошибка при разборе файла токенов: {e}")

    return tree

def get_vnc_address(token):
    """Ищет адрес VNC (host, port) в tokens.conf по токену"""
    if not os.path.exists(TOKENS_PATH): return None
    with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            match = TOKEN_REGEXP.match(line)
            if match and match.group(1) == token:
                return match.group(2), int(match.group(3))
    return None

class InstructorPanelHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        # Пуленепробиваемый разбор путей через partition
        path, _, query_str = self.path.partition('?')
        webroot = self.server.webroot

        # REST API эндпоинт: Выдача дерева стендов на основе tokens.conf
        if path == '/api/tokens':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            
            api_response = {
                "config": {
                    "pythonPort": self.server.server_port,
                    "websockifyPort": self.server.proxy_port
                },
                "tree": parse_tokens_file()
            }
            self.wfile.write(json.dumps(api_response).encode('utf-8'))
            return

        # Раздача статических ассетов noVNC
        if path.startswith("/novnc/"):
            relative_path = path.replace("/novnc/", "", 1)
            file_path = os.path.normpath(os.path.join(webroot, relative_path))
            
            # Проверка Path Traversal исключает чтение системных файлов через '../../'
            if not file_path.startswith(os.path.normpath(webroot)) or not os.path.exists(file_path) or os.path.isdir(file_path):
                self.send_error(404, "File not found")
                return
                
            # Динамическое определение MIME-типов, гарантируя 'application/javascript' для rfb.js
            content_type, _ = mimetypes.guess_type(file_path)
            self.send_response(200)
            self.send_header("Content-Type", content_type or "text/plain")
            self.end_headers()
            with open(file_path, 'rb') as f:
                self.wfile.write(f.read())
            return

        # Раздача главной страницы Панели Инструктора
        if path == "/" or path == "/index.html":
            if not os.path.exists(TEMPLATE_PATH):
                self.send_error(500, "UI Template missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(TEMPLATE_PATH, 'rb') as f:
                self.wfile.write(f.read())
            return

        self.send_error(404, "Not Found")

def start_http_server(host, port, proxy_port, webroot):
    httpd = ThreadingHTTPServer((host, port), InstructorPanelHandler)
    httpd.proxy_port = proxy_port
    httpd.server_port = port
    httpd.webroot = os.path.abspath(webroot)
    logging.info(f"[HTTP] Панель инструктора запущена на http://{host}:{port}")
    try: httpd.serve_forever()
    except KeyboardInterrupt: pass

async def ws_handler(websocket, path=None):
    """Главный универсальный асинхронный воркер WebSocket соединения."""
    if LEGACY_WS:
        current_path = websocket.path if (hasattr(websocket, 'path') and "?" in websocket.path) else path
    else:
        current_path = getattr(websocket.request, 'uri', websocket.request.path or "")
    
    # Безопасное строковое разделение через .partition('?')
    _, _, query_str = current_path.partition('?')
    
    params = parse_qs(query_str)
    token_list = params.get('token', params.get('path', [""]))
    token = token_list[0].strip() if token_list and token_list[0] else None
    
    # Защита работы за корпоративными реверс-прокси
    if not token:
        headers = websocket.request_headers if LEGACY_WS else websocket.request.headers
        forwarded_uri = headers.get('X-Forwarded-Uri', '')
        if forwarded_uri and '?' in forwarded_uri:
            _, _, fwd_query = forwarded_uri.partition('?')
            fwd_list = parse_qs(fwd_query).get('token', [""])
            token = fwd_list[0].strip() if fwd_list and fwd_list[0] else None
        
        # Если даже в заголовках прокси токен отсутствует — аварийно закрываем сокет
        if not token:
            await websocket.close(1008, "Missing token")
            return

    vnc_target = get_vnc_address(token)
    if not vnc_target:
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
    except Exception as e:
        logging.error(f"[WS] [{token}] Ошибка TCP подключения к VNC: {e}")
        return

    is_closed = False

    async def close_all():
        nonlocal is_closed
        if not is_closed:
            is_closed = True
            try: 
                writer.close()
                if hasattr(writer, 'wait_closed'): await writer.wait_closed()
            except Exception: pass
            try: await websocket.close()
            except Exception: pass

    async def keep_alive_pinger():
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed: 
                    res = websocket.ping()
                    if asyncio.isfuture(res) or asyncio.iscoroutine(res):
                        await asyncio.wait_for(res, timeout=10.0)
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

def modify_handshake_headers(*args):
    """
    Коррекция заголовков Connection для совместимости с Reverse Proxy.
    
    Универсально нормализует параметры рукопожатия, предотвращая сброс 
    соединения с ошибкой HTTP 426 при прохождении через балансировщики.
    """
    try:
        request_headers = args if LEGACY_WS else args.headers
        conn_header = request_headers.get('Connection', '')
        if 'keep-alive' in conn_header.lower():
            request_headers['Connection'] = 'Upgrade'
            if 'Upgrade' not in request_headers:
                request_headers['Upgrade'] = 'websocket'
    except Exception: pass
    return None 

def choose_subprotocol(*args):
    """
    Динамически согласовывает субпротокол WebSocket.
    
    Универсально обрабатывает сигнатуры:
    - ws15: choose_subprotocol(connection, client_protocols)
    - ws10: choose_subprotocol(client_protocols, server_protocols)
    """
    try:
        # Если это ws15 (первый аргумент имеет свойство 'request'), то список протоколов — это второй аргумент [1.4]
        if len(args) > 1 and hasattr(args[0], 'request'):
            client_protocols = args[1]
        else:
            client_protocols = args[0]

        # Согласовываем бинарную передачу, если noVNC её запросил
        if client_protocols and "binary" in client_protocols:
            return "binary"
    except Exception as e:
        logging.error(f"Ошибка при согласовании субпротокола: {e}")
        
    # Если из-за изоляции iframe список пустой, принудительно возвращаем дефолтный 'binary',
    # как в нашем основном работающем проекте, предотвращая NegotiationError [1.4]
    return "binary"

async def main_async_loop(ws_host, ws_port):
    """Инициализирует и запускает асинхронный сервер универсального WebSocket-шлюза."""
    logging.info(f"[WS] Движок запущен в режиме: {'Legacy (ws10)' if LEGACY_WS else 'Modern (ws15)'}")
    
    async with ws_serve(
        ws_handler, 
        ws_host, 
        ws_port, 
        select_subprotocol=choose_subprotocol, 
        process_request=modify_handshake_headers
    ):
        logging.info(f"[WS] Наш транспортный шлюз успешно активирован на порту {ws_port}")
        if LEGACY_WS:
            await asyncio.Event().wait()
        else:
            await asyncio.Future()

def valid_listen_address(netloc):
    """
    Унифицированный валидатор сетевого адреса для argparse.
    
    Принимает строки вида '8085' или '0.0.0.0:8085'. Возвращает кортеж (host, port).
    """
    try:
        host, port_str = netloc.rsplit(':', 1) if ":" in netloc else ("0.0.0.0", netloc)
        return host, int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' не является валидным сетевым адресом.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "vnc2ws-instructor: Unified Instructor Control Panel, Monitoring & Gateway Engine. "
            "Handles static noVNC HTML5 assets distribution, parses local tokens.conf mapping, "
            "and orchestrates embedded high-performance asynchronous WebSocket-to-VNC proxies."
        )
    )
    parser.add_argument("-p", "--port", type=int, default=8000, help="Сетевой порт веб-интерфейса панели управления (по умолчанию: 8000)")
    parser.add_argument("-w", "--wport", type=int, default=8085, help="Сетевой порт асинхронного WebSocket-шлюза (по умолчанию: 8085)")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf", help="Путь к оригинальному файлу конфигурации токенов")
    parser.add_argument("--webroot", default="/usr/share/novnc/", help="Путь к системной директории noVNC")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source

    # КРИТИЧЕСКАЯ ВАЛИДАЦИЯ: Проверяем наличие необходимых компонентов перед запуском сокетов
    if not os.path.exists(args.webroot) or not os.path.exists(TEMPLATE_PATH):
        logging.fatal("[FATAL] Критические ресурсы среды (webroot или index.html) отсутствуют в каталоге скрипта!")
        sys.exit(1)

    # Запуск синхронного многопоточного HTTP веб-сервера панели в выделенном потоке ОС (Thread)
    threading.Thread(target=start_http_server, args=("0.0.0.0", args.port, args.wport, args.webroot), daemon=True).start()

    # Запуск асинхронного WebSocket шлюза в основном потоке приложения (Event Loop)
    try:
        asyncio.run(main_async_loop("0.0.0.0", args.wport))
    except KeyboardInterrupt:
        logging.info("Служба Панели инструктора успешно остановлена администратором.")
