#!/usr/bin/env python3
import argparse
import asyncio
from functools import partial
import logging
from pathlib import Path
import re
import socket
import sys
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs

try:
    from websockets.asyncio.server import serve as ws_serve
    LEGACY_WS = False
except ModuleNotFoundError:
    from websockets import serve as ws_serve
    LEGACY_WS = True

class CapitalizeFormatter(logging.Formatter):
    def format(self, record):
        original = record.getMessage()
        record.msg = original[:1].upper() + original[1:]
        record.args = ()
        return super().format(record)

handler = logging.StreamHandler()
handler.setFormatter(CapitalizeFormatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[handler])

VncMap = Dict[str, Tuple[str, int]]
VNC_RE = re.compile(r"(?:^|\s)-vnc(?:\s+|=)(\S+)")


def parse_endpoint(spec: str, qemu_vnc: bool = False) -> Tuple[str, int]:
    """Разбирает 'host:port' или QEMU '-vnc host:display[,opts]' в (ip, tcp_port)."""
    target, _, _ = spec.strip().partition(",")
    host, sep, port_str = target.rpartition(":")
    if not sep or not port_str.isdigit():
        raise ValueError(f"invalid address '{spec}'")

    host = host.strip()
    if qemu_vnc and (not host or host == "0.0.0.0"):
        host = "127.0.0.1"

    port = int(port_str)
    if qemu_vnc and port < 5900:
        port += 5900

    if not host or not (1 <= port <= 65535):
        raise ValueError(f"out of range in '{spec}'")

    socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return host, port


def load_qemu_configs(configs_dir: str) -> VncMap:
    """Сканирует *.conf файлы QEMU и извлекает адреса -vnc."""
    base_dir = Path(configs_dir)
    if not base_dir.is_dir():
        logging.critical("Configs directory not found: %s", configs_dir)
        sys.exit(1)

    vms: VncMap = {}
    for conf in sorted(base_dir.glob("*.conf")):
        lines = [
            l.strip().rstrip("\\")
            for l in conf.read_text(encoding="utf-8").splitlines()
            if not l.lstrip().startswith("#")
        ]
        match = VNC_RE.search(" ".join(lines))
        if match:
            try:
                host, port = parse_endpoint(match.group(1), qemu_vnc=True)
                vms[conf.stem] = (host, port)
                logging.info("Loaded %s VNC endpoint (%s:%s)", conf.stem, host, port)
            except Exception as e:
                logging.error("Failed to parse %s: %s", conf.name, e)
    return vms


async def ws_handler(websocket, path: Optional[str] = None, *, vnc_map: VncMap):
    """Асинхронный воркер WebSocket-соединения."""
    uri = (websocket.path if "?" in websocket.path else (path or "")) if LEGACY_WS else getattr(websocket.request, "uri", websocket.request.path or "")
    params = parse_qs(uri.partition("?")[2])
    tokens = params.get("token") or params.get("path")
    token = tokens[0].strip() if tokens else None

    target = vnc_map.get(token) if token else None
    if not target:
        await websocket.close(1008, "Unknown or missing token")
        return

    host, port = target
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as e:
        logging.error("[%s] VNC connection failed (%s:%s): %s", token, host, port, e)
        await websocket.close(1011, "VNC backend unavailable")
        return

    logging.info("Connected to %s (%s:%s)", token, host, port)

    async def browser_to_vm():
        async for msg in websocket:
            if isinstance(msg, bytes) and msg:
                writer.write(msg)
                await writer.drain()

    async def vm_to_browser():
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await asyncio.wait_for(websocket.send(data), timeout=10.0)

    tasks = [asyncio.create_task(browser_to_vm()), asyncio.create_task(vm_to_browser())]
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        writer.close()
        if hasattr(writer, "wait_closed"):
            await writer.wait_closed()
        await websocket.close()
        logging.info("Session with %s closed", token)


async def main(host: str, port: int, vnc_map: VncMap):
    logging.info(
        "Run in %s mode",
        "legacy (websockets < 13)" if LEGACY_WS else "modern (websockets >= 13)",
    )
    handler = partial(ws_handler, vnc_map=vnc_map)
    async with ws_serve(
        handler, host, port,
        subprotocols=["binary"],
        ping_interval=15,
        ping_timeout=10,
        max_size=None,
        reuse_address=True,
    ):
        await asyncio.Event().wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-bridge: WebSocket VNC proxy")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8080")
    parser.add_argument("--config-dir", default="/srv/qemu/config")
    args = parser.parse_args()

    vnc_map = load_qemu_configs(args.config_dir)
    try:
        host, port = args.listen.rpartition(":")[::2]
        asyncio.run(main(host or "0.0.0.0", int(port), vnc_map))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logging.critical("Failed to start bridge on '%s': %s", args.listen, e)
        sys.exit(1)
