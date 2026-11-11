#!/usr/bin/env python3
import argparse
import json
import logging
import mimetypes
import os
import re
import socket
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

TOKEN_PATTERN = re.compile(r"^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S",
)


def load_raw_tokens(tokens_path: str) -> List[Dict[str, Any]]:
    """Читает файл с токенами и возвращает список словарей с параметрами ВМ."""
    vms: List[Dict[str, Any]] = []
    try:
        with open(tokens_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = TOKEN_PATTERN.match(line)
                if match:
                    vms.append({
                        "token": match.group(1),
                        "ip": match.group(2),
                        "port": int(match.group(3)),
                    })
    except OSError as exc:
        logging.error("Не удалось прочитать файл токенов %s: %s", tokens_path, exc)
    return vms


def is_port_open(host: str, port: int, timeout: float = 0.05) -> bool:
    """Проверяет доступность TCP-порта VNC."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_path_within(target_path: str, base_dir: str) -> bool:
    """Защита от выхода за пределы каталога (Path Traversal)."""
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(target_path)
    return os.path.commonpath([real_base, real_target]) == real_base


class WebPanelHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов обзорной панели управления ВМ."""

    def log_message(self, format: str, *args) -> None:
        logging.debug(format, *args)

    def send_payload(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_payload(status, body, "application/json; charset=utf-8")

    def send_safe_static(self, base_dir: str, relative_path: str, default_mime: str = "application/octet-stream") -> None:
        """Безопасная отдача статического файла из указанного каталога."""
        clean_rel = relative_path.lstrip("/")
        target_file = os.path.join(base_dir, clean_rel)

        if not is_path_within(target_file, base_dir) or not os.path.isfile(target_file):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        try:
            with open(target_file, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        mime_type, _ = mimetypes.guess_type(target_file)
        self.send_payload(HTTPStatus.OK, data, mime_type or default_mime)

    def do_GET(self) -> None:
        route = urlsplit(self.path).path

        if route == "/" or route == "/panel.html":
            self.send_safe_static(self.server.args.webroot, "panel.html", "text/html; charset=utf-8")
        elif route == "/api/tokens":
            self.handle_tokens()
        elif route == "/api/ping":
            self.handle_ping()
        elif route.startswith("/novnc/"):
            rel_path = route[len("/novnc/"):]
            self.send_safe_static(self.server.args.novncroot, rel_path, "text/plain")
        else:
            # Раздача дополнительных локальных файлов из webroot (например, virt-manager.png или viewer.html)
            self.send_safe_static(self.server.args.webroot, route)

    def handle_tokens(self) -> None:
        args = self.server.args
        response = {
            "config": {
                "pythonPort": args.port,
                "proxyPort": args.wport,
            },
            "vms": load_raw_tokens(args.token_source),
        }
        self.send_json(response)

    def handle_ping(self) -> None:
        vms = load_raw_tokens(self.server.args.token_source)
        results = {
            f"{vm['ip']}:{vm['token']}": is_port_open(vm["ip"], vm["port"])
            for vm in vms
        }
        self.send_json(results)


def main() -> None:
    default_webroot = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser(
        description="vnc2ws-webpanel: Control panel and state monitoring scanner."
    )
    parser.add_argument("-b", "--bind", default="0.0.0.0", help="IP-адрес прослушивания (default: 0.0.0.0)")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Порт веб-интерфейса панели (default: 8000)")
    parser.add_argument("-w", "--wport", "--proxy-port", dest="wport", type=int, default=8080, help="Порт WebSocket-прокси (default: 8080)")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf", help="Путь к файлу tokens.conf")
    parser.add_argument("--webroot", default=default_webroot, help="Каталог с panel.html и иконками")
    parser.add_argument("--novncroot", default="/usr/share/novnc", help="Системный каталог noVNC")
    args = parser.parse_args()

    args.webroot = os.path.abspath(args.webroot)
    args.novncroot = os.path.abspath(args.novncroot)

    panel_file = os.path.join(args.webroot, "panel.html")
    if not os.path.isdir(args.novncroot) or not os.path.isfile(panel_file):
        logging.fatal("[FATAL] Критические ресурсы (novncroot или panel.html) не найдены!")
        sys.exit(1)

    addr: Tuple[str, int] = (args.bind, args.port)
    server = ThreadingHTTPServer(addr, WebPanelHandler)
    server.args = args

    logging.info("Веб-панель запущена на http://%s:%s (webroot: %s)", args.bind, args.port, args.webroot)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Остановка сервера...")
        server.server_close()


if __name__ == "__main__":
    main()
