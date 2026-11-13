#!/usr/bin/env python3
import argparse
import asyncio
from functools import partial
import ipaddress
import logging
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TokensMap = Dict[str, Tuple[str, int]]


def load_tokens(filepath: str) -> TokensMap:
    """Загружает и валидирует файл токенов."""
    tokens: TokensMap = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, raw_line in enumerate(f, 1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue

                token, host_str, port_str = [p.strip() for p in line.split(":")]
                ip_str = str(ipaddress.IPv4Address(host_str))
                port = int(port_str)

                if not token or token in tokens or not (1 <= port <= 65535):
                    raise ValueError("некорректный токен, дубликат или порт")

                tokens[token] = (ip_str, port)
    except Exception as e:
        line_info = f" (строка {line_num})" if "line_num" in locals() else ""
        logging.critical("Ошибка загрузки %s%s: %s", filepath, line_info, e)
        sys.exit(1)

    logging.info("Загружено токенов: %d", len(tokens))
    return tokens


def extract_token(websocket, path: Optional[str] = None) -> Optional[str]:
    """Извлекает токен из URI запроса."""
    if LEGACY_WS:
        current_path = websocket.path if "?" in websocket.path else (path or "")
    else:
        current_path = getattr(websocket.request, "uri", websocket.request.path or "")

    _, _, query_str = current_path.partition("?")
    params = parse_qs(query_str)
    token_list = params.get("token") or params.get("path")
    return token_list[0].strip() if token_list else None


async def ws_handler(websocket, path: Optional[str] = None, *, tokens_map: TokensMap):
    """Асинхронный воркер WebSocket-соединения."""
    token = extract_token(websocket, path)
    if not token:
        await websocket.close(1008, "Missing token")
        return

    vnc_target = tokens_map.get(token)
    if not vnc_target:
        logging.warning("[%s] Попытка подключения с незарегистрированным токеном", token)
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    logging.info("[%s] Подключение к VNC бэкенду %s:%s", token, host, port)

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5.0
        )
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        logging.info("[%s] Мост скоммутирован", token)
    except Exception as e:
        logging.error("[%s] Ошибка TCP подключения к VNC: %s", token, e)
        await websocket.close(1011, "VNC backend connection failed")
        return

    async def pipeline_browser_to_vm():
        async for message in websocket:
            if isinstance(message, bytes) and message:
                writer.write(message)
                await writer.drain()

    async def pipeline_vm_to_browser():
        while True:
            data = await reader.read(65536)
            if not data:
                break
            await asyncio.wait_for(websocket.send(data), timeout=10.0)

    tasks = [
        asyncio.create_task(pipeline_browser_to_vm()),
        asyncio.create_task(pipeline_vm_to_browser()),
    ]

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            if not task.cancelled() and task.exception():
                logging.debug("[%s] Поток завершен с ошибкой: %r", token, task.exception())
    except Exception as e:
        logging.debug("[%s] Исключение в конвейере данных: %s", token, e)
    finally:
        try:
            writer.close()
            if hasattr(writer, "wait_closed"):
                await writer.wait_closed()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
        logging.info("[%s] Сессия закрыта, ресурсы освобождены", token)


async def main(listen_host: str, listen_port: int, tokens_map: TokensMap):
    logging.info(
        "Запуск в режиме %s",
        "legacy (websockets < 13)" if LEGACY_WS else "modern (websockets >= 13)",
    )
    handler = partial(ws_handler, tokens_map=tokens_map)

    async with ws_serve(
        handler,
        listen_host,
        listen_port,
        subprotocols=["binary"],
        ping_interval=15,
        ping_timeout=10,
        max_size=1024 * 1024,
        reuse_address=True,
    ):
        logging.info("VNC-to-WebSocket Bridge запущен на %s:%s", listen_host, listen_port)
        await asyncio.Event().wait()


def valid_listen_address(netloc: str) -> Tuple[str, int]:
    try:
        host, port_str = netloc.rsplit(":", 1) if ":" in netloc else ("0.0.0.0", netloc)
        return host, int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' не является валидным сетевым адресом.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-bridge: WebSocket VNC proxy")
    parser.add_argument(
        "listen_target",
        nargs="?",
        type=valid_listen_address,
        default="0.0.0.0:8080",
        help="Адрес и порт прослушивания (по умолчанию 0.0.0.0:8080)",
    )
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    tokens_map = load_tokens(args.token_source)
    host, port = args.listen_target

    try:
        asyncio.run(main(host, port, tokens_map))
    except KeyboardInterrupt:
        logging.info("Остановка сервера по сигналу прерывания")
