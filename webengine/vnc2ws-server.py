#!/usr/bin/env python3
import os
import sys
import re
import socket
import logging
import argparse
import subprocess
import mimetypes
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# HTML-шаблон интерфейса панели управления
TEMPLATE_PATH = "index.html"

# Регулярное выражение для предотвращения атак типа Command Injection через имя ВМ
VM_NAME_SAFE_REGEXP = re.compile(r'^[a-zA-Z0-9_-]+$')

# Шаблон парсинга строк tokens.conf: Имя_ВМ:Локальный_IP:Порт_VNC
TOKEN_REGEXP = re.compile(r'^([a-zA-Z0-9_-]+):\s*([0-9.]+):(\d+)')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y/%m/%d %H:%M:%S')

# Глобальная переменная для пути к конфигурации токенов (инициализируется в __main__)
TOKENS_PATH = ""

def get_vnc_address(vm_name):
    """
    Ищет сетевой адрес VNC-бэкенда в файле tokens.conf по точному имени ВМ.

    Args:
        vm_name (str): Уникальное имя запрашиваемой виртуальной машины.

    Returns:
        tuple (str, int) или None: Кортеж (IP-адрес, Порт) при успехе, иначе None.
    """
    if not os.path.exists(TOKENS_PATH) or not os.access(TOKENS_PATH, os.R_OK):
        logging.error(f"Файл конфигурации токенов недоступен для чтения: {TOKENS_PATH}")
        return None
    with open(TOKENS_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            match = TOKEN_REGEXP.match(line)
            if match and match.group(1) == vm_name:
                return match.group(2), int(match.group(3))
    return None

def is_vm_running(vnc_addr_tuple):
    """
    Опрашивает статус ВМ методом проверки доступности её локального VNC-порта.

    ПРИМЕЧАНИЕ: Использование минимального таймаута (0.5 сек) гарантирует, что
    опрос зависшего сокета не заблокирует ресурсы рабочего HTTP-потока.

    Args:
        vnc_addr_tuple (tuple): Кортеж (IP-адрес, Порт) целевой ВМ.

    Returns:
        bool: True, если порт открыт и отвечает (RUNNING), иначе False (STOPPED).
    """
    if not vnc_addr_tuple: return False
    try:
        host, port = vnc_addr_tuple
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except Exception:
        return False

class VLabManagementHandler(BaseHTTPRequestHandler):
    """Обработчик входящих HTTP REST API и статических UI запросов веб-панели."""

    def log_message(self, format, *args):
        """Подавление стандартных отладочных логов BaseHTTPRequestHandler в stdout."""
        logging.debug(format % args)

    def send_simple_response(self, code, body, content_type):
        """
        Унифицированный хелпер для формирования и отправки стандартных ответов HTTP.

        ПРИМЕЧАНИЕ: Заголовок CORS добавлен для предотвращения блокировок браузера
        при AJAX-опросе статуса ВМ с фронтенда.
        """
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        """Основной линейный диспетчер маршрутизации входящих HTTP-запросов (GET)."""
        # ОПТИМИЗАЦИЯ: Сплит строки по знаку '?' работает быстрее urlparse на уровне ядра Python
        if '?' in self.path:
            path, query_str = self.path.split('?', 1)
        else:
            path = self.path
            query_str = ""

        # БЕЗОПАСНОЕ ИЗВЛЕЧЕНИЕ: Использование фолбэка [""] защищает индекс [0] от IndexError.
        # Текстовый маркер [0] прописан явно, метод .strip() применяется строго к строке.
        params = parse_qs(query_str)
        vm_name = params.get("vm", [""])[0].strip()
        webroot = self.server.webroot

        # БЕЗОПАСНОСТЬ: Исключаем спецсимволы, защищая систему от инъекций в bash утилиты vmctrl
        if vm_name and not VM_NAME_SAFE_REGEXP.match(vm_name):
            self.send_error(400, "Invalid VM name format")
            return

        # Маршрут API: Асинхронный запуск виртуальной машины
        if path == "/start" and vm_name:
            logging.info(f"API: Запуск виртуальной машины '{vm_name}'")
            # ПРИМЕЧАНИЕ: subprocess.Popen выполняется в фоне. Мы не вызываем .wait(),
            # чтобы долгий старт операционной системы внутри ВМ не подвешивал веб-сервер.
            subprocess.Popen(["/usr/local/bin/vmctrl", "start", vm_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.send_simple_response(200, b"OK", "text/plain")
            return

        # Маршрут API: Запрос текущего состояния ВМ (RUNNING/STOPPED)
        if path == "/status":
            vnc_addr = get_vnc_address(vm_name) if vm_name else None
            status = "RUNNING" if is_vm_running(vnc_addr) else "STOPPED"
            self.send_simple_response(200, status.encode(), "text/plain")
            return

        # Маршрут UI: Динамическая раздача цветных иконок из системного пути
        if path == "/favicon.ico":
            icons_dir = "/usr/share/pixmaps"
            icon_path = os.path.join(icons_dir, f"{vm_name}.png") if vm_name else ""
            if not icon_path or not os.path.exists(icon_path):
                icon_path = os.path.join(icons_dir, "default.png")
            
            if not os.path.exists(icon_path):
                self.send_simple_response(204, b"", "image/png")
                return
            with open(icon_path, 'rb') as f:
                self.send_simple_response(200, f.read(), "image/png")
            return

        # Маршрут UI: Раздача статических ресурсов noVNC из системы
        if path.startswith("/novnc/"):
            relative_path = path.replace("/novnc/", "", 1)
            file_path = os.path.normpath(os.path.join(webroot, relative_path))
            
            # БЕЗОПАСНОСТЬ: Проверка Path Traversal исключает чтение системных файлов через '../../'
            if not file_path.startswith(os.path.normpath(webroot)) or not os.path.exists(file_path) or os.path.isdir(file_path):
                self.send_error(404, "File not found")
                return
                
            # КРИТИЧЕСКИЙ НЮАНС: mimetypes.guess_type динамически определяет расширения.
            # Это гарантирует отдачу заголовка 'application/javascript' для rfb.js,
            # защищая noVNC от блокировки строгими правилами безопасности Firefox.
            content_type, _ = mimetypes.guess_type(file_path)
            with open(file_path, 'rb') as f:
                self.send_simple_response(200, f.read(), content_type or "text/plain")
            return

        # Маршрут UI: Рендеринг главной страницы консоли
        if path == "/":
            if not vm_name or not get_vnc_address(vm_name):
                self.send_error(404, f"VM '{vm_name}' not registered")
                return
            if not os.path.exists(TEMPLATE_PATH):
                self.send_error(500, "Internal Server Error: UI Template missing")
                return
            with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
                # Динамическая замена Go-плейсхолдеров шаблона на переменные Python
                html = f.read().replace("{{.VMName}}", vm_name).replace("{{.ProxyPort}}", str(self.server.proxy_port))
            self.send_simple_response(200, html.encode('utf-8'), "text/html; charset=utf-8")
            return

        self.send_error(404, "Not Found")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-server: Web Console & Management API")
    parser.add_argument("listen", nargs="?", default="0.0.0.0:8081")
    parser.add_argument("--proxy-port", type=int, default=8080)
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf")
    parser.add_argument("--webroot", default="/usr/share/novnc/")
    args = parser.parse_args()

    TOKENS_PATH = args.token_source
    host, port_str = args.listen.rsplit(':', 1) if ":" in args.listen else ("0.0.0.0", args.listen)
    addr = (host, int(port_str))

    # Запуск сервера. ThreadingHTTPServer автоматически выделяет поток ОС на каждый запрос,
    # что изолирует обработку HTTP от основного GIL асинхронного WebSocket шлюза.
    server = ThreadingHTTPServer(addr, VLabManagementHandler)
    server.proxy_port = args.proxy_port
    server.webroot = os.path.abspath(args.webroot)
    logging.info(f"UI Server started on http://{host}:{port_str}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
