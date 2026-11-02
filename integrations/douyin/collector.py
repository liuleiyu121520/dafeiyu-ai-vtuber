"""抖音弹幕采集器（纯 Python，替代 Windows 上的 WssBarrageServer.exe）。

为什么需要它
------------
`utils/platforms/dy.py` 只连 `ws://127.0.0.1:8888`，弹幕要靠 Windows 的
`tools/DouyinBarrageGrab/WssBarrageServer.exe` 推进来，macOS 上没有这个 .NET 程序。
仓库里虽有 `utils/platforms/dy2.py` 能直连抖音，但它是「采集 + 业务处理」一体的，
要改 `platform=dy2` 才能用，而且拿不到 exe 那条链路上的礼物价格表等既有行为。

本模块只做**采集与转发**，不碰业务：

    抖音 wss ──protobuf──> 本采集器 ──HTTP POST──> 本机 Hub :8888/inject ──ws──> dy.py

于是 `dy.py`、`My_handle.process_data()`、礼物价格表、动态配置切换
全部**一行都不用改**，与 Windows 上跑 exe 的行为完全一致。

与 dy2.py 的差异
----------------
1. 不 import my_handle / config，纯采集器，崩了不影响主程序；
2. 输出走 Hub 的统一事件结构（见 adapter/events.py），不是直接调业务；
3. 心跳按抖音现行协议发二进制 PushFrame(payload_type="hb")，
   而不是 dy2 里那个 `ws.send("hi")`；
4. 断线指数退避重连，直播间下播（ControlMessage.status==3）后按开播间隔重试。

风控提示
--------
wss URL 里的 version_code / sdk_version / signature 是公开抓包参数，抖音随时可能
收紧。抓不到数据时先 `--probe` 看握手是否被拒，再用 DY_WSS_URL 粘贴你自己
浏览器里抓到的完整地址覆盖（见 README 或 --help）。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from protobuf.douyin import (  # noqa: E402
    ChatMessage,
    ControlMessage,
    EmojiChatMessage,
    GiftMessage,
    LikeMessage,
    MemberMessage,
    PushFrame,
    Response,
    RoomUserSeqMessage,
    SocialMessage,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
LIVE_URL = "https://live.douyin.com/"
WSS_HOST = "wss://webcast3-ws-web-lq.douyin.com/webcast/im/push/v2/"

# 统计事件的占位昵称：events.normalize() 要求 nickname 非空，
# 但抖音的在线人数消息没有归属用户，这里填一个可读的固定值，
# dy.py 的 Type==6 分支也不会用到它（只读 OnlineUserCount/Content）。
STATS_NICKNAME = "直播间统计"


def log(msg: str) -> None:
    print(f"[collector] {time.strftime('%H:%M:%S')} {msg}", flush=True)


# ---------------------------------------------------------------------- 鉴权


def fetch_ttwid(timeout: float = 15.0) -> str | None:
    """访问直播站首页，从 Set-Cookie 里取 ttwid（匿名观众凭证）。"""
    req = urllib.request.Request(LIVE_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp.headers.get_all("Set-Cookie") or []:
                if raw.startswith("ttwid="):
                    return raw.split(";", 1)[0][len("ttwid="):]
    except Exception as error:  # noqa: BLE001
        log(f"取 ttwid 失败：{type(error).__name__} {error}")
    return None


def resolve_room_id(live_id: str, ttwid: str, timeout: float = 15.0) -> str | None:
    """把直播间号（URL 里那串数字）换成推流用的真 roomId。

    未开播时页面 SSR 里是 roomId\":\"$undefined\"，返回 None；
    调用方应当按「稍后重试」处理，而不是当错误。
    """
    url = f"{LIVE_URL}{live_id}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "cookie": f"ttwid={ttwid}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001
        log(f"请求直播间页失败：{type(error).__name__} {error}")
        return None

    match = re.search(r'roomId\\":\\"(\d+)\\"', html)
    if not match:
        return None
    return match.group(1)


def build_wss_url(room_id: str) -> str:
    """拼推流地址。允许用 DY_WSS_URL 整条覆盖（自己抓包时最省事）。"""
    override = os.environ.get("DY_WSS_URL")
    if override:
        return override.replace("{room_id}", room_id)
    return (
        f"{WSS_HOST}?app_name=douyin_web&version_code=180800"
        f"&webcast_sdk_version=1.3.0&update_version_code=1.3.0&compress=gzip"
        f"&internal_ext=internal_src:dim|wss_push_room_id:{room_id}"
        f"|wss_push_did:{room_id}|first_req_ms:{int(time.time() * 1000)}"
        f"&fetch_time:{int(time.time() * 1000)}|seq:1&cursor=t-{int(time.time() * 1000)}_r-1_d-1_u-1"
        f"&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3"
        f"&debug=false&endpoint=live_pc&support_wrds=1&user_unique_id={room_id}"
        f"&im_path=/webcast/im/fetch/&identity=audience&need_persist_msg_count=15"
        f"&device_platform=web&cookie_enabled=true&screen_width=1440&screen_height=900"
        f"&browser_language=zh&browser_platform=MacIntel&browser_name=Mozilla"
        f"&browser_version=5.0%20(Macintosh;%20Intel%20Mac%20OS%20X%2010_15_7)%20"
        f"AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Chrome/120.0.0.0%20Safari/537.36"
        f"&browser_online=true&tz_name=Asia/Shanghai&room_id={room_id}"
        f"&heartbeatDuration=0&signature=00000000"
    )


# ---------------------------------------------------------------------- 解码


def _nick(user) -> tuple[str, str] | None:
    """取 (昵称, 用户id)；昵称为空时返回 None 表示这条应当丢弃。

    betterproto 的 message 字段没赋值时返回的是**空对象而不是 None**，
    所以 `if m.user` 永远为真，必须校验 nick_name 本身非空——
    否则会把 nickname='' 转给 Hub，被 events.normalize() 拒成 400。
    """
    if user is None:
        return None
    nickname = (getattr(user, "nick_name", "") or "").strip()
    if not nickname:
        return None
    return nickname, str(getattr(user, "id", "") or "")


def decode_events(payload_bytes: bytes) -> list[dict]:
    """一帧 protobuf 二进制 -> 若干统一事件（纯函数，便于单测）。

    返回的事件字典直接满足 adapter/events.normalize() 的要求。
    未知 method 静默跳过：抖音加新消息类型不该让采集器挂掉。
    """
    response = Response().parse(payload_bytes)
    events: list[dict] = []

    for msg in response.messages_list:
        try:
            if msg.method == "WebcastChatMessage":
                m = ChatMessage().parse(msg.payload)
                who = _nick(m.user)
                if who and m.content:
                    events.append({"type": "chat", "nickname": who[0],
                                   "user_id": who[1], "content": m.content})
            elif msg.method == "WebcastEmojiChatMessage":
                m = EmojiChatMessage().parse(msg.payload)
                who = _nick(m.user)
                if who and m.default_content:
                    events.append({"type": "chat", "nickname": who[0],
                                   "content": m.default_content})
            elif msg.method == "WebcastGiftMessage":
                m = GiftMessage().parse(msg.payload)
                name = getattr(m.gift, "name", "") if m.gift else ""
                # dy.py 用 RepeatCount 算总价、GiftCount 显示数量，
                # 与 dy2 的 combo_count 口径对齐，避免连击礼物金额算少。
                num = int(m.combo_count or m.repeat_count or 1)
                who = _nick(m.user)
                if who and name:
                    events.append({"type": "gift", "nickname": who[0],
                                   "user_id": who[1], "gift_name": name,
                                   "gift_count": num, "repeat_count": num})
            elif msg.method == "WebcastMemberMessage":
                m = MemberMessage().parse(msg.payload)
                who = _nick(m.user)
                if who:
                    events.append({"type": "enter", "nickname": who[0],
                                   "user_id": who[1]})
            elif msg.method == "WebcastSocialMessage":
                m = SocialMessage().parse(msg.payload)
                who = _nick(m.user)
                if who:
                    events.append({"type": "follow", "nickname": who[0],
                                   "user_id": who[1]})
            elif msg.method == "WebcastLikeMessage":
                m = LikeMessage().parse(msg.payload)
                who = _nick(m.user)
                if who:
                    events.append({"type": "like", "nickname": who[0],
                                   "count": int(m.count or 1)})
            elif msg.method == "WebcastRoomUserSeqMessage":
                m = RoomUserSeqMessage().parse(msg.payload)
                events.append({"type": "stats", "nickname": STATS_NICKNAME,
                               "online_count": int(m.total or 0),
                               "total_count": int(m.total_user or 0)})
        except Exception as error:  # noqa: BLE001 - 单条消息解析失败不影响其它
            log(f"解析 {msg.method} 失败（已跳过）：{type(error).__name__} {error}")

    return events


def room_ended(payload_bytes: bytes) -> bool:
    """ControlMessage.status == 3 表示主播已下播。"""
    try:
        for msg in Response().parse(payload_bytes).messages_list:
            if msg.method == "WebcastControlMessage":
                if ControlMessage().parse(msg.payload).status == 3:
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


# ---------------------------------------------------------------------- 转发


class HubForwarder:
    """把统一事件 POST 给本机 Hub 的 /inject。失败只计数不抛异常。"""

    def __init__(self, hub_url: str):
        self.hub_url = hub_url.rstrip("/")
        self.sent = 0
        self.failed = 0

    def post(self, event: dict) -> bool:
        body = json.dumps(event, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.hub_url}/inject",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                result = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as error:  # noqa: BLE001
            self.failed += 1
            if self.failed in (1, 10) or self.failed % 100 == 0:
                log(f"转发失败（累计 {self.failed}）：{type(error).__name__} {error}")
            return False

        if result.get("code") != 200:
            self.failed += 1
            return False
        self.sent += 1
        # clients==0 说明 dy.py 还没连上 Hub，事件会被 Hub 丢掉，值得提示
        if not result.get("clients"):
            log("警告：Hub 当前没有消费者（dy.py 未连接），该事件已丢弃")
        return True


# ---------------------------------------------------------------------- 主循环


class Collector:
    def __init__(self, live_id: str, hub_url: str, once: bool = False):
        self.live_id = str(live_id).strip()
        self.forwarder = HubForwarder(hub_url)
        self.once = once
        self.ttwid: str | None = None
        self.room_id: str | None = None
        self.connected = False
        self._stop = threading.Event()

    # ---- 连接与保活

    def _heartbeat(self, ws) -> None:
        """按现行协议发二进制 hb 帧；dy2 里那个 ws.send("hi") 是文本帧，服务端不认。"""
        import websocket  # 延迟导入：--probe 之类不需要

        while not self._stop.wait(10.0):
            if not self.connected:
                return
            try:
                ws.send(bytes(PushFrame(payload_type="hb", payload=b"")),
                        websocket.ABNF.OPCODE_BINARY)
            except Exception:  # noqa: BLE001 - 连接已断，交给 run_forever 的 on_close
                return

    def _run_once(self) -> bool:
        """建立一次连接并跑到断开。返回 True 表示正常结束（--once）。"""
        import websocket

        self.ttwid = self.ttwid or fetch_ttwid()
        if not self.ttwid:
            log("拿不到 ttwid，5 秒后重试（网络或风控问题）")
            time.sleep(5)
            return False

        self.room_id = resolve_room_id(self.live_id, self.ttwid)
        if not self.room_id:
            log(f"roomId 解析失败：直播间 {self.live_id} 大概率没在开播，10 秒后重试")
            time.sleep(10)
            return False
        log(f"roomId={self.room_id}")

        state = {"ended": False}

        def on_open(ws):
            self.connected = True
            log("抖音 wss 已连接，等待弹幕…")
            threading.Thread(target=self._heartbeat, args=(ws,), daemon=True).start()

        def on_message(ws, message):
            package = PushFrame().parse(message)
            if package.payload_type == "ack":
                return
            payload = package.payload
            if (package.payload_encoding or "").lower() == "gzip":
                payload = gzip.decompress(payload)
            response = Response().parse(payload)

            # 服务端要求回执，不回就认为你掉线了，会停止推送
            if response.need_ack:
                ack = PushFrame(log_id=package.log_id, payload_type="ack",
                                payload=response.internal_ext.encode("utf-8"))
                try:
                    ws.send(bytes(ack), websocket.ABNF.OPCODE_BINARY)
                except Exception:  # noqa: BLE001
                    pass

            for event in decode_events(payload):
                self.forwarder.post(event)

            if room_ended(payload):
                log("主播已下播（ControlMessage.status=3），本次连接结束")
                state["ended"] = True
                ws.close()

        def on_error(ws, error):
            self.connected = False
            text = str(error)
            if "handshake-msg" in text or "Handshake status" in text:
                m = re.search(r"'handshake-msg':\s*'([^']*)'", text)
                log(f"握手被拒：{m.group(1) if m else text[:160]}"
                    f"（多半是 roomId 失效或被风控，可试 DY_WSS_URL 覆盖）")
            else:
                log(f"ws 错误：{text[:160]}")

        def on_close(ws, *_):
            self.connected = False
            log("抖音 wss 已断开")

        ws = websocket.WebSocketApp(
            build_wss_url(self.room_id),
            header={"User-Agent": USER_AGENT, "cookie": f"ttwid={self.ttwid}"},
            on_open=on_open, on_message=on_message,
            on_error=on_error, on_close=on_close,
        )
        ws.run_forever()
        return state["ended"] and self.once

    def run(self) -> int:
        import websocket  # 早失败：镜像里没装就直接报清楚

        del websocket
        log(f"采集器启动：live_id={self.live_id} -> Hub {self.forwarder.hub_url}")
        delay = 1.0
        while not self._stop.is_set():
            try:
                done = self._run_once()
            except KeyboardInterrupt:
                log("收到中断，退出")
                break
            except Exception as error:  # noqa: BLE001 - 采集器不能因单帧异常退出
                log(f"连接异常：{type(error).__name__} {error}")
                done = False

            if self.once and done:
                break
            # 连上过就把退避重置，没连上则指数退避到 30 秒封顶
            delay = 1.0 if self.room_id else min(delay * 2, 30.0)
            time.sleep(delay)

        log(f"结束：成功转发 {self.forwarder.sent} 条，失败 {self.forwarder.failed} 条")
        return 0


# ---------------------------------------------------------------------- CLI


def probe(live_id: str) -> int:
    """只做三步连通性自检，不常驻：ttwid -> roomId -> wss 握手。"""
    ttwid = fetch_ttwid()
    print(f"1) ttwid      : {'✅ ' + ttwid[:24] + '…' if ttwid else '❌ 拿不到'}")
    if not ttwid:
        return 1
    room = resolve_room_id(live_id, ttwid)
    print(f"2) roomId     : {'✅ ' + room if room else '❌ 未开播或房间号错/被风控'}")
    if not room:
        return 2

    import websocket

    outcome = {"ok": False, "detail": ""}

    def on_open(ws):
        outcome["ok"] = True
        ws.close()

    def on_error(ws, error):
        m = re.search(r"'handshake-msg':\s*'([^']*)'", str(error))
        outcome["detail"] = m.group(1) if m else str(error)[:200]

    ws = websocket.WebSocketApp(build_wss_url(room),
                                header={"User-Agent": USER_AGENT,
                                        "cookie": f"ttwid={ttwid}"},
                                on_open=on_open, on_error=on_error)
    ws.run_forever()
    print(f"3) wss 握手   : {'✅ 通过' if outcome['ok'] else '❌ ' + outcome['detail']}")
    return 0 if outcome["ok"] else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="抖音弹幕采集器：直连抖音 wss，转发到本机 Hub /inject",
    )
    parser.add_argument("--live-id", default=os.environ.get("ROOM_ID") or os.environ.get("DY_LIVE_ID"),
                        help="直播间号（URL 最后那串数字），默认取环境变量 ROOM_ID")
    parser.add_argument("--hub", default=os.environ.get("DAFEIYU_HUB_URL", "http://127.0.0.1:8888"),
                        help="Hub 地址，默认 http://127.0.0.1:8888")
    parser.add_argument("--once", action="store_true", help="收到下播信号就退出（调试用）")
    parser.add_argument("--probe", action="store_true", help="只做三步连通性自检后退出")
    args = parser.parse_args(argv)

    if not args.live_id:
        parser.error("缺少直播间号：给 --live-id 或设环境变量 ROOM_ID")

    if args.probe:
        return probe(args.live_id)
    return Collector(args.live_id, args.hub, once=args.once).run()


if __name__ == "__main__":
    raise SystemExit(main())
