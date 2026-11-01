"""离线 Mock 大模型：没有 API Key 时也能把整条链路跑通。

用途
----
* 单元测试 / 联调：不花钱、不依赖外网、结果可预测
* 首次部署：先确认「弹幕 → 回复 → 语音 → 口型 → 字幕」这条链是通的，
  再去填真实 Key

**不会伪装成真实模型**
`get_resp` 返回的文本里带固定的 `[mock]` 前缀，日志里也会明确写
「mock 模式，未调用真实大模型」，避免把测试结果误当成真实调用成功。

配置
----
config.json:
    "chat_type": "mock",
    "mock": {
        "reply": "自定义固定回复（可选）",
        "delay_ms": 0,
        "max_chars": 60
    }

环境变量 MOCK_LLM_REPLY / MOCK_LLM_DELAY_MS 可覆盖。
"""
import os
import random
import time

from utils.my_log import logger

# 没有配置固定回复时，按观众内容挑一句。都用大肥鱼的语气，方便直接听效果
CANNED_REPLIES = [
    "本鱼听到啦，{content}——这个我记下了。",
    "你刚说{content}，我这条鱼觉得挺有意思的。",
    "收到收到，{content}是吧，本鱼陪你聊聊。",
    "海底大厅收到你的消息了：{content}。",
    "嗯，{content}，这个我得好好想想。",
]

# 各种事件类型的默认回复，保持与原项目 thanks 文案风格一致
GIFT_REPLY = "谢谢{username}送的{gift_name}，本鱼今天又能多游两圈了。"
ENTRANCE_REPLY = "欢迎{username}来到海底大厅，随便坐。"
FOLLOW_REPLY = "谢谢{username}的关注，本鱼记住你了。"


class Mock:
    """与其它 Provider 同签名的假模型。"""

    def __init__(self, config):
        config = config or {}
        self.reply = os.environ.get("MOCK_LLM_REPLY") or config.get("reply") or ""
        self.delay_ms = int(os.environ.get("MOCK_LLM_DELAY_MS") or config.get("delay_ms") or 0)
        self.max_chars = int(config.get("max_chars") or 60)
        self.calls = 0
        logger.warning(
            "大模型使用 mock 模式：不会调用任何真实接口，回复内容带 [mock] 前缀。"
            "填好 API Key 后请把 config.json 的 chat_type 改成 chatgpt 或 zhipu。"
        )

    def _finish(self, text):
        self.calls += 1
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000.0)
        text = " ".join(str(text).split())
        if len(text) > self.max_chars:
            text = text[: self.max_chars]
        return f"[mock] {text}"

    def get_resp(self, data):
        """主入口：与 chatgpt.get_gpt_resp / zhipu.get_resp 同签名。

        Args:
            data: 原项目传的是 {"prompt": ...} 或 {"content": ...}；
                  这里统一兼容，尽量把观众原话读出来。
        """
        content = ""
        if isinstance(data, dict):
            content = str(data.get("content") or data.get("prompt") or "").strip()

        if self.reply:
            return self._finish(self.reply)

        if not content:
            return self._finish("本鱼在的，你想聊点什么？")

        template = random.choice(CANNED_REPLIES)
        return self._finish(template.format(content=content[:40]))

    # 供 my_handle 里 gift/entrance/follow 分支直接调用（如果将来接上）
    def gift_reply(self, username, gift_name):
        return self._finish(GIFT_REPLY.format(username=username, gift_name=gift_name))

    def entrance_reply(self, username):
        return self._finish(ENTRANCE_REPLY.format(username=username))

    def follow_reply(self, username):
        return self._finish(FOLLOW_REPLY.format(username=username))
