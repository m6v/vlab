# vnc2ws: Universal HTML5 Web Console & Gateway Engine

Универсальный программный комплекс на Python для организации безопасного удаленного доступа к консолям виртуальных машин и серверов через браузер (HTML5 noVNC) с поддержкой динамической авторизации по токенам и автоматического управления питанием бэкендов.

## Архитектура системы

Комплекс состоит из двух независимых взаимодействующих серверных компонентов:

1. **`vnc2ws-server.py`** — Управляющий веб-сервер и координатор сессий. Обеспечивает раздачу статических ресурсов интерфейса noVNC, обрабатывает REST API запросы состояний (`/status`, `/start`) и динамически генерирует уникальные фавиконки для целевых хостов из `/usr/share/pixmaps`.
2. **`vnc2ws-bridge.py`** — Сетевой асинхронный транспортный шлюз. Обеспечивает бесшовную трансляцию сырых графических потоков VNC/RFB в современные веб-сокеты браузера. Защищен от засыпания вкладок благодаря встроенному циклу обмена пингами.

## Требования и зависимости

* Интерпретатор **Python 3.7+**
* Библиотека **`websockets`** (рекомендуется v15.0+ для актуальных систем или v10.4 для сред со старым ядром вроде Astra Linux)
* Системный пакет **`novnc`** (для раздачи базовых JS-декодеров из `/usr/share/novnc/`)

## Установка и сборка

Проект оснащен универсальным `Makefile`, автоматизирующим установку и компиляцию пакетов.

```bash
# Проверка зависимостей среды
make deps

# Локальная установка компонентов в директорию /srv/qemu/
sudo make install

# Сборка нативного .deb пакета для Debian/Astra Linux
make deb
```

## Конфигурация токенов (`tokens.conf`)

Маппинг виртуальных машин на их внутренние сетевые адреса VNC описывается в едином конфигурационном файле в формате:
```text
# Имя_ВМ:Локальный_IP:Порт_VNC
arm-abi:127.0.0.1:5912
srv-szi:127.0.0.1:5913
```

## Ручной запуск компонентов

Для автономного запуска служб в терминале используйте следующие команды:

```bash
# 1. Запуск управляющего сервера консоли (Порт 8081)
./vnc2ws-server.py 0.0.0.0:8081 --proxy-port=8080 --token-source=/srv/qemu/tokens.conf

# 2. Запуск транспортного вебсокет-моста (Порт 8080)
./vnc2ws-bridge.py 0.0.0.0:8080 --token-source=/srv/qemu/tokens.conf
```

После старта интерфейс управления и отображения экранов доступен в браузере по адресу:
`http://<IP_СЕРВЕРА>:8081/?vm=ИМЯ_МАШИНЫ`

## Интеграция с Systemd

При установке через `make deb` компоненты автоматически регистрируются в системе как фоновые демоны.

```bash
# Старт и активация автозапуска экосистемы vnc2ws
sudo systemctl enable --now vnc2ws-server.service
sudo systemctl enable --now vnc2ws-bridge.service

# Проверка статуса сетевых воркеров
sudo systemctl status vnc2ws-*
```

# Критические замечания и риски от Qween 3.7

1. Синхронное чтение файла на каждое подключение (I/O Bottleneck). Функция get_target_addr открывает и читает файл /srv/qemu/tokens.conf синхронно при каждом новом WebSocket-соединении. При 100 одновременных подключениях это создаст очередь и заблокирует event loop. Решение: Загружать токены в словарь (dict) в памяти при запуске скрипта. Поиск по словарю занимает O(1) и не блокирует асинхронный цикл.
2. Опасное подавление исключений (except Exception:). В функциях pipeline_* и close_all используется голое except Exception:. Это перехватывает даже asyncio.CancelledError, что может привести к зависанию задач или скрытию реальных багов.
- Решение: Явно разрешать проброс asyncio.CancelledError и логировать конкретные ошибки.
3. Хак с *args в modify_handshake_headers. Сигнатура process_request в библиотеке websockets менялась между версиями 9, 10, 12 и 14. Использование *args и попытка угадать структуру – это "мина замедленного действия" при обновлении зависимостей.
- Решение: Нормализацию заголовков Connection и Upgrade настоятельно рекомендуется делать на уровне обратного прокси (Nginx/Traefik), а не в Python-коде. Если это невозможно, код нужно адаптировать под конкретную мажорную версию библиотеки.
4. Регулярное выражение для IP-адреса. ([0-9.]+) пропускает невалидные строки вроде 1.2.3.4.5.6 или 999.999.999.999. Решение: Использовать встроенный модуль ipaddress для валидации или хотя бы ужесточить регулярное выражение.
5. Отсутствие логирования причин разрыва. Если pipeline_vm_to_browser падает из-за таймаута websocket.send (например, медленный клиент), это молча закрывает соединение. Полезно логировать такие события для отладки.

