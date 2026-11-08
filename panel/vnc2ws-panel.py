#!/usr/bin/env python3
import os
import sys
import re
import json
import socket
import logging
import argparse
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(BASE_DIR, "index.html")
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')
TOKENS_PATH = ""

def load_raw_tokens():
    """Читает конфигурационный файл с токенами и возвращает список структур ВМ"""
    vms_list = []
    if not os.path.exists(TOKENS_PATH) or not os.access(TOKENS_PATH, os.R_OK):
        logging.error(f"Конфигурационный файл с токенами недоступен: {TOKENS_PATH}")
        return vms_list
    try:
        with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'): continue
                match = TOKEN_REGEXP.match(line)
                if match:
                    vms_list.append({
                        "token": match.group(1),
                        "ip": match.group(2),
                        "port": int(match.group(3))
                    })
    except Exception as e:
        logging.error(f"Ошибка при чтении конфигурационного файла с токенами: {e}")
    return vms_list

class InstructorScannerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        # Унифицированное пуленепробиваемое разделение путей через partition
        path, _, _ = self.path.partition('?')
        webroot = self.server.webroot

        # REST API: Передача конфигурации служебных портов и списка виртуальных машин
        if path == '/api/tokens':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            
            api_response = {
                "config": {
                    "pythonPort": self.server.server_port,
                    "proxyPort": self.server.proxy_port
                },
                "vms": load_raw_tokens()
            }
            self.wfile.write(json.dumps(api_response).encode('utf-8'))
            return

        # REST API: Сканирование статусов виртуальных машин с поддержкой составных ключей для защиты от дубликатов имен
        if path == '/api/ping':
            vms = load_raw_tokens()
            results = {}
            for vm in vms:
                state_key = f"{vm['ip']}:{vm['token']}"
                try:
                    with socket.create_connection((vm["ip"], vm["port"]), timeout=0.05):
                        results[state_key] = True
                        continue
                except Exception: pass
                results[state_key] = False

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(results).encode('utf-8'))
            return

        # UI Статика: Раздача системных файлов noVNC
        if path.startswith("/novnc/"):
            relative_path = path.replace("/novnc/", "", 1)
            file_path = os.path.normpath(os.path.join(webroot, relative_path))
            if not file_path.startswith(os.path.normpath(webroot)) or not os.path.exists(file_path) or os.path.isdir(file_path):
                self.send_error(404, "File not found")
                return
            content_type, _ = mimetypes.guess_type(file_path)
            self.send_response(200)
            self.send_header("Content-Type", content_type or "text/plain")
            self.end_headers()
            with open(file_path, 'rb') as f:
                self.wfile.write(f.read())
            return

        # UI Статика: Раздача главной страницы
        if path == "/" or path == "/index.html":
            if not os.path.exists(TEMPLATE_PATH):
                self.send_error(404, "UI Template missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            with open(TEMPLATE_PATH, 'rb') as f:
                self.wfile.write(f.read())
            return

        self.send_error(404, "Not Found")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="vnc2ws-instructor: Enterprise static control panel and state monitoring scanner."
    )
    parser.add_argument("-p", "--port", type=int, default=8000, help="Порт веб-интерфейса Панели (default: 8000)")
    parser.add_argument("-w", "--wport", type=int, default=8080, help="Порт работающего WebSocket-шлюза проекта (default: 8080)")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf", help="Путь к оригинальному файлу tokens.conf")
    parser.add_argument("--webroot", default="/usr/share/novnc/", help="Путь к системной директории noVNC")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    
    if not os.path.exists(args.webroot) or not os.path.exists(TEMPLATE_PATH):
        logging.fatal("[FATAL] Критические системные ресурсы (webroot или index.html) отсутствуют!")
        sys.exit(1)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), InstructorScannerHandler)
    server.server_port = args.port
    server.proxy_port = args.wport
    server.webroot = os.path.abspath(args.webroot)
    
    logging.info(f"Панель инструктора успешно запущена на http://0.0.0:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
