"""本机弹幕 Hub：复刻 Windows 上 `WssBarrageServer.exe` 的 WebSocket 协议。

为什么需要它
------------
`utils/platforms/dy.py` 连的是 `ws://127.0.0.1:8888`，收到的每一帧是：

    {"Type": 1, "Data": "{\"User\": {\"Nickname\": \"...\"}, \"Content\": \"...\"}"}

macOS 上没有那个 .NET 程序。这里用标准库（`socketserver` + 手写 WS 帧）
实现同一个服务端，于是 `dy.py` **完全不用改**。

同时提供两条注入通道，方便 Mock 与外部采集器：

    POST http://127.0.0.1:8888/inject      统一事件结构（推荐）
    POST http://127.0.0.1:8888/inject_raw  原协议帧 {"Type": n, "Data": "..."}
    GET  http://127.0.0.1:8888/health
    GET  http://127.0.0.1:8888/clients     当前连着的消费者

只绑 127.0.0.1。不主动连抖音，不做任何平台侧的鉴权绕过。
"""
import argparse
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from integrations.douyin.adapter import events as ev  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 已连接的消费者（`dy.py` 就是其中一个）
CLIENTS = []
CLIENTS_LOCK = threading.Lock()

# 去重：同一条 event_id 在窗口期内只推一次
SEEN = {}
SEEN_LOCK = threading.Lock()
SEEN_TTL = 30.0
SEEN_MAX = 4096

STATS = {"received": 0, "pushed": 0, "dropped": 0, "lastAt": 0.0}


def emit(event):
    try:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    except Exception:
        pass


def dedupe(event_id):
    """返回 True 表示这条事件已经见过（应该丢弃）。"""
    now = time.time()
    with SEEN_LOCK:
        previous = SEEN.get(event_id)
        if previous is not None and now - previous < SEEN_TTL:
            return True
        SEEN[event_id] = now
        if len(SEEN) > SEEN_MAX:
            cutoff = now - SEEN_TTL
            for key in [k for k, v in SEEN.items() if v < cutoff]:
                SEEN.pop(key, None)
    return False


# ------------------------------------------------------------ WebSocket 帧


def ws_encode(payload, opcode=0x1):
    """服务端 -> 客户端：不加掩码。"""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    header = bytearray([0x80 | opcode])
    length = len(data)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + data


def ws_decode_head(sock):
    """读一个客户端帧头。返回 (opcode, payload_len, mask)。"""
    head = sock.recv(2)
    if len(head) < 2:
        return None, 0, b""
    first, second = head[0], head[1]
    opcode = first & 0x0F
    masked = second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exact(sock, 8))[0]
    mask = recv_exact(sock, 4) if masked else b""
    return opcode, length, mask


def recv_exact(sock, size):
    buffer = b""
    while len(buffer) < size:
        chunk = sock.recv(size - len(buffer))
        if not chunk:
            raise ConnectionError("socket closed")
        buffer += chunk
    return buffer


# ------------------------------------------------------------ 消费者


class Client:
    def __init__(self, sock, address):
        self.sock = sock
        self.address = address
        self.alive = True
        self.lock = threading.Lock()

    def send(self, frame):
        """发送一帧原协议 JSON。失败就标记为断开。"""
        if not self.alive:
            return False
        try:
            with self.lock:
                self.sock.sendall(ws_encode(json.dumps(frame, ensure_ascii=False)))
            return True
        except Exception:
            self.alive = False
            return False

    def close(self):
        self.alive = False
        try:
            self.sock.close()
        except Exception:
            pass


def broadcast(frame):
    """把一帧推给所有消费者。返回成功推送的数量。"""
    with CLIENTS_LOCK:
        targets = list(CLIENTS)
    sent = 0
    for client in targets:
        if client.send(frame):
            sent += 1
        else:
            with CLIENTS_LOCK:
                if client in CLIENTS:
                    CLIENTS.remove(client)
    return sent


