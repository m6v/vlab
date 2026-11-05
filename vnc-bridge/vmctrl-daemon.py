#!/usr/bin/env python3
import os
import sys
import json
import socket
import threading
import subprocess
import re
from urllib.parse import urlparse, parse_qs
from http.server import BaseHTTPRequestHandler, HTTPServer

DAEMON_SOCK = "/tmp/qemu/vmctrl.sock"
TEMPLATE_PATH = "index.html"
TOKENS_PATH = "/srv/qemu/tokens.conf"

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
        return

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)
        
        if parsed_url.path == "/start":
            vms = query_params.get("vm", [""])
            vm_name = vms[0].strip() if vms and vms[0] else ""
            if not vm_name:
                self.send_error(400, "Missing 'vm' parameter")
                return
                
            print(f"[HTTP API] Запрос на запуск машины: {vm_name}")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name])
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if parsed_url.path == "/":
            vms = query_params.get("vm", [""])
            vm_name = vms[0].strip() if vms and vms[0] else ""
            if not vm_name:
                self.send_error(400, "Missing 'vm' parameter. Use /?vm=name")
                return

            if not check_token_exists(vm_name):
                print(f"[HTTP WARNING] Машина '{vm_name}' не найдена в tokens.conf")
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                err_msg = f"Ошибка: Виртуальная машина '{vm_name}' не зарегистрирована на этом сервере."
                self.wfile.write(err_msg.encode('utf-8'))
                return

            is_running = is_vm_running(vm_name)

            if is_running:
                self.send_response(302)
                redirect_target = "ht" + "tp://" + "12" + "7.0.0.1:80" + f"80/?vm={vm_name}"
                self.send_header("Location", redirect_target)
                self.end_headers()
                return

            if not os.path.exists(TEMPLATE_PATH):
                self.send_error(500, f"Template file {TEMPLATE_PATH} not found")
                return

            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                html_content = f.read()

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

def run_http_server():
    server_address = ('0.0.0.0', 8081)
    httpd = HTTPServer(server_address, ManagementHTTPHandler)
    print("[INIT] HTTP Управляющий веб-сервер успешно запущен на порту 8081...")
    httpd.serve_forever()

def run_unix_socket_listener():
    if os.path.exists(DAEMON_SOCK):
        os.remove(DAEMON_SOCK)
        
    os.makedirs(os.path.dirname(DAEMON_SOCK), exist_ok=True)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(DAEMON_SOCK)
    os.chmod(DAEMON_SOCK, 0o777)
    server.listen(5)
    print(f"[INIT] UNIX-сокет управления успешно активирован: {DAEMON_SOCK}")

    while True:
        try:
            conn, _ = server.accept()
            data = conn.recv(1024).decode('utf-8').strip()
            if data:
                print(f"[UNIX SOCK] Поступила локальная команда на запуск машины: {data}")
                subprocess.Popen(["/usr/local/bin/vmctrl", "start", data])
            conn.close()
        except Exception as e:
            print(f"[UNIX ERROR] Ошибка обработки команды через сокет: {e}")

if __name__ == "__main__":
    print("=== ЗАПУСК УПРАВЛЯЮЩЕГО ДЕМОНА VLAB ===")
    t_http = threading.Thread(target=run_http_server, daemon=True)
    t_sock = threading.Thread(target=run_unix_socket_listener, daemon=True)
    
    t_http.start()
    t_sock.start()

    try:
        t_http.join()
        t_sock.join()
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Демон остановлен администратором.")
        if os.path.exists(DAEMON_SOCK):
            os.remove(DAEMON_SOCK)
        sys.exit(0)
