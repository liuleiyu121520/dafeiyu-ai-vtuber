"""兼容现有 /send_message 的本机字幕服务；仅绑定回环地址，不播放音频。

除字幕外还承担三类信号（都是只读轮询，前端按 seq 判断是否是新事件）：
- /thinking?on=1|0   角色是否处于「思考中」，超过 THINKING_TTL 秒自动失效，
                      避免核心异常退出后气泡一直挂着
- /effect?name=clap  一次性特效触发，seq 单调递增

以及 **浏览器音频桥**（Docker 部署时音频的唯一出口，见 utils/audio_handle/browser_bridge.py）：
- POST /audio/sync         主程序推送「播放/停止/暂停/淡出/音量」命令，取回各通道状态
- POST /audio/report       浏览器回报「真的在播 / 播完了 / 播放位置」
- GET  /audio/file/<id>    把音频字节交给浏览器（OBS 浏览器源）
- GET  /audio/state        只读快照，给 doctor.sh 和前端诊断用
- GET  /audio/ws           WebSocket 推送（可选；拿不到就用 /audio/sync 轮询）

设计约束：
  * 只用标准库（http.server + 手写 WS 握手），不引入新依赖
  * 只绑 127.0.0.1，不对外网暴露
  * 音频文件按「路径哈希」编 id，浏览器拿到的 URL 不含宿主机路径
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import base64
import hashlib
import json
import os
import re
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'out/songroom'
LOCK = threading.Lock()
STATE = {'id': 0, 'text': '', 'at': 0}

# 思考状态：核心开始跑 LLM 时置位，出结果或超时后失效
THINKING_TTL = 25.0
THINKING = {'on': False, 'at': 0.0}

# 特效：每次触发 seq+1，前端记住上次看过的 seq
EFFECT = {'name': '', 'seq': 0, 'at': 0.0}


# ============================================================ 音频桥状态

# RLock：apply_command 持锁期间会调用 register_audio_file，后者也要这把锁。
# 用普通 Lock 会把自己锁死（请求线程挂住，curl 看到空响应）。
AUDIO_LOCK = threading.RLock()

# 通道语义与 utils/audio_handle/browser_bridge.py 保持一致
CHANNEL_NAMES = {0: 'comment', 1: 'copywriting', 2: 'bgm', 3: 'song'}

# 每个通道的权威状态（由主程序的命令驱动，由浏览器回报校正）
AUDIO = {
    channel: {
        'state': 'idle',      # idle | starting | playing | paused | fading | ended | stopped | error
        'fileId': '',
        'path': '',
        'volume': 1.0,
        'kind': '',
        'seq': 0,             # 每次 play 自增，浏览器据此判断是不是新的一段
        'startedAt': 0.0,
        'error': '',
    }
    for channel in CHANNEL_NAMES
}

# 浏览器回报
CLIENT = {
    'connected': False,
    'lastReport': 0.0,
    'channel': None,
    'state': None,
    'positionMs': 0,
    'level': 0.0,        # 浏览器算出的实时振幅，给诊断用（口型不走这里）
}

# fileId -> 绝对路径。只保留最近的若干条，避免无限增长
FILES = {}
FILES_ORDER = []
FILES_MAX = 64

# 允许交给浏览器的扩展名（不限制的话就是任意文件读取）
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.aac', '.ogg', '.opus', '.flac', '.webm'}

MIME_BY_EXT = {
    '.wav': 'audio/wav',
    '.mp3': 'audio/mpeg',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
    '.ogg': 'audio/ogg',
    '.opus': 'audio/ogg',
    '.flac': 'audio/flac',
    '.webm': 'audio/webm',
}


def emit(event):
    """运行日志。日志写不进去（编码、文件被占用……）不能让请求处理线程崩掉。

    stdout 被重定向成 GBK 文件时，“♪”、emoji、“ʚ”这类字符会抛 UnicodeEncodeError；
    以前这条异常发生在状态已更新、响应还没发出去之间，客户端看到的是“连接被断开”。
    """
    try:
        print(json.dumps(event, ensure_ascii=False), flush=True)
    except Exception:
        pass


# ------------------------------------------------------------ 音频桥工具


def audio_file_id(path):
    """路径 -> 稳定的短 id。同一文件重复登记得到同一个 id。"""
    return hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:16]


def register_audio_file(path):
    """登记一个要交给浏览器播放的音频文件，返回 fileId。

    Raises:
        ValueError: 扩展名不在白名单里
        FileNotFoundError: 文件不存在
    """
    resolved = Path(path).resolve()
    suffix = resolved.suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError(f'不支持的音频扩展名：{suffix}')
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))

    file_id = audio_file_id(resolved)
    with AUDIO_LOCK:
        if file_id not in FILES:
            FILES[file_id] = str(resolved)
            FILES_ORDER.append(file_id)
            # 淘汰最老的登记（文件本身不删，播放队列那边自己管）
            while len(FILES_ORDER) > FILES_MAX:
                FILES.pop(FILES_ORDER.pop(0), None)
    return file_id


def resolve_audio_file(file_id):
    """fileId -> 绝对路径；不在登记表里返回 None（不按 id 猜路径）。"""
    with AUDIO_LOCK:
        return FILES.get(file_id)


def channel_view(channel):
    item = AUDIO[channel]
    return {
        'state': item['state'],
        'seq': item['seq'],
        'kind': item['kind'],
        'fileId': item['fileId'],
        'url': f'/audio/file/{item["fileId"]}' if item['fileId'] else '',
        'volume': round(float(item['volume']), 4),
        'startedAt': item['startedAt'],
        'error': item['error'],
    }


def audio_snapshot():
    with AUDIO_LOCK:
        return {
            'channels': {str(ch): channel_view(ch) for ch in CHANNEL_NAMES},
            'client': dict(CLIENT),
            'now': time.time(),
        }


def apply_command(command):
    """执行一条来自主程序的命令。返回是否改变了状态。"""
    op = command.get('op')
    try:
        channel = int(command.get('channel'))
    except (TypeError, ValueError):
        return False
    if channel not in AUDIO:
        return False

    if op == 'play':
        path = command.get('path') or ''
        try:
            # 登记要读文件系统，放在锁外做，别让磁盘 I/O 卡住状态查询
            file_id = register_audio_file(path)
        except (ValueError, FileNotFoundError) as error:
            with AUDIO_LOCK:
                item = AUDIO[channel]
                item.update(state='error', error=str(error)[:200], seq=item['seq'] + 1)
            emit({'event': 'AUDIO_PLAY_ERROR', 'channel': channel, 'error': str(error)[:200]})
            return True

    with AUDIO_LOCK:
        item = AUDIO[channel]

        if op == 'play':
            item.update(
                state='starting',
                fileId=file_id,
                path=str(Path(path).resolve()),
                volume=float(command.get('volume') or 1.0),
                kind=str(command.get('kind') or ''),
                seq=item['seq'] + 1,
                startedAt=time.time(),
                error='',
            )
            emit({'event': 'AUDIO_PLAY', 'channel': channel, 'fileId': file_id,
                  'kind': item['kind'], 'seq': item['seq']})
            return True

        if op in ('stop', 'fadeout'):
            item.update(state='fading' if op == 'fadeout' else 'stopped', error='')
            emit({'event': 'AUDIO_STOP', 'channel': channel, 'op': op})
            return True

        if op == 'pause':
            if item['state'] in ('playing', 'starting'):
                item['state'] = 'paused'
            return True

        if op == 'resume':
            if item['state'] == 'paused':
                item['state'] = 'playing'
            return True

    return False


def apply_report(report):
    """浏览器回报：校正通道状态。"""
    with AUDIO_LOCK:
        CLIENT.update(
            connected=True,
            lastReport=time.time(),
            channel=report.get('channel'),
            state=report.get('state'),
            positionMs=int(report.get('positionMs') or 0),
            level=float(report.get('level') or 0.0),
        )
        try:
            channel = int(report.get('channel'))
        except (TypeError, ValueError):
            channel = None

        if channel in AUDIO:
            item = AUDIO[channel]
            seq = report.get('seq')
            # 只接受「当前这一段」的回报，晚到的旧回报不能把新的一段标成播完
            if seq is None or int(seq) == item['seq']:
                state = report.get('state')
                if state in ('playing', 'paused', 'ended', 'error'):
                    if state == 'ended' and item['state'] in ('idle', 'stopped'):
                        pass  # 已经清过了，不重复改
                    else:
                        item['state'] = state
                        if state == 'error':
                            item['error'] = str(report.get('error') or '')[:200]
    return True


# ------------------------------------------------------------ WebSocket


def ws_handshake(handler):
    """最小 WebSocket 握手。失败返回 False。"""
    key = handler.headers.get('Sec-WebSocket-Key')
    if not key:
        return False
    accept = base64.b64encode(
        hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()
    ).decode()
    handler.send_response(101, 'Switching Protocols')
    handler.send_header('Upgrade', 'websocket')
    handler.send_header('Connection', 'Upgrade')
    handler.send_header('Sec-WebSocket-Accept', accept)
    handler.end_headers()
    return True


def ws_send(handler, payload):
    """发一个文本帧（服务端帧不加掩码）。"""
    data = payload.encode('utf-8')
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header += struct.pack('>H', length)
    else:
        header.append(127)
        header += struct.pack('>Q', length)
    handler.wfile.write(bytes(header) + data)
    handler.wfile.flush()


def ws_read_frame(handler):
    """读一个客户端帧，返回 (opcode, payload)。客户端帧一定带掩码。"""
    head = handler.rfile.read(2)
    if len(head) < 2:
        return None, b''
    first, second = head[0], head[1]
    opcode = first & 0x0F
    masked = second & 0x80
    length = second & 0x7F
    if length == 126:
        length = struct.unpack('>H', handler.rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack('>Q', handler.rfile.read(8))[0]
    mask = handler.rfile.read(4) if masked else b''
    payload = handler.rfile.read(length) if length else b''
    if masked and payload:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


# ------------------------------------------------------------ HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _origin_ok(self):
        origin = self.headers.get('Origin', '')
        if not origin:
            return True
        return urlparse(origin).hostname in ('127.0.0.1', 'localhost', None)

    def _json(self, data, status=200, origin=''):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self._cors_headers()
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 1 << 20:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return {}

    # ---------------- 音频字节 ----------------

    def _serve_audio(self, file_id):
        path = resolve_audio_file(file_id)
        if not path or not os.path.isfile(path):
            self.send_error(404, 'audio not registered')
            return
        try:
            size = os.path.getsize(path)
            with open(path, 'rb') as handle:
                body = handle.read()
        except OSError:
            self.send_error(500, 'read failed')
            return

        mime = MIME_BY_EXT.get(Path(path).suffix.lower(), 'application/octet-stream')
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Accept-Ranges', 'none')
        # fetch 音频字节也是跨域（:12345 -> :5500），没有这个头浏览器会拒绝
        self._cors_headers()
        # 音频文件 id 已经含内容无关的路径哈希；用 no-cache 保证换文件后立刻生效
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)
        emit({'event': 'AUDIO_SERVED', 'fileId': file_id, 'bytes': size})

    # ---------------- WebSocket ----------------

    def _serve_ws(self):
        if not self._origin_ok():
            self.send_error(403)
            return
        if not ws_handshake(self):
            self.send_error(400, 'websocket only')
            return
        # 首帧推送完整状态，前端不用等下一轮
        try:
            ws_send(self, json.dumps(audio_snapshot(), ensure_ascii=False))
            last_push = time.time()
            while True:
                opcode, payload = ws_read_frame(self)
                if opcode is None or opcode == 0x8:   # 断开
                    break
                if opcode == 0x9:                      # ping -> pong
                    self.wfile.write(bytes([0x8A, len(payload)]) + payload)
                    self.wfile.flush()
                    continue
                if opcode == 0x1 and payload:
                    try:
                        apply_report(json.loads(payload.decode('utf-8')))
                    except Exception:
                        pass
                # 状态变化就推，最少 4 Hz 心跳
                if time.time() - last_push > 0.25:
                    ws_send(self, json.dumps(audio_snapshot(), ensure_ascii=False))
                    last_push = time.time()
        except Exception:
            pass
        finally:
            with AUDIO_LOCK:
                CLIENT['connected'] = False

    # ---------------- 路由 ----------------

    def _cors_headers(self):
        """浏览器跨域访问舞台页（:12345）要调 :5500 的接口，必须给全 CORS 头。

        POST + Content-Type: application/json 会先发 OPTIONS 预检，
        不实现 do_OPTIONS 的话浏览器直接判定 CORS 失败——
        表现就是「页面能开、字幕能出（GET 同源不预检）、但音频一声不响」。
        """
        origin = self.headers.get('Origin', '')
        if origin and urlparse(origin).hostname in ('127.0.0.1', 'localhost'):
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Vary', 'Origin')

    def send_error(self, code, message=None, explain=None):
        """错误响应也要带 CORS 头。

        http.server 默认的 send_error 只发 HTML，不带 CORS。浏览器看到的是
        「CORS 拦截」而不是「404」，排查时会被误导到完全错误的方向——
        实际原因只是这个 fileId 已经不在登记表里了（比如容器重启过）。
        """
        try:
            short, long_message = self.responses[code]
        except KeyError:
            short, long_message = '???', '???'
        body = json.dumps({'code': code, 'message': message or short}, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self._cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def do_OPTIONS(self):
        """预检请求：只回 CORS 头，不返回内容。"""
        self.send_response(204)
        self._cors_headers()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_POST(self):
        if not self._origin_ok():
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        origin = self.headers.get('Origin', '')

        if parsed.path == '/audio/sync':
            payload = self._read_json()
            commands = payload.get('commands') or []
            volumes = payload.get('volumes') or {}
            for command in commands:
                apply_command(command)
            if volumes:
                with AUDIO_LOCK:
                    for key, value in volumes.items():
                        try:
                            channel = int(key)
                        except (TypeError, ValueError):
                            continue
                        if channel in AUDIO:
                            AUDIO[channel]['volume'] = max(0.0, min(1.0, float(value)))
            snapshot = audio_snapshot()
            self._json(snapshot, origin=origin)
            return

        if parsed.path == '/audio/report':
            apply_report(self._read_json())
            self._json({'code': 200}, origin=origin)
            return

        self.send_error(404)

    def do_GET(self):
        if not self._origin_ok():
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        now = time.time()
        origin = self.headers.get('Origin', '')

        if parsed.path == '/send_message':
            text = query.get('content', [''])[0][:1000]
            with LOCK:
                STATE.update(id=STATE['id'] + 1, text=text, at=now)
                value = dict(STATE)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / 'caption-last.json').write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            data = {'code': 200, 'message': '字幕已接收', 'id': value['id']}
            emit({'event': 'CAPTION_RECEIVED', **value})

        elif parsed.path == '/thinking':
            on = query.get('on', ['0'])[0] in ('1', 'true', 'True')
            with LOCK:
                THINKING['on'] = on
                THINKING['at'] = now
            data = {'code': 200, 'thinking': on}
            emit({'event': 'THINKING', 'on': on})

        elif parsed.path == '/effect':
            name = query.get('name', ['clap'])[0][:32] or 'clap'
            with LOCK:
                EFFECT.update(name=name, seq=EFFECT['seq'] + 1, at=now)
                value = dict(EFFECT)
            data = {'code': 200, **value}
            emit({'event': 'EFFECT', **value})

        elif parsed.path == '/state':
            with LOCK:
                data = dict(STATE)
                thinking_on = THINKING['on'] and (now - THINKING['at'] < THINKING_TTL)
                data['thinking'] = thinking_on
                data['thinking_age'] = round(now - THINKING['at'], 2) if THINKING['at'] else None
                data.update(effect=EFFECT['name'], effect_seq=EFFECT['seq'], effect_at=EFFECT['at'])

        elif parsed.path == '/health':
            data = {'code': 200, 'service': 'local-caption-printer'}

        elif parsed.path == '/audio/state':
            data = audio_snapshot()

        elif parsed.path.startswith('/audio/file/'):
            self._serve_audio(parsed.path[len('/audio/file/'):])
            return

        elif parsed.path == '/audio/ws':
            self._serve_ws()
            return

        else:
            self.send_error(404)
            return

        self._json(data, origin=origin)

    def log_message(self, *args):
        pass


def main():
    # 日志统一用 UTF-8，不可编码的字符用替代符，不再依赖系统默认的 GBK
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    OUT.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get('CAPTION_PORT', '5500'))
    # 绑哪个地址：默认 127.0.0.1（Windows/macOS 直装，管理面不外露）。
    # Docker 里必须用 0.0.0.0——容器内绑 127.0.0.1 的话，Docker Desktop 的端口转发
    # 连进来是空的（TCP 能连上但没有任何响应），宿主机和 OBS 都拿不到数据。
    bind = os.environ.get('CAPTION_BIND', '127.0.0.1')
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    print(f'CAPTION_PRINTER_READY http://{bind}:{port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
