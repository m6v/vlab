#!/usr/bin/env python3
import http.server
import json
import libvirt
import os
import subprocess
import sys
import time
import socket
import argparse

# Глобальные переменные для передачи портов в API-обработчик
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
    """Парсинг json-файла конфигурации, проверка статуса ВМ и генерация файла с токенами"""
    tokens_tree = {}
    flat_lines = []

    if not os.path.exists(JSON_TOKENS_FILE):
        print(f"Ошибка: Файл конфигурации {JSON_TOKENS_FILE} не найден!", file=sys.stderr)
        return tokens_tree

    try:
        with open(JSON_TOKENS_FILE, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        for vm_name, vm_info in config_data.items():
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
        print(f"Ошибка синтаксиса в файле конфигурации: {je}", file=sys.stderr)
    except Exception as e:
        print(f"Ошибка при обработке файла конфигурации: {e}", file=sys.stderr)

    return tokens_tree

def libvirt_control(host_ip, vm_name, action):
    """Прямое управление ВМ через API libvirt (локально или удаленно по SSH)"""
    import libvirt # Импорт внутри для надежности, если глобальный забыли
    
    if host_ip in ["localhost", "127.0.0.1", ""]:
        uri = "qemu:///system"
    else:
        uri = f"qemu+ssh://root@{host_ip}/system?no_verify=1"

    conn = None
    try:
        conn = libvirt.open(uri)
        if conn is None:
            return {"status": "error", "message": f"Не удалось подключиться к гипервизору {uri}"}
        
        dom = conn.lookupByName(vm_name)
        
        if action == "start":
            dom.create()
        elif action == "shutdown":
            dom.shutdown()
        elif action == "destroy":
            dom.destroy()
        else:
            return {"status": "error", "message": f"Неизвестное действие: {action}"}
            
        return {"status": "success"}

    except libvirt.libvirtError as e:
        return {"status": "error", "message": f"Ошибка Libvirt: {e.get_error_message()}"}
    except Exception as e:
        return {"status": "error", "message": f"Системная ошибка: {str(e)}"}
    finally:
        if conn:
            conn.close()

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


    def do_POST(self):
        """Обработка команд управления питанием ВМ"""
        if self.path == '/api/vm/control':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                req = json.loads(post_data.decode('utf-8'))
                host_key = req.get('host')  # Например: "арм-1"
                vm_name = req.get('vm')    # Например: "arm-abi"
                action = req.get('action')  # 'start', 'shutdown', 'destroy'
                
                if not host_key or not vm_name or not action:
                    raise ValueError("Отсутствуют параметры 'host', 'vm' или 'action'")

                host_ip = "localhost"
                
                # Ищем IP-адрес нужного физического АРМа в конфигурации
                if os.path.exists(JSON_TOKENS_FILE):
                    with open(JSON_TOKENS_FILE, 'r', encoding='utf-8') as f:
                        config_data = json.load(f)
                        if host_key in config_data:
                            host_ip = config_data[host_key].get("ip", "localhost").strip()

                # Вызываем управление через libvirt-python
                # Подключение пойдет на host_ip (например, 192.168.1.128) к машине vm_name (например, arm-abi)
                result = libvirt_control(host_ip, vm_name, action)
                
                if result["status"] == "success":
                    self.send_response(200)
                else:
                    self.send_response(500)
                    
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))

            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()
        self.end_headers()


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
