#!/usr/bin/env python3
import os
import sys
import socket
import subprocess
import logging
import argparse
import re
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEMPLATE_PATH = "index.html"
# Регулярное выражение для безопасной валидации имени ВМ
VM_NAME_SAFE_REGEXP = re.compile(r'^[a-zA-Z0-9_-]+$')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

def get_vnc_address(vm_name, tokens_path):
    """Извлекает адрес VNC (host:port) из tokens.conf по строгому совпадению имени"""
    if not os.path.exists(tokens_path) or not os.access(tokens_path, os.R_OK):
        logging.error(f"Configuration file not found or not readable: {tokens_path}")
        return None

    with open(tokens_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if ':' in line:
                name, addr = line.split(':', 1)
                if name.strip() == vm_name:
                    return addr.strip()
    return None

def is_vm_running(vnc_addr):
    """Проверяет статус ВМ по доступности её VNC-порта"""
    if not vnc_addr or ':' not in vnc_addr:
        return False
    try:
        host, port_str = vnc_addr.split(':')
        with socket.create_connection((host, int(port_str)), timeout=0.5):
            return True
    except Exception:
        return False

class VLabManagementHandler(BaseHTTPRequestHandler):
    """Многопоточный контроллер управления веб-консолью"""
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        vms = query_params.get("vm", [""])
        vm_name = vms[0].strip() if vms and vms[0] else ""
        tokens_path = self.server.tokens_path
        webroot = self.server.webroot

        # Защитная валидация имени ВМ для всех эндпоинтов
        if vm_name and not VM_NAME_SAFE_REGEXP.match(vm_name):
            logging.warning(f"Rejected malicious or invalid VM name format: '{vm_name}'")
            self.send_error(400, "Bad Request: Invalid VM name format")
            return

        # API ЭНДПОИНТ: Запуск ВМ
        if parsed_url.path == "/start" and vm_name:
            logging.info(f"Starting virtual machine: {vm_name}...")
            cmd = ["/usr/local/bin/vmctrl", "start", vm_name]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # API ЭНДПОИНТ: Текстовый статус для фронтенда
        if parsed_url.path == "/status":
            vnc_addr = get_vnc_address(vm_name, tokens_path) if vm_name else None
            status = "RUNNING" if is_vm_running(vnc_addr) else "STOPPED"
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(status.encode())
            return

        # UI ЭНДПОИНТ: Раздача фавикон (ищет в подкаталоге images внутри webroot)
        if parsed_url.path == "/favicon.ico":
            icons_dir = os.path.join(webroot, "images")
            icon_path = os.path.join(icons_dir, f"{vm_name}.png")
            if not vm_name or not os.path.exists(icon_path):
                icon_path = os.path.join(icons_dir, "default.png")
            if not os.path.exists(icon_path):
                # Если дефолтной иконки нет в webroot, отдаем пустой ответ без ошибки
                self.send_response(204)
                self.end_headers()
                return
            with open(icon_path, 'rb') as f:
                icon_data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(icon_data)
            return

        # UI ЭНДПОИНТ: Выдача интерфейса
        if parsed_url.path == "/":
            if not vm_name or not get_vnc_address(vm_name, tokens_path):
                logging.warning(f"Rejected request for unregistered VM identifier: '{vm_name}'")
                self.send_error(404, f"VM '{vm_name}' not registered")
                return

            if not os.path.exists(TEMPLATE_PATH):
                logging.critical(f"Template asset source file missing: {TEMPLATE_PATH}")
                self.send_error(500, "Internal Server Error: UI template missing")
                return

            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                html = f.read().replace("{{.VMName}}", vm_name).replace("{{.ProxyPort}}", str(self.server.proxy_port))

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        self.send_error(404, "Not Found")

class CleanThreadingHTTPServer(ThreadingHTTPServer):
    """Кастомный сервер, сохраняющий порты и пути в своем контексте"""
    def __init__(self, server_address, RequestHandlerClass, proxy_port, tokens_path, webroot):
        self.proxy_port = proxy_port
        self.tokens_path = tokens_path
        self.webroot = webroot  # Контекстный путь к каталогу noVNC
        super().__init__(server_address, RequestHandlerClass)

    def handle_error(self, request, client_address):
        err_type, _, _ = sys.exc_info()
        if err_type in [BrokenPipeError, ConnectionResetError]:
            logging.info(f"Client {client_address} disconnected prematurely.")
            return
        super().handle_error(request, client_address)

def valid_listen_address(netloc):
    try:
        host, port_str = netloc.rsplit(':', 1)
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError
        return (host, port) 
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"'{netloc}' is not a valid listen address. Expected 'HOST:PORT' (e.g., 0.0.0.0:8081)"
        )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Web Console Server")
    parser.add_argument("listen", nargs="?", type=valid_listen_address, default="0.0.0.0:8081", help="Listen address and port (default: 0.0.0.0:8081)")
    parser.add_argument("--proxy-port", type=int, default=8080, help="VNC proxy runtime port")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf", help="Path to tokens configuration file (default: /srv/qemu/tokens.conf)")
    parser.add_argument("--webroot", default="/usr/share/novnc/", help="Path to noVNC static web directory (default: /usr/share/novnc/)")
    args = parser.parse_args()

    host, port = args.listen

    if not os.path.exists(TEMPLATE_PATH):
        logging.critical(f"Required UI template file component is missing: {TEMPLATE_PATH}")
        sys.exit(1)

    # Валидация существования каталога webroot
    if not os.path.exists(args.webroot) or not os.path.isdir(args.webroot):
        logging.fatal(f"[FATAL] Specified noVNC webroot directory '{args.webroot}' does not exist or is not a directory")
        sys.exit(1)

    logging.info(f"Management infrastructure successfully started on {host}:{port}")
    logging.info(f"Using noVNC webroot source: {args.webroot}")
    
    # Передаем args.webroot в инициализацию сервера
    server = CleanThreadingHTTPServer((host, port), VLabManagementHandler, args.proxy_port, args.token_source, args.webroot)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Daemon terminated by admin.")
