"""流式对话：user 消息不能被时间覆盖。

原代码 session['msg'][1] = {...system...} 假设 msg 里固定有两条 system。
人设与时间合并成一条 system 后，下标 1 正好是刚追加的 user 消息，
会被 system 顶掉 —— 上游直接报 "at least one message is required"。
"""
import inspect

from utils.gpt_model.chatgpt import Chatgpt


def _bot():
    return Chatgpt(
        {"api": "http://127.0.0.1:1/v1", "api_key": ["sk-test"]},
        {"model": "test-model", "preset": "你叫大肥鱼。", "stream": True},
    )


def _code_lines(source):
    """去掉注释和空行，只留真正会执行的代码。"""
    out = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(line)
    return out


def test_stream_keeps_the_user_message(monkeypatch):
    bot = _bot()
    captured = {}

    def fake_create_completion(client, stream=False, **kwargs):
        captured["messages"] = kwargs.get("messages")
        captured["stream"] = stream
        return iter([])

    monkeypatch.setattr(bot, "create_completion", fake_create_completion)
    bot.chat_stream("你叫什么名字", "stream-user")

    messages = captured["messages"]
    assert messages is not None, "没有拿到 messages"

    roles = [m["role"] for m in messages]
    assert "user" in roles, "user 消息丢了，实际 roles=%s" % roles

    users = [m for m in messages if m["role"] == "user"]
    assert users[-1]["content"] == "你叫什么名字"

    systems = [m for m in messages if m["role"] == "system"]
    assert len(systems) == 1, "应该只有一条 system，实际 %d" % len(systems)
    assert "大肥鱼" in systems[0]["content"], "人设丢了"
    assert captured["stream"] is True


def test_stream_does_not_assign_by_index():
    """回归保护：不能再靠 msg[1] 这种固定下标写消息。

    只检查真正的代码行，注释里提到旧写法是允许的。
    """
    code = "\n".join(_code_lines(inspect.getsource(Chatgpt.chat_stream)))
    assert "msg'][1] = " not in code, "又在用 msg[1] 覆盖消息了"