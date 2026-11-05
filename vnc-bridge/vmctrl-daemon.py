#!/usr/bin/env python3
import os
import sys
import socket
import threading
import subprocess
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

# Динамическая конфигурация портов
runtime_config = {"http_port": 8081, "proxy_port": 8080}

def is_vm_running(vm_name):
    """Checks VM availability by probing its mapped VNC port from tokens.conf"""
    try:
        # 1. Получаем целевой VNC-адрес (host:port) из tokens.conf
        if not os.path.exists(TOKENS_PATH):
            return False
            
        vnc_target = None
        target_prefix = f"{vm_name}:"
        
        with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith(target_prefix):
                    # Извлекаем всё, что после двоеточия (например, 127.0.0.1:5913)
                    parts = line.split(':', 1)
                    if len(parts) > 1:
                        vnc_target = parts[1].strip()
                    break
                    
        if not vnc_target:
            return False

        # 2. Выделяем IP и порт
        host, port_str = vnc_target.split(':')
        port = int(port_str)

        # 3. Делаем быстрый тестовый коннект к реальному VNC-порту
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.1) # Микротаймаут 100мс
        s.connect((host, port))
        s.close()
        
        # Если коннект прошел успешно — машина гарантированно запущена!
        return True
    except Exception:
        # Если порт закрыт или упал таймаут — машина выключена
        return False

def check_token_exists(vm_name):
    """Проверяет, зарегистрирована ли целевая ВМ в файле токенов"""
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
    """Обработчик HTTP-запросов веб-интерфейса пульта управления лабораторией"""
    
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        # 1. API ЭНДПОИНТ: Запуск ВМ
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

        # 2. API ЭНДПОИНТ: Проверка статуса (Отдает чистый технический статус текстом)
        if parsed_url.path == "/status":
            vms = query_params.get("vm", [""])
            vm_name = vms[0].strip() if vms and vms[0] else ""
            
            status_text = "RUNNING" if is_vm_running(vm_name) else "STOPPED"
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(status_text.encode('utf-8'))
            return

        # 3. UI ЭНДПОИНТ: Выдача интерфейса
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

            # Чистая линейная инжекция переменных
            html_content = html_content.replace("{{.VMName}}", vm_name)
            html_content = html_content.replace("{{.ProxyPort}}", str(runtime_config["proxy_port"]))

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
            return

        self.send_error(404, "Not Found")

def run_http_server(host, port):
    """Инициализация и бесконечный цикл прослушивания HTTP веб-сервера"""
    server_address = (host, port)
    httpd = HTTPServer(server_address, ManagementHTTPHandler)
    logging.info(f"HTTP management server listening on {host}:{port}")
    httpd.serve_forever()

def run_unix_socket_listener():
    """Слушатель локального UNIX-сокета для системной интеграции"""
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
    parser.add_argument(
        "--proxy-port",
        type=int,
        default=8080,
        dest="proxy_port",
        help="Runtime port of the VNC proxy bridge gateway"
    )
    args = parser.parse_args()

    runtime_config["proxy_port"] = args.proxy_port

    try:
        listen_host, port_str = args.listen.split(':')
        runtime_config["http_port"] = int(port_str)
    except ValueError:
        logging.error(f"Invalid listen address format '{args.listen}', falling back to default configuration")
        default_value = parser.get_default("listen")
        listen_host, port_str = default_value.split(':')
        runtime_config["http_port"] = int(port_str)

    logging.info("Starting vlab management daemon")

    t_http = threading.Thread(target=run_http_server, args=(listen_host, runtime_config["http_port"]), daemon=True)
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
