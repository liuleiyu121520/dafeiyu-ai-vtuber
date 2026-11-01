"""抖音弹幕适配层。

原项目在 Windows 上靠 `tools/DouyinBarrageGrab/WssBarrageServer.exe` 提供
`ws://127.0.0.1:8888`，把弹幕以 `{"Type": n, "Data": "<json>"}` 推给
`utils/platforms/dy.py`。macOS 上没有这个 .NET 程序，所以这里用 Python
复刻同一个服务端协议，让 `dy.py` **一行都不用改**。

三个入口，共用同一套事件映射：
  * `hub`      —— WebSocket 服务端（:8888），兼容原 WssBarrageServer 协议
  * `inject`   —— 命令行/HTTP 注入单条事件（脚本、外部采集器都能用）
  * `mock`     —— 自动生成模拟弹幕，用于无人值守联调

统一内部事件结构（docs/ARCHITECTURE_MACOS.md §4.1）：

    {
      "source": "douyin",
      "event_id": "unique-event-id",
      "room_id": "123456",
      "type": "chat" | "gift" | "follow" | "enter" | "like" | "stats",
      "user_id": "user-id",
      "nickname": "观众昵称",
      "content": "你好，大肥鱼",
      "timestamp": 0,
      "raw": {}
    }
"""
