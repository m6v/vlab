#!/usr/bin/env python3
import os
import sys
import re
import socket
import logging
import argparse
import subprocess
import mimetypes
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TEMPLATE_PATH = "index.html"
VM_NAME_SAFE_REGEXP = re.compile(r'^[a-zA-Z0-9_-]+$')
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')
TOKENS_PATH = ""

def get_vnc_address(vm_name):
    if not os.path.exists(TOKENS_PATH) or not os.access(TOKENS_PATH, os.R_OK):
        logging.error(f"Файл конфигурации токенов недоступен: {TOKENS_PATH}")
        return None
    with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            match = TOKEN_REGEXP.match(line)
            if match and match.group(1) == vm_name:
                return match.group(2), int(match.group(3))
    return None

def is_vm_running(vnc_addr_tuple):
    if not vnc_addr_tuple: return False
    try:
        host, port = vnc_addr_tuple
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False

class VLabManagementHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.debug(format % args)

    def send_simple_response(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if '?' in self.path:
            path, query_str = self.path.split('?', 1)
        else:
            path = self.path
            query_str = ""

        params = parse_qs(query_str)
        vms = params.get("vm", [""])
        vm_name = vms[0].strip() if vms and vms[0] else ""
        webroot = self.server.webroot

        if vm_name and not VM_NAME_SAFE_REGEXP.match(vm_name):
            self.send_error(400, "Invalid VM name format")
            return

        if path == "/start" and vm_name:
            logging.info(f"API: Запуск виртуальной машины '{vm_name}'")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.send_simple_response(200, b"OK", "text/plain")
            return

        if path == "/status":
            vnc_addr = get_vnc_address(vm_name) if vm_name else None
            status = "RUNNING" if is_vm_running(vnc_addr) else "STOPPED"
            self.send_simple_response(200, status.encode(), "text/plain")
            return

        if path == "/favicon.ico":
            icons_dir = "/usr/share/pixmaps"
            icon_path = os.path.join(icons_dir, f"{vm_name}.png") if vm_name else ""
            if not icon_path or not os.path.exists(icon_path):
                icon_path = os.path.join(icons_dir, "default.png")
            if not os.path.exists(icon_path):
                self.send_simple_response(204, b"", "image/png")
                return
            with open(icon_path, 'rb') as f:
                self.send_simple_response(200, f.read(), "image/png")
            return

        if path.startswith("/novnc/"):
            relative_path = path.replace("/novnc/", "", 1)
            file_path = os.path.normpath(os.path.join(webroot, relative_path))
            if not file_path.startswith(os.path.normpath(webroot)) or not os.path.exists(file_path) or os.path.isdir(file_path):
                self.send_error(404, "File not found")
                return
            content_type, _ = mimetypes.guess_type(file_path)
            with open(file_path, 'rb') as f:
                self.send_simple_response(200, f.read(), content_type or "text/plain")
            return

        if path == "/":
            if not vm_name or not get_vnc_address(vm_name):
                self.send_error(404, f"VM '{vm_name}' not registered")
                return
            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                html = f.read().replace("{{.VMName}}", vm_name).replace("{{.ProxyPort}}", str(self.server.proxy_port))
            self.send_simple_response(200, html.encode('utf-8'), "text/html; charset=utf-8")
            return

        self.send_error(404, "Not Found")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081")
    parser.add_argument("--proxy-port", type=int, default=8080)
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    parser.add_argument("--webroot", default="/usr/share/novnc/")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    if ":" in args.listen:
        host, port_str = args.listen.rsplit(':', 1)
        addr = (host, int(port_str))
    else:
        addr = ("0.0.0.0", int(args.listen))

    server = ThreadingHTTPServer(addr, VLabManagementHandler)
    server.proxy_port = args.proxy_port
    server.webroot = os.path.abspath(args.webroot)
    logging.info(f"UI Server started on http://{addr[0]}:{addr[1]}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