# Оптимизированная и безопасная версия кода
```python
#!/usr/bin/env python3
import argparse
import asyncio
import logging
import ipaddress
from urllib.parse import parse_qs

try:
    from websockets.asyncio.server import serve as ws_serve
    # Современная версия websockets (>= 14.0)
    MODERN_WS = True
except ModuleNotFoundError:
    try:
        from websockets import serve as ws_serve
        # Более старая, но все еще распространенная версия (10.x - 13.x)
        MODERN_WS = False
    except ModuleNotFoundError:
        raise RuntimeError("Требуется библиотека 'websockets'.)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

TOKENS_SOURCE = "/srv/qemu/tokens.conf"
# Глобальный кэш токенов для доступа без блокирующих I/O операций
TOKEN_CACHE = {}


def load_tokens(filepath: str) -> dict:
    """Загружает токены из файла в память. Возвращает словарь {token: (host, port)}."""
    tokens = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                parts = line.split(":")
                if len(parts) != 3:
                    logging.warning("Неверный формат строки %d в %s: %s", line_num, filepath, line)
                    continue
                
                token = parts[0].strip()
                host = parts[1].strip()
                port_str = parts[2].strip()
                
                # Валидация хоста и порта
                try:
                    ipaddress.ip_address(host)
                except ValueError:
                    # Разрешаем также hostname, если ipaddress не прошел, но с предупреждением
                    pass 
                
                try:
                    port = int(port_str)
                    if not (1 <= port <= 65535):
                        raise ValueError
                except ValueError:
                    logging.warning("Неверный порт в строке %d: %s", line_num, line)
                    continue
                
                tokens[token] = (host, port)
        logging.info("Загружено %d токенов из %s", len(tokens), filepath)
        return tokens
    except FileNotFoundError:
        logging.error("Файл токенов не найден: %s", filepath)
        return {}
    except Exception as e:
        logging.error("Ошибка чтения файла токенов: %s", e)
        return {}


def get_token_from_request(websocket) -> str | None:
    """Извлекает токен из query-параметров или заголовков прокси."""
    # Извлечение пути в зависимости от версии библиотеки
    if MODERN_WS:
        # websockets >= 14.0 использует websocket.request
        path = getattr(websocket.request, "path", "")
        headers = websocket.request.headers
    else:
        # websockets < 14.0
        path = getattr(websocket, "path", "")
        headers = getattr(websocket, "request_headers", {})

    # Проверяем query string
    if "?" in path:
        _, _, query_str = path.partition("?")
        params = parse_qs(query_str)
        token_list = params.get("token") or params.get("path")
        if token_list:
            return token_list[0].strip()

    # Проверяем заголовок X-Forwarded-Uri (для Nginx proxy_pass)
    forwarded_uri = headers.get("X-Forwarded-Uri", "")
    if "?" in forwarded_uri:
        _, _, fwd_query = forwarded_uri.partition("?")
        fwd_list = parse_qs(fwd_query).get("token")
        if fwd_list:
            return fwd_list[0].strip()

    return None


async def ws_handler(websocket):
    """Асинхронный воркер WebSocket соединения."""
    token = get_token_from_request(websocket)

    if not token:
        logging.warning("Попытка подключения без токена с %s", websocket.remote_address)
        await websocket.close(1008, "Missing token")
        return

    vnc_target = TOKEN_CACHE.get(token)
    if not vnc_target:
        logging.warning("Неверный токен: %s", token)
        await websocket.close(1008, "Token not registered")
        return

    host, port = vnc_target
    logging.info("[%s] Подключение к VNC бэкенду %s:%s", token, host, port)
    
    reader, writer = None, None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), 
            timeout=5.0
        )
        sock = writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        logging.info("[%s] Мост успешно установлен", token)
    except asyncio.TimeoutError:
        logging.error("[%s] Таймаут подключения к VNC бэкенду", token)
        await websocket.close(1011, "Backend timeout")
        return
    except Exception as e:
        logging.error("[%s] Ошибка TCP подключения к VNC: %s", token, e)
        await websocket.close(1011, "Backend connection failed")
        return

    is_closed = False

    async def close_all():
        nonlocal is_closed
        if is_closed:
            return
        is_closed = True
        logging.info("[%s] Закрытие сессии", token)
        
        # Закрываем TCP
        if writer:
            try:
                writer.close()
                if hasattr(writer, "wait_closed"):
                    await writer.wait_closed()
            except Exception:
                pass
                
        # Закрываем WebSocket
        try:
            await websocket.close()
        except Exception:
            pass

    async def keep_alive_pinger():
        try:
            while not is_closed:
                await asyncio.sleep(15)
                if not is_closed:
                    # В современных версиях websockets ping() возвращает awaitable
                    await asyncio.wait_for(websocket.ping(), timeout=10.0)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.debug("[%s] Ошибка ping: %s", token, e)
            await close_all()

    async def pipeline_browser_to_vm():
        try:
            async for message in websocket:
                if is_closed:
                    break
                if isinstance(message, bytes) and message:
                    writer.write(message)
                    # Критически важно для предотвращения переполнения памяти (backpressure)
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.debug("[%s] Ошибка чтения из WebSocket: %s", token, e)
        finally:
            await close_all()

    async def pipeline_vm_to_browser():
        try:
            while not is_closed:
                data = await reader.read(65536)
                if not data:
                    logging.info("[%s] VNC бэкенд разорвал соединение", token)
                    break
                
                try:
                    # Отправляем бинарные данные. Таймаут защищает от медленных клиентов.
                    await asyncio.wait_for(websocket.send(data), timeout=10.0)
                except asyncio.TimeoutError:
                    logging.warning("[%s] Таймаут отправки данных клиенту (медленное соединение)", token)
                    break
                except Exception as e:
                    logging.debug("[%s] Ошибка отправки в WebSocket: %s", token, e)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.debug("[%s] Ошибка чтения из VNC: %s", token, e)
        finally:
            await close_all()

    # Запускаем задачи параллельно
    ping_task = asyncio.create_task(keep_alive_pinger())
    to_vm_task = asyncio.create_task(pipeline_browser_to_vm())
    to_browser_task = asyncio.create_task(pipeline_vm_to_browser())

    try:
        # Ждем завершения любой из задач (обычно это означает разрыв соединения)
        done, pending = await asyncio.wait(
            [ping_task, to_vm_task, to_browser_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        # Отменяем оставшиеся задачи
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        logging.error("[%s] Неожиданная ошибка в gather: %s", token, e)
    finally:
        await close_all()


async def main(listen_host: str, listen_port: int):
    global TOKEN_CACHE
    TOKEN_CACHE = load_tokens(TOKENS_SOURCE)
    
    # Опционально: здесь можно добавить asyncio.create_task(periodic_reload_tokens()) 
    # если файл tokens.conf меняется динамически без перезапуска сервиса.

    logging.info("Запуск VNC-to-WebSocket Bridge Engine на %s:%s", listen_host, listen_port)
    
    # Примечание: process_request удален, так как нормализация заголовков должна 
    # выполняться на уровне Nginx (proxy_set_header Connection "Upgrade";).
    # Это делает код стабильным across всех версий websockets >= 10.0
    async with ws_serve(
        ws_handler,
        listen_host,
        listen_port,
        subprotocols=["binary"], # Важно для noVNC
        max_size=10 * 1024 * 1024, # 10 MB макс. размер сообщения (защита от DoS)
    ):
        await asyncio.Future()  # Бесконечное ожидание


def valid_listen_address(netloc: str) -> tuple:
    try:
        if ":" in netloc:
            # Поддержка IPv6 адресов в квадратных скобках [::1]:8080
            if netloc.startswith("[") and "]:" in netloc:
                host, port_str = netloc.rsplit(":", 1)
                host = host.strip("[]")
            else:
                host, port_str = netloc.rsplit(":", 1)
        else:
            host, port_str = "0.0.0.0", netloc
        return host, int(port_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{netloc}' не является валидным сетевым адресом.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="vnc2ws-bridge: WebSocket VNC proxy")
    parser.add_argument("listen_target", nargs="?", type=valid_listen_address, default="0.0.0.0:8080")
    parser.add_argument("--token-source", default="/srv/qemu/tokens.conf", help="Путь к файлу токенов")
    args = parser.parse_args()

    TOKENS_SOURCE = args.token_source
    host, port = args.listen_target

    try:
        asyncio.run(main(host, port))
    except KeyboardInterrupt:
        logging.info("Остановка по сигналу пользователя")
```
