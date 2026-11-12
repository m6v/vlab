#!/usr/bin/env python3
import argparse
import html
import logging
import mimetypes
import os
import re
import socket
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlsplit

VM_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
TOKEN_PATTERN = re.compile(r"^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)")

ICONS_DIR = "/usr/share/pixmaps"
VMCTRL_BIN = "/usr/local/bin/vmctrl"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S",
)


def resolve_vnc_target(tokens_path: str, vm_name: str) -> Optional[Tuple[str, int]]:
    """Ищет (IP, порт) ВМ в файле токенов."""
    try:
        with open(tokens_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                match = TOKEN_PATTERN.match(line)
                if match and match.group(1) == vm_name:
                    return match.group(2), int(match.group(3))
    except OSError as exc:
        logging.error("Не удалось прочитать %s: %s", tokens_path, exc)
    return None


def is_port_open(target: Optional[Tuple[str, int]], timeout: float = 0.2) -> bool:
    """Проверяет доступность TCP-порта VNC."""
    if not target:
        return False
    try:
        with socket.create_connection(target, timeout=timeout):
            return True
    except OSError:
        return False


def is_path_within(target_path: str, base_dir: str) -> bool:
    """Защита от выхода за пределы каталога (Path Traversal)."""
    real_base = os.path.realpath(base_dir)
    real_target = os.path.realpath(target_path)
    return os.path.commonpath([real_base, real_target]) == real_base


class VMConsoleHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов веб-консоли виртуальной машины (режим обучаемого)."""

    def log_message(self, format: str, *args) -> None:
        logging.debug(format, *args)

    def send_payload(self, status: HTTPStatus, body: bytes, content_type: str, no_cache: bool = False) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            if no_cache:
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_file(self, file_path: str, default_mime: str = "application/octet-stream") -> None:
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        mime_type, _ = mimetypes.guess_type(file_path)
        self.send_payload(HTTPStatus.OK, data, mime_type or default_mime)

    def do_GET(self) -> None:
        parsed_url = urlsplit(self.path)
        route = parsed_url.path
        params = parse_qs(parsed_url.query)
        vm_name = params.get("vm", [""])[0].strip()

        if vm_name and not VM_NAME_PATTERN.match(vm_name):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid VM name format")
            return

        if route in ("/", "/console", "/console.html"):
            self.handle_console(vm_name)
        elif route == "/status":
            self.handle_status(vm_name)
        elif route == "/start":
            self.handle_start(vm_name)
        elif route == "/favicon.ico":
            self.handle_favicon(vm_name)
        elif route.startswith("/novnc/"):
            self.handle_novnc_static(route[len("/novnc/"):])
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def handle_console(self, vm_name: str) -> None:
        if not vm_name:
            self.send_error(HTTPStatus.BAD_REQUEST, "Parameter 'vm' is required")
            return

        args = self.server.args
        if not resolve_vnc_target(args.token_source, vm_name):
            self.send_error(HTTPStatus.NOT_FOUND, f"VM '{vm_name}' not registered")
            return

        template_file = os.path.join(args.webroot, "console.html")
        try:
            with open(template_file, "r", encoding="utf-8") as f:
                raw_html = f.read()
        except OSError:
            logging.error("Шаблон не найден: %s", template_file)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "UI Template missing")
            return

        rendered = Template(raw_html).safe_substitute(
            vm_name=html.escape(vm_name),
            proxy_port=args.proxy_port,
        )
        self.send_payload(HTTPStatus.OK, rendered.encode("utf-8"), "text/html; charset=utf-8")

    def handle_status(self, vm_name: str) -> None:
        target = resolve_vnc_target(self.server.args.token_source, vm_name) if vm_name else None
        status = b"RUNNING" if is_port_open(target) else b"STOPPED"
        self.send_payload(HTTPStatus.OK, status, "text/plain; charset=utf-8")

    def handle_start(self, vm_name: str) -> None:
        if not vm_name:
            self.send_error(HTTPStatus.BAD_REQUEST, "Parameter 'vm' is required")
            return

        logging.info("API: Запуск виртуальной машины '%s'", vm_name)
        subprocess.Popen(
            [VMCTRL_BIN, "start", vm_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.send_payload(HTTPStatus.OK, b"OK", "text/plain; charset=utf-8")

    def handle_favicon(self, vm_name: str) -> None:
        default_icon = os.path.join(ICONS_DIR, "default.png")
        candidate = os.path.join(ICONS_DIR, f"{vm_name}.png") if vm_name else default_icon

        if not os.path.isfile(candidate):
            candidate = default_icon

        if not os.path.isfile(candidate):
            self.send_payload(HTTPStatus.NO_CONTENT, b"", "image/png")
            return

        self.send_file(candidate, default_mime="image/png")

    def handle_novnc_static(self, relative_path: str) -> None:
        novncroot = self.server.args.novncroot
        target_file = os.path.join(novncroot, relative_path)

        if not is_path_within(target_file, novncroot) or not os.path.isfile(target_file):
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        self.send_file(target_file, default_mime="text/plain")


def main() -> None:
    default_webroot = os.path.dirname(os.path.abspath(__file__))

    parser = argparse.ArgumentParser(description="vnc2ws-server: Student VM console server")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081")
    parser.add_argument("--proxy-port", type=int, default=8080)
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    parser.add_argument("--webroot", default=default_webroot)
    parser.add_argument("--novncroot", default="/usr/share/novnc")
    args = parser.parse_args()

    args.webroot = os.path.abspath(args.webroot)
    args.novncroot = os.path.abspath(args.novncroot)

    host, _, port_str = args.listen.rpartition(":")
    addr = (host or "0.0.0.0", int(port_str))

    server = ThreadingHTTPServer(addr, VMConsoleHandler)
    server.args = args

    logging.info("Student Console Server started on http://%s:%s", addr[0], addr[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
        server.server_close()


if __name__ == "__main__":
    main()
