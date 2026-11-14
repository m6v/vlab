#!/usr/bin/env python3
import argparse
import html
import logging
import mimetypes
import os
import re
import socket
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

VM_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+(?:@[a-zA-Z0-9_.-]+)?$")
VNC_ARG_RE = re.compile(r"(?:^|\s)-vnc(?:\s+|=)(\S+)")

ICONS_DIR = "/usr/share/pixmaps"
VMCTRL_BIN = "/usr/local/bin/vmctrl"

VncMap = Dict[str, Tuple[str, int]]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y/%m/%d %H:%M:%S",
)


def parse_endpoint(addr: str) -> Tuple[str, int]:
    """Разбирает и валидирует адрес 'host:port' для прослушивания."""
    host, sep, port_str = addr.strip().rpartition(":")
    port = int(port_str)
    if not sep or not host or not (1 <= port <= 65535):
        raise ValueError(f"invalid endpoint address '{addr}'")
    socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return host.strip(), port


def parse_vnc_spec(spec: str) -> Tuple[str, int]:
    """Преобразует аргумент QEMU '-vnc host:display[,opts]' в (ip, tcp_port)."""
    target, _, _ = spec.strip().partition(",")
    host_part, sep, disp_part = target.rpartition(":")
    if not sep or not disp_part.isdigit():
        raise ValueError(f"invalid -vnc specification '{spec}'")

    host = host_part.strip()
    if not host or host == "0.0.0.0":
        host = "127.0.0.1"

    disp = int(disp_part)
    port = disp + 5900 if disp < 5900 else disp
    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range in '{spec}'")

    socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return host, port


def load_qemu_configs(configs_dir: str) -> VncMap:
    """Сканирует каталог *.conf файлов QEMU и извлекает параметры -vnc."""
    if not os.path.isdir(configs_dir):
        logging.critical("Configs directory not found: %s", configs_dir)
        sys.exit(1)

    vms: VncMap = {}
    for filename in sorted(os.listdir(configs_dir)):
        if not filename.endswith(".conf"):
            continue

        vm_name = filename[:-5]
        conf_path = os.path.join(configs_dir, filename)
        if not os.path.isfile(conf_path):
            continue

        try:
            with open(conf_path, "r", encoding="utf-8") as f:
                parts = []
                for raw_line in f:
                    line = raw_line.split("#", 1).pop(0).rstrip(" \\\r\n\t").strip()
                    if line:
                        parts.append(line)

            full_cmd = " ".join(parts)
            match = VNC_ARG_RE.search(full_cmd)
            if not match:
                logging.warning("Skipping %s: '-vnc' parameter not found", filename)
                continue

            vms[vm_name] = parse_vnc_spec(match.group(1))
        except Exception as exc:
            logging.error("Failed to parse %s: %s", filename, exc)

    logging.info("Loaded VM configs: %d", len(vms))
    return vms


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
    """Обработчик HTTP-запросов веб-консоли виртуальной машины."""

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
        vm_list = params.get("vm")
        vm_name = vm_list.pop(0).strip() if vm_list else ""

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

        if vm_name not in self.server.vms:
            self.send_error(HTTPStatus.NOT_FOUND, f"VM '{vm_name}' not found")
            return

        args = self.server.args
        template_file = os.path.join(args.webroot, "console.html")
        try:
            with open(template_file, "r", encoding="utf-8") as f:
                raw_html = f.read()
        except OSError:
            logging.error("Template not found: %s", template_file)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "UI Template missing")
            return

        short_name, _, _ = vm_name.partition("@")
        rendered = Template(raw_html).safe_substitute(
            vm_name=html.escape(vm_name),
            display_name=html.escape(short_name),
            proxy_port=args.proxy_port,
        )
        self.send_payload(HTTPStatus.OK, rendered.encode("utf-8"), "text/html; charset=utf-8")

    def handle_status(self, vm_name: str) -> None:
        target = self.server.vms.get(vm_name) if vm_name else None
        status = b"RUNNING" if is_port_open(target) else b"STOPPED"
        self.send_payload(HTTPStatus.OK, status, "text/plain; charset=utf-8", no_cache=True)

    def handle_start(self, vm_name: str) -> None:
        if not vm_name:
            self.send_error(HTTPStatus.BAD_REQUEST, "Parameter 'vm' is required")
            return

        if vm_name not in self.server.vms:
            self.send_error(HTTPStatus.NOT_FOUND, f"VM '{vm_name}' not found")
            return

        logging.info("API: Starting VM '%s'", vm_name)
        subprocess.Popen(
            [VMCTRL_BIN, "start", vm_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.send_payload(HTTPStatus.OK, b"OK", "text/plain; charset=utf-8", no_cache=True)

    def handle_favicon(self, vm_name: str) -> None:
        default_icon = os.path.join(ICONS_DIR, "default.png")
        short_name, _, _ = vm_name.partition("@")
        candidate = os.path.join(ICONS_DIR, f"{short_name}.png") if short_name else default_icon

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
    parser.add_argument(
        "--config-dir",
        default="/srv/qemu/config",
        help="Directory with QEMU *.conf files (default: /srv/qemu/config)",
    )
    parser.add_argument("--webroot", default=default_webroot)
    parser.add_argument("--novncroot", default="/usr/share/novnc")
    args = parser.parse_args()

    try:
        addr = parse_endpoint(args.listen)
    except Exception as e:
        logging.critical("Invalid listen address '%s': %s", args.listen, e)
        sys.exit(1)

    vms = load_qemu_configs(args.config_dir)

    args.webroot = os.path.abspath(args.webroot)
    args.novncroot = os.path.abspath(args.novncroot)

    server = ThreadingHTTPServer(addr, VMConsoleHandler)
    server.args = args
    server.vms = vms
    host, port = addr
    logging.info("Student Console Server started on http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
        server.server_close()

if __name__ == "__main__":
    main()