def publish(event):
    """统一事件 -> 去重 -> 转原协议 -> 广播。"""
    event = ev.normalize(event)
    STATS["received"] += 1
    if dedupe(event["event_id"]):
        STATS["dropped"] += 1
        emit({"event": "DROPPED_DUPLICATE", "event_id": event["event_id"]})
        return 0

    frame = ev.to_wss_frame(event)
    sent = broadcast(frame)
    STATS["pushed"] += 1
    STATS["lastAt"] = time.time()
    emit({
        "event": "PUSH",
        "type": event["type"],
        "nickname": event["nickname"],
        "content": event["content"][:60],
        "clients": sent,
    })
    return sent


# ------------------------------------------------------------ HTTP + WS


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 1 << 20:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            with CLIENTS_LOCK:
                count = len(CLIENTS)
            self._json({"code": 200, "service": "douyin-barrage-hub", "clients": count, **STATS})
            return
        if path == "/clients":
            with CLIENTS_LOCK:
                rows = [{"address": f"{c.address[0]}:{c.address[1]}", "alive": c.alive} for c in CLIENTS]
            self._json({"code": 200, "clients": rows})
            return
        # 原 WssBarrageServer 是「连到根路径就是一个纯 WS 服务」，
        # utils/platforms/dy.py 连的是 ws://127.0.0.1:8888（没有 /ws 后缀）。
        # 所以这里按 Upgrade 头判断，而不是按路径——任何路径都能升级成 WS，
        # 既兼容原协议，也允许显式用 /ws。
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self._upgrade()
            return
        if path == "/ws":
            self._upgrade()
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/inject":
            try:
                event = ev.normalize(self._body())
            except ValueError as error:
                self._json({"code": -1, "message": str(error)}, status=400)
                return
            sent = publish(event)
            self._json({"code": 200, "clients": sent, "event_id": event["event_id"]})
            return
        if path == "/inject_raw":
            try:
                event = ev.from_wss_frame(self._body())
            except (ValueError, json.JSONDecodeError) as error:
                self._json({"code": -1, "message": str(error)}, status=400)
                return
            sent = publish(event)
            self._json({"code": 200, "clients": sent, "event_id": event["event_id"]})
            return
        self.send_error(404)

    def _upgrade(self):
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self.send_error(400, "websocket only")
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()

        sock = self.connection
        sock.settimeout(None)
        client = Client(sock, self.client_address)
        with CLIENTS_LOCK:
            CLIENTS.append(client)
        emit({"event": "CLIENT_CONNECTED", "address": f"{self.client_address[0]}:{self.client_address[1]}"})

        try:
            # 读循环：只处理 ping / close，业务数据是单向推的
            while client.alive:
                opcode, length, mask = ws_decode_head(sock)
                if opcode is None or opcode == 0x8:
                    break
                payload = recv_exact(sock, length) if length else b""
                if mask and payload:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 0x9:  # ping -> pong
                    with client.lock:
                        sock.sendall(ws_encode(payload, opcode=0xA))
        except Exception:
            pass
        finally:
            client.close()
            with CLIENTS_LOCK:
                if client in CLIENTS:
                    CLIENTS.remove(client)
            emit({"event": "CLIENT_DISCONNECTED", "address": f"{self.client_address[0]}:{self.client_address[1]}"})
        self.close_connection = True


def serve(port):
    # 绑哪个地址：默认 127.0.0.1（本机直装，管理面不外露）。
    # Docker 里用 0.0.0.0——容器内绑 127.0.0.1 时 Docker Desktop 的端口转发连进来是空的。
    bind = os.environ.get("BARRAGE_HUB_BIND", "127.0.0.1")
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    print(f"BARRAGE_HUB_READY http://{bind}:{port}", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="抖音弹幕本机 Hub（兼容 WssBarrageServer 协议）")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BARRAGE_HUB_PORT", "8888")))
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    serve(args.port)


if __name__ == "__main__":
    main()
