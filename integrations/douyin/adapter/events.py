"""统一事件结构 ↔ 原项目 WssBarrageServer 协议 的双向映射。

原协议（`utils/platforms/dy.py` 的 `on_message`）：

    {"Type": 1, "Data": "{\"User\": {\"Nickname\": \"...\"}, \"Content\": \"...\"}"}

| Type | 含义     | Data 里的关键字段 |
|------|----------|-------------------|
| 1    | 弹幕     | User.Nickname, Content |
| 2    | 点赞     | User.Nickname, Count |
| 3    | 进场     | User.Nickname |
| 4    | 关注     | User.Nickname |
| 5    | 礼物     | User.Nickname, GiftName, GiftCount, RepeatCount |
| 6    | 直播间统计 | OnlineUserCount, TotalUserCount, ... |
| 8    | 分享     | User.Nickname |

这里只做**纯函数**映射，不碰网络，方便单元测试。
"""
import json
import time
import uuid

# 统一事件类型 -> 原协议 Type
EVENT_TO_TYPE = {
    "chat": 1,
    "like": 2,
    "enter": 3,
    "follow": 4,
    "gift": 5,
    "stats": 6,
    "share": 8,
}

# 原协议 Type -> 统一事件类型（反向，给「外部采集器推原协议」的场景用）
TYPE_TO_EVENT = {value: key for key, value in EVENT_TO_TYPE.items()}

# 平台标识。写进 data["platform"]，主程序会拿它做来源区分
SOURCE = "douyin"


def new_event_id():
    return uuid.uuid4().hex[:16]


def normalize(event):
    """把任意来源的事件补全成统一结构。

    Args:
        event (dict): 至少要有 type 和 nickname；其余缺失字段补默认值

    Returns:
        dict: 完整统一结构

    Raises:
        ValueError: type 不认识
    """
    if not isinstance(event, dict):
        raise ValueError("event 必须是 dict")

    event_type = str(event.get("type") or "chat").lower()
    if event_type not in EVENT_TO_TYPE:
        raise ValueError(f"未知事件类型：{event_type}（支持 {sorted(EVENT_TO_TYPE)}）")

    nickname = str(event.get("nickname") or event.get("user_name") or "").strip()
    if not nickname:
        raise ValueError("nickname 不能为空")

    return {
        "source": str(event.get("source") or SOURCE),
        "event_id": str(event.get("event_id") or new_event_id()),
        "room_id": str(event.get("room_id") or ""),
        "type": event_type,
        "user_id": str(event.get("user_id") or ""),
        "nickname": nickname,
        "content": str(event.get("content") or ""),
        "timestamp": int(event.get("timestamp") or time.time() * 1000),
        "raw": event.get("raw") or {},
        # 礼物专有
        "gift_name": str(event.get("gift_name") or ""),
        "gift_count": int(event.get("gift_count") or event.get("num") or 1),
        "repeat_count": int(event.get("repeat_count") or event.get("gift_count") or event.get("num") or 1),
        # 点赞专有
        "count": int(event.get("count") or 1),
        # 统计专有
        "online_count": int(event.get("online_count") or 0),
        "total_count": int(event.get("total_count") or 0),
    }


def to_wss_frame(event):
    """统一事件 -> 原 WssBarrageServer 的一帧（dict，调用方 json.dumps 后发送）。"""
    event = normalize(event)
    event_type = event["type"]

    if event_type == "chat":
        data = {"User": {"Nickname": event["nickname"]}, "Content": event["content"]}
    elif event_type == "like":
        data = {"User": {"Nickname": event["nickname"]}, "Count": event["count"]}
    elif event_type == "enter":
        data = {"User": {"Nickname": event["nickname"]}}
    elif event_type == "follow":
        data = {"User": {"Nickname": event["nickname"]}}
    elif event_type == "share":
        data = {"User": {"Nickname": event["nickname"]}}
    elif event_type == "gift":
        data = {
            "User": {"Nickname": event["nickname"]},
            "GiftName": event["gift_name"],
            "GiftCount": event["gift_count"],
            "RepeatCount": event["repeat_count"],
        }
    elif event_type == "stats":
        data = {
            "OnlineUserCount": event["online_count"],
            "TotalUserCount": event["total_count"],
            "TotalUserCountStr": str(event["total_count"]),
            "OnlineUserCountStr": str(event["online_count"]),
            "MsgId": 0,
            "User": None,
            "Content": f"当前直播间人数 {event['online_count']}，累计直播间人数 {event['total_count']}",
            "RoomId": event["room_id"],
        }
    else:  # pragma: no cover - normalize 已经拦掉了
        raise ValueError(f"未知事件类型：{event_type}")

    return {"Type": EVENT_TO_TYPE[event_type], "Data": json.dumps(data, ensure_ascii=False)}


def from_wss_frame(frame):
    """原协议的一帧 -> 统一事件（外部采集器直接推原协议时用）。"""
    if not isinstance(frame, dict) or "Type" not in frame:
        raise ValueError("帧格式不对，需要 {Type, Data}")
    event_type = TYPE_TO_EVENT.get(int(frame["Type"]))
    if event_type is None:
        raise ValueError(f"未知 Type：{frame['Type']}")

    raw = frame.get("Data")
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})

    user = data.get("User") or {}
    nickname = user.get("Nickname") or data.get("Nickname") or ""

    event = {"type": event_type, "nickname": nickname, "raw": data}
    if event_type == "chat":
        event["content"] = data.get("Content") or ""
    elif event_type == "like":
        event["count"] = int(data.get("Count") or 1)
    elif event_type == "gift":
        event["gift_name"] = data.get("GiftName") or ""
        event["gift_count"] = int(data.get("GiftCount") or 1)
        event["repeat_count"] = int(data.get("RepeatCount") or event["gift_count"])
    elif event_type == "stats":
        event["online_count"] = int(data.get("OnlineUserCount") or 0)
        event["total_count"] = int(data.get("TotalUserCount") or 0)
    return normalize(event)


def to_main_program(event):
    """统一事件 -> 主程序 `/send` 接口的 body（HTTP 直连时用）。

    原项目 `main.py` 的 `POST /send` 接受：
        {"type": "comment"|"gift"|"entrance"|"follow"|"reread"|"tuning",
         "data": {...}}

    返回值 None 表示该事件类型不进入主程序（例如 like / stats 只做统计）。
    """
    event = normalize(event)
    event_type = event["type"]

    if event_type == "chat":
        return {
            "type": "comment",
            "data": {
                "platform": event["source"],
                "username": event["nickname"],
                "content": event["content"],
            },
        }
    if event_type == "enter":
        return {
            "type": "entrance",
            "data": {
                "platform": event["source"],
                "username": event["nickname"],
                "content": "进入直播间",
            },
        }
    if event_type == "follow":
        return {
            "type": "follow",
            "data": {"platform": event["source"], "username": event["nickname"]},
        }
    if event_type == "gift":
        return {
            "type": "gift",
            "data": {
                "platform": event["source"],
                "gift_name": event["gift_name"],
                "username": event["nickname"],
                "num": event["gift_count"],
                "unit_price": 0,
                "total_price": 0,
            },
        }
    return None
