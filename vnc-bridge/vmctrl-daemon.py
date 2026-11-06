#!/usr/bin/env python3

import os, sys, socket, subprocess, logging, argparse
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKENS_PATH = "/srv/qemu/tokens.conf"
TEMPLATE_PATH = "index.html"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

def get_vnc_address(vm_name):
    """Извлекает адрес VNC (host:port) из tokens.conf"""
    if not os.path.exists(TOKENS_PATH): return None
    with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
        match = [line.split(':', 1)[1].strip() for line in f if line.strip() and not line.startswith('#') and line.startswith(f"{vm_name}:")]
        return match[0] if match else None

def is_vm_running(vm_name):
    """Проверяет статус ВМ сетевым пингом VNC-порта с надежным таймаутом"""
    addr = get_vnc_address(vm_name)
    if not addr or ':' not in addr: return False
    try:
        host, port = addr.split(':')
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except Exception:
        return False

class VLabManagementHandler(BaseHTTPRequestHandler):
    """Многопоточный контроллер управления"""
    def log_message(self, format, *args): logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        vms = query_params.get("vm", [""])
        vm_name = vms[0].strip() if vms and vms[0] else ""

        # API ЭНДПОИНТ: Запуск ВМ
        if parsed_url.path == "/start" and vm_name:
            logging.info(f"Start request for VM: {vm_name}")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name])
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # API ЭНДПОИНТ: Текстовый статус для фронтенда
        if parsed_url.path == "/status":
            status = "RUNNING" if is_vm_running(vm_name) else "STOPPED"
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
            if not vm_name or not get_vnc_address(vm_name):
                logging.warning(f"Rejected request for unregistered VM: '{vm_name}'")
                self.send_error(404, f"VM '{vm_name}' not registered")
                return

            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                # Извлекаем порты из свойств инстанса сервера, переданных при старте
                html = f.read().replace("{{.VMName}}", vm_name).replace("{{.ProxyPort}}", str(self.server.proxy_port))

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        self.send_error(404, "Not Found")

class CleanThreadingHTTPServer(ThreadingHTTPServer):
    """Кастомный сервер, подавляющий системный спам BrokenPipeError в логах"""
    def __init__(self, server_address, RequestHandlerClass, proxy_port):
        # Динамически подмешиваем порт прокси прямо в контекст сервера
        self.proxy_port = proxy_port
        super().__init__(server_address, RequestHandlerClass)

    def handle_error(self, request, client_address):
        err_type, _, _ = sys.exc_info()
        if err_type in [BrokenPipeError, ConnectionResetError]:
            logging.info(f"Client {client_address} disconnected prematurely.")
            return
        super().handle_error(request, client_address)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VLab compact management daemon.")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081", help="Listen address and port")
    parser.add_argument("--proxy-port", type=int, default=8080, help="VNC proxy runtime port")
    args = parser.parse_args()

    try: host, port_str = args.listen.split(':')
    except ValueError: host, port_str = '0.0.0.0', '8081'

    logging.info(f"Starting management infrastructure on {host}:{port_str}")

    # Передача распарсенного proxy-port третьим аргументом в конструктор сервера
    server = CleanThreadingHTTPServer((host, int(port_str)), VLabManagementHandler, args.proxy_port)

    try: server.serve_forever()
    except KeyboardInterrupt: logging.info("Daemon terminated by admin")
