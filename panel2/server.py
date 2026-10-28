#!/usr/bin/env python3
import http.server
import json
import os
import subprocess
import sys
import time
import socket
import argparse

# Глобальные переменные для передачи актуальных портов запуска в API-обработчик
CURRENT_PYTHON_PORT = 8000
CURRENT_WEBSOCKIFY_PORT = 8085

# Определение путей относительно расположения server.py
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEBSOCKIFY_BIN = os.path.join(BASE_DIR, 'websockify')
NOVNC_DIR = os.path.join(BASE_DIR, 'noVNC-1.6.0')
JSON_TOKENS_FILE = os.path.join(BASE_DIR, 'tokens.json') 

FLAT_TOKENS_FILE = '/tmp/tokens.txt'

def check_port(ip, port):
    """Проверка открытия TCP-порта VNC на удаленной ЭВМ"""
    try:
        # Использование AF_INET напрямую оптимизирует создание сокета
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.1)
            # connect_ex возвращает 0 при успешном подключении
            result = sock.connect_ex((ip, int(port)))
            return result == 0
    except Exception:
        return False

def rebuild_flat_tokens():
    """Парсинг конфигурации с перехватом синтаксических ошибок для отправки в UI"""
    tokens_tree = {}
    flat_lines = []

    if not os.path.exists(JSON_TOKENS_FILE):
        return {"_error": f"Файл конфигурации {JSON_TOKENS_FILE} не найден!"}

    try:
        with open(JSON_TOKENS_FILE, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        for vm_name, vm_info in config_data.items():
            if not isinstance(vm_info, dict):
                continue

            ip_address = vm_info.get("ip", "").strip()
            tokens_dict = vm_info.get("tokens", {})

            if not ip_address:
                continue

            tokens_tree[vm_name] = {
                "ip": ip_address,
                "vms": []
            }

            for key, port in tokens_dict.items():
                token_name = f"{vm_name}-{key.strip()}"
                is_online = check_port(ip_address, port)

                tokens_tree[vm_name]["vms"].append({
                    "token": token_name,
                    "status": "online" if is_online else "offline"
                })

                flat_lines.append(f"{token_name}: {ip_address}:{port}\n")

        with open(FLAT_TOKENS_FILE, 'w', encoding='utf-8') as f:
            f.writelines(flat_lines)

    except json.JSONDecodeError as je:
        # Формируем понятную ошибку с указанием строки и позиции
        error_msg = f"Ошибка синтаксиса JSON (строка {je.lineno}, символ {je.colno}): {je.msg}"
        print(f"[ОШИБКА КОНФИГУРАЦИИ] {error_msg}", file=sys.stderr)
        return {"_error": error_msg}
    except Exception as e:
        return {"_error": f"Критическая ошибка бэкенда: {str(e)}"}

    return tokens_tree

class NoVNCHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Настройка пути к статике из корня проекта, где лежит index.html
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def do_GET(self):
        # Явное указание использования глобальных переменных для предотвращения Scope-ошибок
        global CURRENT_PYTHON_PORT, CURRENT_WEBSOCKIFY_PORT

        # Перехват запросов к API, обработка остальных запросов в SimpleHTTPRequestHandler
        if self.path == '/api/tokens':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()

            tokens_data = rebuild_flat_tokens()
            
            # Упаковываем динамические порты и дерево токенов в единый JSON-ответ
            api_response = {
                "config": {
                    "pythonPort": CURRENT_PYTHON_PORT,
                    "websockifyPort": CURRENT_WEBSOCKIFY_PORT
                },
                "tree": tokens_data
            }
            
            self.wfile.write(json.dumps(api_response).encode('utf-8'))
            return

        super().do_GET()

def start_websockify(websockify_port):
    """Запуск go-websockify в режиме чтения файла токенов с динамическим портом"""
    cmd = [
        WEBSOCKIFY_BIN,
        '-l', f'0.0.0.0:{websockify_port}',
        '-f', FLAT_TOKENS_FILE
    ]

    print(f"Запуск go-websockify на порту {websockify_port}...")
    try:
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        return process
    except FileNotFoundError:
        print(f"Ошибка: Файл {WEBSOCKIFY_BIN} not found!", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    # Настраиваем парсер аргументов командной строки
    parser = argparse.ArgumentParser(description="Бэкенд Панели инструктора УТК-СЗИ")
    
    parser.add_argument('-p', '--port', type=int, default=8000, 
                        help="Порт веб-сервера Панели инструктора (по умолчанию 8000)")
    parser.add_argument('-w', '--wport', type=int, default=8085, 
                        help="Порт прокси-сервера websockify (по умолчанию 8085)")
    
    args = parser.parse_args()

    # Сохраняем переданные при запуске порты в глобальные переменные для API
    CURRENT_PYTHON_PORT = args.port
    CURRENT_WEBSOCKIFY_PORT = args.wport

    # Проверка наличия каталога с novnc
    if not os.path.exists(NOVNC_DIR):
        print(f"Ошибка: каталог с novnc {NOVNC_DIR} не найден!", file=sys.stderr)
        sys.exit(1)

    rebuild_flat_tokens()
    # Передаем распарсенный порт websockify в функцию запуска
    websock_proc = start_websockify(args.wport)
    time.sleep(1)

    print(f"Панель инструктора запущена на http://localhost:{args.port}")

    try:
        # Использование многопоточного сервера для обработки параллельных запросов
        server = http.server.ThreadingHTTPServer(('0.0.0.0', args.port), NoVNCHandler)
        server.serve_forever()
    except BaseException:
        print("\nОстановка серверов...")
    finally:
        if 'websock_proc' in locals() and websock_proc:
            websock_proc.terminate()
            websock_proc.wait()
        if os.path.exists(FLAT_TOKENS_FILE):
            os.remove(FLAT_TOKENS_FILE)
        print("Процессы успешно завершены.")
