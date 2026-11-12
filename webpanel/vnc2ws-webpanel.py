#!/usr/bin/env python3
import argparse
import html
import json
import logging
import mimetypes
import os
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

VM_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
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


def resolve_vnc_target(tokens_path: str, vm_name: str) -> Optional[Tuple[str, int]]:
    """Ищет (IP, порт) конкретной ВМ."""
    for vm in load_raw_tokens(tokens_path):
        if vm["token"] == vm_name:
            return vm["ip"], vm["port"]
    return None


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
    """Обработчик HTTP-запросов обзорной панели управления ВМ (режим инструктора)."""

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
        parsed_url = urlsplit(self.path)
        route = parsed_url.path
        params = parse_qs(parsed_url.query)
        vm_name = params.get("vm", [""])[0].strip()

        if vm_name and not VM_NAME_PATTERN.match(vm_name):
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid VM name format")
            return

        if route in ("/", "/panel.html"):
            self.send_safe_static(self.server.args.webroot, "panel.html", "text/html; charset=utf-8")
        elif route in ("/console", "/console.html"):
            self.handle_console(vm_name)
        elif route == "/status":
            self.handle_status(vm_name)
        elif route == "/api/tokens":
            self.handle_tokens()
        elif route == "/api/ping":
            self.handle_ping()
        elif route.startswith("/novnc/"):
            rel_path = route[len("/novnc/"):]
            self.send_safe_static(self.server.args.novncroot, rel_path, "text/plain")
        else:
            self.send_safe_static(self.server.args.webroot, route)

    def handle_console(self, vm_name: str) -> None:
        """Отдает общий шаблон console.html из пакета vnc2ws-console в режиме наблюдения."""
        if not vm_name:
            self.send_error(HTTPStatus.BAD_REQUEST, "Parameter 'vm' is required")
            return

        args = self.server.args
        if not resolve_vnc_target(args.token_source, vm_name):
            self.send_error(HTTPStatus.NOT_FOUND, f"VM '{vm_name}' not registered")
            return

        template_file = os.path.join(args.consoledir, "console.html")
        try:
            with open(template_file, "r", encoding="utf-8") as f:
                raw_html = f.read()
        except OSError:
            logging.error("Шаблон консоли не найден: %s", template_file)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Console template missing")
            return

        rendered = Template(raw_html).safe_substitute(
            vm_name=html.escape(vm_name),
            proxy_port=args.wport,
            allow_start="false",
        )
        self.send_payload(HTTPStatus.OK, rendered.encode("utf-8"), "text/html; charset=utf-8")

    def handle_status(self, vm_name: str) -> None:
        """Точечная проверка статуса одной ВМ (используется внутри console.html)."""
        target = resolve_vnc_target(self.server.args.token_source, vm_name) if vm_name else None
        status = b"RUNNING" if (target and is_port_open(target[0], target[1], timeout=0.2)) else b"STOPPED"
        self.send_payload(HTTPStatus.OK, status, "text/plain; charset=utf-8")

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
        """Параллельное сканирование всех ВМ для обновления дерева инструктора."""
        vms = load_raw_tokens(self.server.args.token_source)
        if not vms:
            self.send_json({})
            return

        def check_vm(vm: Dict[str, Any]) -> Tuple[str, bool]:
            return f"{vm['ip']}:{vm['token']}", is_port_open(vm["ip"], vm["port"])

        with ThreadPoolExecutor(max_workers=min(32, len(vms))) as pool:
            results = dict(pool.map(check_vm, vms))

        self.send_json(results)


def main() -> None:
    default_webroot = os.path.dirname(os.path.abspath(__file__))
    # По умолчанию ищем console.html в системном каталоге пакета vnc2ws-console,
    # а при локальном запуске — рядом со скриптом
    system_console_dir = "/usr/share/vnc2ws"
    default_console_dir = system_console_dir if os.path.isfile(os.path.join(system_console_dir, "console.html")) else default_webroot

    parser = argparse.ArgumentParser(
        description="vnc2ws-webpanel: Instructor control panel and state monitoring scanner."
    )
    parser.add_argument("-b", "--bind", default="0.0.0.0", help="IP-адрес прослушивания (default: 0.0.0.0)")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Порт веб-интерфейса панели (default: 8000)")
    parser.add_argument("-w", "--wport", "--proxy-port", dest="wport", type=int, default=8080, help="Порт WebSocket-прокси (default: 8080)")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf", help="Путь к файлу tokens.conf")
    parser.add_argument("--webroot", default=default_webroot, help="Каталог с panel.html и иконками")
    parser.add_argument("--consoledir", default=default_console_dir, help="Каталог с шаблоном console.html из пакета vnc2ws-console")
    parser.add_argument("--novncroot", default="/usr/share/novnc", help="Системный каталог noVNC")
    args = parser.parse_args()

    args.webroot = os.path.abspath(args.webroot)
    args.consoledir = os.path.abspath(args.consoledir)
    args.novncroot = os.path.abspath(args.novncroot)

    panel_file = os.path.join(args.webroot, "panel.html")
    console_file = os.path.join(args.consoledir, "console.html")
    if not os.path.isdir(args.novncroot) or not os.path.isfile(panel_file) or not os.path.isfile(console_file):
        logging.fatal("[FATAL] Критические ресурсы (novncroot, panel.html или console.html) не найдены!")
        sys.exit(1)

    addr: Tuple[str, int] = (args.bind, args.port)
    server = ThreadingHTTPServer(addr, WebPanelHandler)
    server.args = args

    logging.info("Веб-панель запущена на http://%s:%s (webroot: %s, consoledir: %s)", args.bind, args.port, args.webroot, args.consoledir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Остановка сервера...")
        server.server_close()


if __name__ == "__main__":
    main()
