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
VM_NAME_SAFE_REGEXP = re.compile(r'^[a-zA-Z0-9_-]+$')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

def get_vnc_address(vm_name, tokens_path):
    if not os.path.exists(tokens_path) or not os.access(tokens_path, os.R_OK):
        logging.error(f"Configuration file error: {tokens_path}")
        return None
    with open(tokens_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            if ':' in line:
                name, addr = line.split(':', 1)
                if name.strip() == vm_name:
                    return addr.strip()
    return None

def is_vm_running(vnc_addr):
    if not vnc_addr or ':' not in vnc_addr: return False
    try:
        host, port_str = vnc_addr.split(':')
        with socket.create_connection((host, int(port_str)), timeout=0.5):
            return True
    except Exception:
        return False

class VLabManagementHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.debug(format % args)

    def do_GET(self):
        parsed_url = urlparse(self.path)
        query_params = parse_qs(parsed_url.query)

        # Безопасный сбор строковых параметров (исправлен баг vms.strip())
        vms = query_params.get("vm", [""])
        vm_name = vms[0].strip() if vms and vms[0] else ""
        tokens_path = self.server.tokens_path
        webroot = self.server.webroot

        if vm_name and not VM_NAME_SAFE_REGEXP.match(vm_name):
            self.send_error(400, "Bad Request: Invalid VM name format")
            return

        # API: Запуск ВМ
        if parsed_url.path == "/start" and vm_name:
            logging.info(f"Command execution: Start VM '{vm_name}'")
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # API: Статус ВМ
        if parsed_url.path == "/status":
            vnc_addr = get_vnc_address(vm_name, tokens_path) if vm_name else None
            status = "RUNNING" if is_vm_running(vnc_addr) else "STOPPED"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(status.encode())
            return

        # UI: Раздача фавиконок из системного каталога pixmaps
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
            self.end_headers()
            self.wfile.write(icon_data)
            return

        # UI: Раздача статических файлов noVNC с MIME-типами
        if parsed_url.path.startswith("/novnc/"):
            relative_path = parsed_url.path.replace("/novnc/", "", 1)
            file_path = os.path.normpath(os.path.join(webroot, relative_path))
            
            # Защита от выхода за пределы webroot директории (Path Traversal Protection)
            if not file_path.startswith(os.path.normpath(webroot)) or not os.path.exists(file_path) or os.path.isdir(file_path):
                self.send_error(404, "File not found")
                return
                
            content_type = "text/plain"
            if file_path.endswith(".js"): content_type = "application/javascript"
            elif file_path.endswith(".css"): content_type = "text/css"
            elif file_path.endswith(".png"): content_type = "image/png"
            elif file_path.endswith(".html"): content_type = "text/html"

            with open(file_path, 'rb') as f:
                file_data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(file_data)
            return

        # UI: Выдача главной страницы интерфейса
        if parsed_url.path == "/":
            if not vm_name or not get_vnc_address(vm_name, tokens_path):
                self.send_error(404, f"VM '{vm_name}' not registered")
                return
            if not os.path.exists(TEMPLATE_PATH):
                self.send_error(500, "Internal Server Error: Template asset missing")
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
    def __init__(self, server_address, RequestHandlerClass, proxy_port, tokens_path, webroot):
        self.proxy_port = proxy_port
        self.tokens_path = tokens_path
        self.webroot = os.path.abspath(webroot)
        super().__init__(server_address, RequestHandlerClass)

def valid_listen_address(netloc):
    try:
        host, port_str = netloc.rsplit(':', 1)
        return (host, int(port_str))
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' is not a valid listen address.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Web Console Pro Server")
    parser.add_argument("listen", nargs="?", type=valid_listen_address, default="0.0.0.0:8081")
    parser.add_argument("--proxy-port", type=int, default=8080)
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    parser.add_argument("--webroot", default="/usr/share/novnc/")
    args = parser.parse_args()

    if not os.path.exists(args.webroot):
        logging.fatal(f"[FATAL] noVNC directory '{args.webroot}' missing")
        sys.exit(1)

    logging.info(f"Management server launched on {args.listen}")
    server = CleanThreadingHTTPServer(args.listen, VLabManagementHandler, args.proxy_port, args.token_source, args.webroot)
    try: server.serve_forever()
    except KeyboardInterrupt: logging.info("Shutdown completed.")
