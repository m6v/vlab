#!/usr/bin/env python3
import argparse
import configparser
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit
from urllib.request import urlopen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

NodeInfo = Tuple[str, str, int]


def load_config(filepath: Path) -> Tuple[str, Path, float, List[NodeInfo]]:
    """Читает конфигурационный файл."""
    if not filepath.is_file():
        logging.critical("Config file not found: %s", filepath)
        sys.exit(1)

    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    parser.optionxform = str
    try:
        parser.read(filepath, encoding="utf-8")
    except Exception as e:
        logging.critical("Failed to parse %s: %s", filepath, e)
        sys.exit(1)

    default_webroot = str(Path(__file__).resolve().parent)
    listen = parser.get("common", "listen", fallback="0.0.0.0:8000").strip()
    webroot = Path(parser.get("common", "webroot", fallback=default_webroot).strip()).resolve()
    timeout = parser.getfloat("common", "timeout", fallback=0.6)

    if "nodes" not in parser:
        logging.critical("Section [nodes] missing in %s", filepath)
        sys.exit(1)

    nodes: List[NodeInfo] = []
    for addr, label in parser.items("nodes"):
        host, sep, port_str = addr.strip().rpartition(":")
        if sep and port_str.isdigit():
            h, p = host.strip(), int(port_str)
        else:
            h, p = addr.strip(), 8081
        nodes.append((label.strip() or h, h, p))

    logging.info("Loaded lab nodes: %d", len(nodes))
    return listen, webroot, timeout, nodes


def fetch_node_status(node: NodeInfo, timeout: float = 0.6) -> Dict[str, Any]:
    """Запрашивает /api/vms у рабочего места."""
    label, host, console_port = node
    url = f"http://{host}:{console_port}/api/vms"
    try:
        with urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "label": label,
            "host": host,
            "consolePort": console_port,
            "online": True,
            "vms": data.get("vms", []),
        }
    except Exception:
        return {
            "label": label,
            "host": host,
            "consolePort": console_port,
            "online": False,
            "vms": [],
        }


class WebPanelHandler(BaseHTTPRequestHandler):
    """Обработчик HTTP-запросов обзорной панели инструктора."""

    def log_message(self, format: str, *args) -> None:
        logging.debug(format, *args)

    def send_payload(self, status: HTTPStatus, body: bytes, content_type: str, no_cache: bool = False) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
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
        route = urlsplit(self.path).path

        if route in ("/", "/panel.html"):
            self.send_safe_file(self.server.webroot, "panel.html", "text/html; charset=utf-8")
        elif route == "/api/nodes":
            self.handle_nodes()
        else:
            self.send_safe_file(self.server.webroot, route)

    def handle_nodes(self) -> None:
        nodes = self.server.nodes
        if not nodes:
            self.send_payload(HTTPStatus.OK, b"[]", "application/json; charset=utf-8", no_cache=True)
            return

        worker = partial(fetch_node_status, timeout=self.server.timeout)
        with ThreadPoolExecutor(max_workers=min(32, len(nodes))) as pool:
            result = list(pool.map(worker, nodes))

        body = json.dumps(result).encode("utf-8")
        self.send_payload(HTTPStatus.OK, body, "application/json; charset=utf-8", no_cache=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="vnc2ws-webpanel: Instructor web panel")
    parser.add_argument("-c", "--config", default="/srv/qemu/webpanel.conf", help="Path to config file")
    args = parser.parse_args()

    listen, webroot, timeout, nodes = load_config(Path(args.config))

    if not (webroot / "panel.html").is_file():
        logging.critical("panel.html not found in %s", webroot)
        sys.exit(1)

    try:
        host, port_str = listen.rpartition(":")[::2]
        addr = (host or "0.0.0.0", int(port_str))
        server = ThreadingHTTPServer(addr, WebPanelHandler)
    except Exception as e:
        logging.critical("Failed to start webpanel on '%s': %s", listen, e)
        sys.exit(1)

    server.nodes = nodes
    server.webroot = webroot
    server.timeout = timeout

    logging.info("Instructor WebPanel started on http://%s:%s", addr, addr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down server...")
        server.server_close()


if __name__ == "__main__":
    main()
