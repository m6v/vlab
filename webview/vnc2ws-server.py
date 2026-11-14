#!/usr/bin/env python3
import argparse
import html
import logging
import mimetypes
from pathlib import Path
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
VNC_RE = re.compile(r"(?:^|\s)-vnc(?:\s+|=)(\S+)")

ICONS_DIR = Path("/usr/share/pixmaps")
VMCTRL_BIN = "/usr/local/bin/vmctrl"

VncMap = Dict[str, Tuple[str, int]]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def parse_vnc_spec(spec: str) -> Tuple[str, int]:
    """Преобразует QEMU '-vnc host:display[,opts]' в (ip, tcp_port)."""
    target, _, _ = spec.strip().partition(",")
    host, sep, disp_str = target.rpartition(":")
    if not sep or not disp_str.isdigit():
        raise ValueError(f"invalid -vnc specification '{spec}'")

    host = host.strip()
    if not host or host == "0.0.0.0":
        host = "127.0.0.1"

    port = int(disp_str)
    if port < 5900:
        port += 5900

    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range in '{spec}'")

    socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return host, port


def load_qemu_configs(configs_dir: Path) -> VncMap:
    """Сканирует *.conf файлы QEMU и извлекает адреса -vnc."""
    if not configs_dir.is_dir():
        logging.critical("Configs directory not found: %s", configs_dir)
        sys.exit(1)

    vms: VncMap = {}
    for conf in sorted(configs_dir.glob("*.conf")):
        lines = [
            l.strip().rstrip("\\")
            for l in conf.read_text(encoding="utf-8").splitlines()
            if not l.lstrip().startswith("#")
        ]
        match = VNC_RE.search(" ".join(lines))
        if match:
            try:
                host, port = parse_vnc_spec(match.group(1))
                vms[conf.stem] = (host, port)
                logging.info("Loaded %s VNC socket (%s:%s)", conf.stem, host, port)
            except Exception as e:
                logging.error("Failed to parse %s: %s", conf.name, e)

    logging.info("Total VM configs loaded: %d", len(vms))
    return vms


def is_port_open(target: Optional[Tuple[str, int]], timeout: float = 0.2) -> bool:
    if not target:
        return False
    try:
        with socket.create_connection(target, timeout=timeout):
            return True
    except OSError:
        return False


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

    def send_safe_file(self, base_dir: Path, rel_path: str, default_mime: str = "application/octet-stream") -> None:
        try:
            target = (base_dir / rel_path.lstrip("/")).resolve()
            base_resolved = base_dir.resolve()
            if base_resolved != target and base_resolved not in target.parents:
                raise ValueError("Path traversal")
            data = target.read_bytes()
        except Exception:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        mime, _ = mimetypes.guess_type(str(target))
        self.send_payload(HTTPStatus.OK, data, mime or default_mime)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)
        vm_list = params.get("vm")
        vm_name = vm_list.pop(0).strip() if vm_list else ""

        if vm_name and not VM_NAME_PATTERN.match(vm_name):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid VM name format")
            return

        if route in ("/", "/console", "/console.html"):
            self.handle_console(vm_name)
        elif route == "/status":
            target = self.server.vms.get(vm_name) if vm_name else None
            status = b"RUNNING" if is_port_open(target) else b"STOPPED"
            self.send_payload(HTTPStatus.OK, status, "text/plain; charset=utf-8", no_cache=True)
        elif route == "/start":
            self.handle_start(vm_name)
        elif route == "/favicon.ico":
            self.handle_favicon(vm_name)
        elif route.startswith("/novnc/"):
            self.send_safe_file(self.server.novncroot, route[len("/novnc/"):], "text/plain")
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def handle_console(self, vm_name: str) -> None:
        if not vm_name:
            self.send_error(HTTPStatus.BAD_REQUEST, "Parameter 'vm' is required")
            return
        if vm_name not in self.server.vms:
            self.send_error(HTTPStatus.NOT_FOUND, f"VM '{vm_name}' not found")
            return

        template_file = self.server.webroot / "console.html"
        try:
            raw_html = template_file.read_text(encoding="utf-8")
        except OSError:
            logging.error("Template not found: %s", template_file)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "UI Template missing")
            return

        short_name, _, _ = vm_name.partition("@")
        rendered = Template(raw_html).safe_substitute(
            vm_name=html.escape(vm_name),
            display_name=html.escape(short_name),
            proxy_port=self.server.proxy_port,
        )
        self.send_payload(HTTPStatus.OK, rendered.encode("utf-8"), "text/html; charset=utf-8")

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
        short_name, _, _ = vm_name.partition("@")
        candidate = ICONS_DIR / f"{short_name}.png" if short_name else ICONS_DIR / "default.png"
        if not candidate.is_file():
            candidate = ICONS_DIR / "default.png"
        if not candidate.is_file():
            self.send_payload(HTTPStatus.NO_CONTENT, b"", "image/png")
            return
        self.send_safe_file(ICONS_DIR, candidate.name, "image/png")


def main() -> None:
    default_webroot = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="vnc2ws-server: VM Console Server")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081")
    parser.add_argument("--proxy-port", type=int, default=8080)
    parser.add_argument("--config-dir", default="/srv/qemu/config")
    parser.add_argument("--webroot", default=str(default_webroot))
    parser.add_argument("--novncroot", default="/usr/share/novnc")
    args = parser.parse_args()

    vms = load_qemu_configs(Path(args.config_dir))

    try:
        host, port_str = args.listen.rpartition(":")[::2]
        addr = (host or "0.0.0.0", int(port_str))
        server = ThreadingHTTPServer(addr, VMConsoleHandler)
    except Exception as e:
        logging.critical("Failed to start server on '%s': %s", args.listen, e)
        sys.exit(1)

    server.vms = vms
    server.proxy_port = args.proxy_port
    server.webroot = Path(args.webroot).resolve()
    server.novncroot = Path(args.novncroot).resolve()

    logging.info("Student Console Server started on http://%s:%s", addr[0], addr[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
        server.server_close()


if __name__ == "__main__":
    main()
