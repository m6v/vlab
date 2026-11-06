#!/usr/bin/env python3

import os
import sys
import socket
import subprocess
import logging
import argparse
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEMPLATE_PATH = "index.html"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

def get_vnc_address(vm_name, tokens_path):
    """Извлекает адрес VNC (host:port) из tokens.conf"""
    if not os.path.exists(tokens_path):
        logging.error(f"Configuration file not found: {tokens_path}")
        return None
    with open(tokens_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith(f"{vm_name}:"):
                try:
                    return line.split(':', 1)[1].strip()
                except IndexError:
                    continue
        return None

def is_vm_running(vm_name, tokens_path):
    """Проверяет статус ВМ сетевым пингом VNC-порта"""
    addr = get_vnc_address(vm_name, tokens_path)
    if not addr or ':' not in addr:
        return False
    try:
        host, port = addr.split(':')
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except Exception:
        return False

class VLabManagementHandler(BaseHTTPRequestHandler):
    """Многопоточный контроллер управления"""
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        vms = query_params.get("vm", [""])
        vm_name = vms[0].strip() if vms and vms[0] else ""
        tokens_path = self.server.tokens_path

        # API ЭНДПОИНТ: Запуск ВМ
        if parsed_url.path == "/start" and vm_name:
            logging.info(f"Start command requested for VM: {vm_name}")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name])
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # API ЭНДПОИНТ: Текстовый статус для фронтенда
        if parsed_url.path == "/status":
            status = "RUNNING" if is_vm_running(vm_name, tokens_path) else "STOPPED"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(status.encode())
            return

        # UI ЭНДПОИНТ: Раздача фавикон
        if parsed_url.path == "/favicon.ico":
            icons_dir = "/usr/share/pixmaps"
            icon_path = os.path.join(icons_dir, f"{vm_name}.png")
            if not vm_name or not os.path.exists(icon_path):
                icon_path = os.path.join(icons_dir, "default.png")
            if not os.path.exists(icon_path):
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
    def __init__(self, server_address, RequestHandlerClass, proxy_port, tokens_path):
        self.proxy_port = proxy_port
        self.tokens_path = tokens_path
        super().__init__(server_address, RequestHandlerClass)

    def handle_error(self, request, client_address):
        err_type, _, _ = sys.exc_info()
        if err_type in [BrokenPipeError, ConnectionResetError]:
            logging.info(f"Client {client_address} disconnected prematurely.")
            return
        super().handle_error(request, client_address)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VLab compact management daemon.")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081", help="Listen address and port (e.g. 0.0.0.0:8081)")
    parser.add_argument("--proxy-port", type=int, default=8080, help="VNC proxy runtime port")
    parser.add_argument("--tokens", default="/srv/qemu/tokens.conf", help="Path to tokens configuration file")
    args = parser.parse_args()

    # Валидация двоеточия для IPv4
    if ':' not in args.listen or args.listen.count(':') != 1:
        logging.critical(f"Invalid listen address format '{args.listen}'. Expected 'HOST:PORT' (e.g. 0.0.0.0:8081)")
        sys.exit(1)

    host, port_str = args.listen.split(':')

    # Валидация порта
    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            raise ValueError
    except ValueError:
        logging.critical(f"Invalid port value '{port_str}'. Port must be an integer between 1 and 65535.")
        sys.exit(1)

    # Валидация IPv4-хоста
    try:
        socket.inet_aton(host)
    except socket.error:
        try:
            socket.gethostbyname(host)
        except socket.gaierror:
            logging.critical(f"Hostname or IPv4 address '{host}' is invalid or cannot be resolved.")
            sys.exit(1)

    # Валидация шаблона при старте
    if not os.path.exists(TEMPLATE_PATH):
        logging.critical(f"Required UI template file component is missing: {TEMPLATE_PATH}")
        sys.exit(1)

    logging.info(f"Management infrastructure successfully started on {host}:{port}")
    logging.info(f"Using tokens configuration file source: {args.tokens}")

    server = CleanThreadingHTTPServer((host, port), VLabManagementHandler, args.proxy_port, args.tokens)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Daemon terminated by admin.")
