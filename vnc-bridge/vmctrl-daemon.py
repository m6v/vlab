#!/usr/bin/env python3
import os
import sys
import socket
import threading
import subprocess
import re
import logging
import argparse
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

# Системные константы конфигурации
DAEMON_SOCK = "/tmp/qemu/vmctrl.sock"
TEMPLATE_PATH = "index.html"
TOKENS_PATH = "/srv/qemu/tokens.conf"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y/%m/%d %H:%M:%S',
    stream=sys.stdout
)

def is_vm_running(vm_name):
    """Проверяет статус ВМ через loginctl и UNIX-сокеты QEMU"""
    try:
        out = subprocess.check_output(["loginctl", "list-sessions", "--no-legend"], text=True)
        active_user = None
        for line in out.strip().split('\n'):
            if "seat0" in line:
                fields = line.split()
                if len(fields) >= 3:
                    active_user = fields[2]
                    
        if not active_user or active_user == "root":
            return False

        monitor_sock = f"/tmp/qemu/{active_user}/{vm_name}.sock"
        if not os.path.exists(monitor_sock):
            return False

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.15)
        s.connect(monitor_sock)
        s.close()
        return True
    except Exception:
        return False

def check_token_exists(vm_name):
    """Проверяет, зарегистрирована ли машина в tokens.conf"""
    if not os.path.exists(TOKENS_PATH):
        return False
    target_prefix = f"{vm_name}:"
    with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith(target_prefix):
                return True
    return False

class ManagementHTTPHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP запросов пульта управления лабораторией"""
    
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        # ЭНДПОИНТ: /start (Удаленный запуск ВМ)
        if parsed_url.path == "/start":
            vms = query_params.get("vm", [""])
            vm_name = vms[0].strip() if vms and vms[0] else ""
            if not vm_name:
                self.send_error(400, "Missing 'vm' parameter")
                return
                
            logging.info(f"Start request for VM: {vm_name}")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name])
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # ENDPOINT: / (Выдача веб-страницы интерфейса конкретной ВМ)
        if parsed_url.path == "/":
            vms = query_params.get("vm", [""])
            vm_name = vms[0].strip() if vms and vms[0] else ""
            if not vm_name:
                self.send_error(400, "Missing 'vm' parameter. Use /?vm=name")
                return

            if not check_token_exists(vm_name):
                logging.warning(f"Access denied: VM '{vm_name}' not found in tokens.conf")
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                err_msg = f"Error: Virtual machine '{vm_name}' is not registered on this server."
                self.wfile.write(err_msg.encode('utf-8'))
                return

            if not os.path.exists(TEMPLATE_PATH):
                logging.error(f"Template missing: {TEMPLATE_PATH}")
                self.send_error(500, f"Template file {TEMPLATE_PATH} not found")
                return

            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                html_content = f.read()

            is_running = is_vm_running(vm_name)

            # ДИНАМИЧЕСКИЙ РЕДИРЕКТ НА ПОРТ 8080 С УЧЕТОМ УДАЛЕННОГО ПОДКЛЮЧЕНИЯ [1.4]
            if is_running:
                host_header = self.headers.get('Host', '127.0.0.1')
                server_ip = host_header.split(':')[0] # Вытаскиваем чистый IP или домен [1.4]
                
                logging.info(f"VM '{vm_name}' is running, redirecting remote client to port 8080")
                self.send_response(302)
                
                # Защита от искажений вывода парсера чата
                redirect_target = "ht" + "tp://" + f"{server_ip}:80" + f"80/?vm={vm_name}"
                self.send_header("Location", redirect_target)
                self.end_headers()
                return

            # ЕСЛИ МАШИНА ВЫКЛЮЧЕНА: Выдаем экран ожидания [1.4]
            logging.info(f"Serving dashboard for offline VM: {vm_name}")
            html_content = html_content.replace("{{.VMName}}", vm_name)
            html_content = html_content.replace("{{ .VMName }}", vm_name)
            
            pattern = re.compile(r"\{\{\s*if\s+\.IsRunning\s*\}\}(.*?)\{\{\s*else\s*\}\}(.*?)\{\{\s*end\s*\}\}", re.DOTALL)
            match = pattern.search(html_content)
            if match:
                html_content = pattern.sub(match.group(2), html_content)

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
            return

        self.send_error(404, "Not Found")

def run_http_server(host, port):
    """Запуск веб-сервера управления"""
    server_address = (host, port)
    httpd = HTTPServer(server_address, ManagementHTTPHandler)
    logging.info(f"HTTP management server listening on {host}:{port}")
    httpd.serve_forever()

def run_unix_socket_listener():
    """Запуск прослушивания UNIX сокета для команд локального хоста"""
    if os.path.exists(DAEMON_SOCK):
        os.remove(DAEMON_SOCK)
        
    os.makedirs(os.path.dirname(DAEMON_SOCK), exist_ok=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(DAEMON_SOCK)
    os.chmod(DAEMON_SOCK, 0o777)
    server.listen(5)
    logging.info(f"UNIX management socket activated: {DAEMON_SOCK}")

    while True:
        try:
            conn, _ = server.accept()
            data = conn.recv(1024).decode('utf-8').strip()
            if data:
                logging.info(f"UNIX socket command received to start VM: {data}")
                subprocess.Popen(["/usr/local/bin/vmctrl", "start", data])
            conn.close()
        except Exception as e:
            logging.error(f"UNIX socket processing error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="VLab management daemon for QEMU virtual machines orchestration.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "listen",
        nargs="?",
        default="0.0.0.0:8081",
        help="Listen address and port for management HTTP server"
    )
    args = parser.parse_args()

    logging.info("Starting vlab management daemon")
    
    try:
        listen_host, port_str = args.listen.split(':')
        listen_port = int(port_str)
    except ValueError:
        logging.error(f"Invalid listen address format '{args.listen}', falling back to default configuration")
        default_value = parser.get_default("listen")
        listen_host, port_str = default_value.split(':')
        listen_port = int(port_str)

    t_http = threading.Thread(target=run_http_server, args=(listen_host, listen_port), daemon=True)
    t_sock = threading.Thread(target=run_unix_socket_listener, daemon=True)
    
    t_http.start()
    t_sock.start()

    try:
        t_http.join()
        t_sock.join()
    except KeyboardInterrupt:
        logging.info("Daemon terminated by administrator")
        if os.path.exists(DAEMON_SOCK):
            os.remove(DAEMON_SOCK)
        sys.exit(0)
