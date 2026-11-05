#!/usr/bin/env python3
import os, sys, socket, subprocess, logging, argparse
from urllib.parse import urlparse, parse_qs
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# Системные константы конфигурации
TOKENS_PATH = "/srv/qemu/tokens.conf"
TEMPLATE_PATH = "index.html"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

def get_vnc_address(vm_name):
    """Извлекает адрес VNC (host:port) из tokens.conf в одну строчку [1.4]"""
    if not os.path.exists(TOKENS_PATH): return None
    with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
        match = [line.split(':', 1)[1].strip() for line in f if line.strip() and not line.startswith('#') and line.startswith(f"{vm_name}:")]
        return match[0] if match else None

def is_vm_running(vm_name):
    """Проверяет статус ВМ простым сетевым пингом VNC-порта [1.4]"""
    addr = get_vnc_address(vm_name)
    if not addr or ':' not in addr: return False
    try:
        host, port = addr.split(':')
        with socket.create_connection((host, int(port)), timeout=0.1): 
            return True
    except Exception: 
        return False

class VLabManagementHandler(SimpleHTTPRequestHandler):
    """Многопоточный контроллер управления лабораторией [1.4]"""
    def log_message(self, format, *args): logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        # Извлекаем первый элемент из списка параметров и очищаем [1.4]
        vms = query_params.get("vm", [""])
        vm_name = vms[0].strip() if vms and vms[0] else ""

        # 1. API ЭНДПОИНТ: Запуск ВМ
        if parsed_url.path == "/start" and vm_name:
            logging.info(f"Start request for VM: {vm_name}")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name])
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # 2. API ЭНДПОИНТ: Текстовый статус для фронтенда [1.4]
        if parsed_url.path == "/status":
            status = "RUNNING" if is_vm_running(vm_name) else "STOPPED"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(status.encode())
            return

        # 3. UI ЭНДПОИНТ: Выдача интерфейса [1.4]
        if parsed_url.path == "/":
            if not vm_name or not get_vnc_address(vm_name):
                logging.warning(f"Rejected request for unregistered VM: '{vm_name}'")
                self.send_error(404, f"VM '{vm_name}' not registered")
                return

            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                html = f.read().replace("{{.VMName}}", vm_name).replace("{{.ProxyPort}}", str(proxy_port))

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode('utf-8'))
            return

        self.send_error(404, "Not Found")

class CleanThreadingHTTPServer(ThreadingHTTPServer):
    """Кастомный сервер, подавляющий системный спам BrokenPipeError в логах [1.4]"""
    def handle_error(self, request, client_address):
        # Извлекаем тип текущей ошибки из системного стека [1.4]
        err_type, err_val, _ = sys.exc_info()
        if err_type in [BrokenPipeError, ConnectionResetError]:
            # Вместо страшного многострочного Traceback пишем одну чистую лаконичную строчку [1.4]
            logging.info(f"Client {client_address} disconnected prematurely.")
            return
        # Все остальные критические ошибки (если упадет сам сервер) выводим стандартно [1.4]
        super().handle_error(request, client_address)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VLab compact management daemon.")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081", help="Listen address and port")
    parser.add_argument("--proxy-port", type=int, default=8080, help="VNC proxy runtime port")
    args = parser.parse_args()

    proxy_port = args.proxy_port
    try: host, port = args.listen.split(':')
    except ValueError: host, port = '0.0.0.0', 8081

    logging.info(f"Starting management infrastructure on {host}:{port}")
    
    # Запускаем наш очищенный от спама сервер [1.4]
    server = CleanThreadingHTTPServer((host, int(port)), VLabManagementHandler)
    
    try: server.serve_forever()
    except KeyboardInterrupt: logging.info("Daemon terminated by admin")
