#!/usr/bin/env python3
import argparse
import asyncio
import logging
import re
import socket
from urllib.parse import parse_qs

try:
    from websockets.asyncio.server import serve as ws_serve
    LEGACY_WS = False
except ModuleNotFoundError:
    from websockets import serve as ws_serve
    LEGACY_WS = True

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN_REGEXP = re.compile(r"^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)")
TOKENS_SOURCE = "/srv/qemu/tokens.conf"


def get_target_addr(token):
    """Ищет IP и порт целевого VNC-сервера в файле токенов по переданному токену."""
    try:
        with open(TOKENS_SOURCE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                match = TOKEN_REGEXP.match(line)
                if match and match.group(1) == token:
                    return match.group(2), int(match.group(3))
    except Exception as e:
        logging.error("Ошибка чтения файла токенов: %s", e)
    return None


async def ws_handler(websocket, path=None):
    """Асинхронный воркер WebSocket соединения."""
    if LEGACY_WS:
        current_path = websocket.path if "?" in websocket.path else (path or "")
    else:
        current_path = getattr(websocket.request, "uri", websocket.request.path or "")

    _, _, query_str = current_path.partition("?")
    params = parse_qs(query_str)
    token_list = params.get("token") or params.get("path")
    token = token_list.pop(0).strip() if token_list else None

    if not token:
        headers = websocket.request_headers if LEGACY_WS else websocket.request.headers
        forwarded_uri = headers.get("X-Forwarded-Uri", "")
        _, _, fwd_query = forwarded_uri.partition("?")
        fwd_list = parse_qs(fwd_query).get("token")
        token = fwd_list.pop(0).strip() if fwd_list else None

        if not token:
            await websocket.close(1008, "Missing token")
            return

    vnc_target = get_target_addr(token)
    if not vnc_target:
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    logging.info("[%s] Попытка подключения к VNC бэкенду %s:%s", token, host, port)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=5.0)
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        logging.info("[%s] Сетевой мост успешно скоммутирован", token)
    except Exception as e:
        logging.error("[%s] Ошибка TCP подключения к VNC: %s", token, e)
        return

    is_closed = False

    async def close_all():
        nonlocal is_closed
        if not is_closed:
            is_closed = True
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
            logging.info("[%s] Транспортные каналы сессии закрыты", token)

    async def keep_alive_pinger():
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed:
                    res = websocket.ping()
                    if asyncio.isfuture(res) or asyncio.iscoroutine(res):
                        await asyncio.wait_for(res, timeout=10.0)
        except Exception:
            await close_all()

    async def pipeline_browser_to_vm():
        try:
            async for message in websocket:
                if isinstance(message, bytes) and len(message) > 0 and not is_closed:
                    writer.write(message)
                    await writer.drain()
        except Exception:
            pass
        finally:
            await close_all()

    async def pipeline_vm_to_browser():
        try:
            while not is_closed:
                data = await reader.read(65536)
                if not data:
                    break
                await asyncio.wait_for(websocket.send(data), timeout=10.0)
        except Exception:
            pass
        finally:
            await close_all()

    await asyncio.gather(
        keep_alive_pinger(),
        pipeline_browser_to_vm(),
        pipeline_vm_to_browser(),
        return_exceptions=True,
    )


def modify_handshake_headers(*args):
    """Нормализует HTTP-заголовки Handshake-запроса при работе за прокси."""
    try:
        request_headers = args if LEGACY_WS else args.headers
        conn_header = request_headers.get("Connection", "")
        if "keep-alive" in conn_header.lower():
            request_headers["Connection"] = "Upgrade"
            if "Upgrade" not in request_headers:
                request_headers["Upgrade"] = "websocket"
    except Exception:
        pass
    return None


async def main(listen_host, listen_port):
    logging.info(
        "Engine launched in %s mode",
        "legacy (websockets v10.4)" if LEGACY_WS else "modern (websockets v13-15+)",
    )
    async with ws_serve(
        ws_handler,
        listen_host,
        listen_port,
        subprotocols=["binary"],
        process_request=modify_handshake_headers,
    ):
        logging.info("VNC-to-WebSocket Bridge Engine active on port %s", listen_port)
        if LEGACY_WS:
            await asyncio.Event().wait()
        else:
            await asyncio.Future()


def valid_listen_address(netloc):
    try:
        host, port_str = netloc.rsplit(":", 1) if ":" in netloc else ("0.0.0.0", netloc)
        return host, int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' не является валидным сетевым адресом.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-bridge: WebSocket VNC proxy")
    parser.add_argument("listen_target", nargs="?", type=valid_listen_address, default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    args = parser.parse_args()

    TOKENS_SOURCE = args.token_source
    host, port = args.listen_target

    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        pass
