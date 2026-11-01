"""验证「人设不会被时间顶掉」：会话里只能有一条 system。"""
import json

from utils.gpt_model.chatgpt import Chatgpt


def _configs():
    openai_cfg = {
        "api": "http://127.0.0.1:1/v1",
        "api_key": ["sk-test"],
    }
    chatgpt_cfg = {
        "model": "test-model",
        "preset": "你叫大肥鱼，是海底大厅的 AI 虚拟主播。",
        "stream": False,
    }
    return openai_cfg, chatgpt_cfg


def test_session_has_exactly_one_system_message():
    """多条 system 会让不少 OpenAI 兼容网关只认最后一条，人设被时间覆盖。"""
    openai_cfg, chatgpt_cfg = _configs()
    bot = Chatgpt(openai_cfg, chatgpt_cfg)
    session = bot.get_chat_session("one-system-user")

    systems = [m for m in session["msg"] if m["role"] == "system"]
    assert len(systems) == 1, f"应该只有一条 system，实际 {len(systems)} 条：{[s['content'][:30] for s in systems]}"
    assert "大肥鱼" in systems[0]["content"]


def test_time_is_appended_into_the_same_system():
    openai_cfg, chatgpt_cfg = _configs()
    bot = Chatgpt(openai_cfg, chatgpt_cfg)
    session = bot.get_chat_session("time-user")

    bot._apply_time(session)
    content = session["msg"][0]["content"]
    assert "大肥鱼" in content, "人设丢了"
    assert "当前时间" in content, "时间没写进去"
    assert len([m for m in session["msg"] if m["role"] == "system"]) == 1


def test_repeated_time_updates_do_not_pile_up():
    """每次对话都会刷新时间，不能越堆越长。"""
    openai_cfg, chatgpt_cfg = _configs()
    bot = Chatgpt(openai_cfg, chatgpt_cfg)
    session = bot.get_chat_session("repeat-user")

    for _ in range(5):
        bot._apply_time(session)

    content = session["msg"][0]["content"]
    assert content.count("当前时间") == 1, "时间被重复堆叠了"
    assert "大肥鱼" in content


def test_legacy_second_system_is_cleaned_up():
    """旧版本建出来的会话里有两条 system，要能被清掉。"""
    openai_cfg, chatgpt_cfg = _configs()
    bot = Chatgpt(openai_cfg, chatgpt_cfg)
    session = bot.get_chat_session("legacy-user")

    # 手动塞一条旧的 "current time is:" system，模拟历史会话
    session["msg"].append({"role": "system", "content": "current time is:2026-01-01 00:00:00"})
    assert len([m for m in session["msg"] if m["role"] == "system"]) == 2

    bot._apply_time(session)
    systems = [m for m in session["msg"] if m["role"] == "system"]
    assert len(systems) == 1
    assert "大肥鱼" in systems[0]["content"]


def test_user_and_assistant_turns_are_preserved():
    openai_cfg, chatgpt_cfg = _configs()
    bot = Chatgpt(openai_cfg, chatgpt_cfg)
    session = bot.get_chat_session("history-user")

    session["msg"].append({"role": "user", "content": "你好"})
    session["msg"].append({"role": "assistant", "content": "你好呀"})
    bot._apply_time(session)

    roles = [m["role"] for m in session["msg"]]
    assert roles == ["system", "user", "assistant"]
