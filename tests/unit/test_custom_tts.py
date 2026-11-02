"""本地 MLX Audio TTS（custom_tts）适配器。

这条链路最容易出错的地方不是请求本身，而是「配置里留空的项被当成有值发出去」：
voice 留空本该让服务端用默认音色，如果发成空字符串，服务端会拿 "" 去查音色表，
查不到再退回默认——看起来能用，但一旦默认音色名和空字符串的语义分叉就会踩坑。
"""

import asyncio

import pytest

from fakes import FakeConfig
from utils.audio_handle.my_tts import MY_TTS


class Recorder:
    """替掉 download_audio，把请求参数记下来，不发网络请求。"""

    def __init__(self):
        self.calls = []

    async def __call__(self, type, file_url, timeout=30, request_type="get", data=None,
                       json_data=None, audio_suffix="wav"):
        self.calls.append({
            "type": type,
            "url": file_url,
            "timeout": timeout,
            "request_type": request_type,
            "json": json_data,
            "suffix": audio_suffix,
        })
        return "/tmp/fake.wav"


def build_tts(config_data):
    tts = MY_TTS.__new__(MY_TTS)          # 跳过 __init__，它要读真实配置文件
    tts.config = FakeConfig(config_data)
    tts.audio_out_path = "./out"
    return tts


def run(tts, content, cfg=None):
    recorder = Recorder()
    tts.download_audio = recorder
    payload = {"content": content}
    if cfg is not None:
        payload["custom_tts"] = cfg
    result = asyncio.run(tts.custom_tts_api(payload))
    return result, recorder.calls


def test_defaults_when_config_is_minimal():
    tts = build_tts({"custom_tts": {"api_ip_port": "http://127.0.0.1:8788"}})
    result, calls = run(tts, "你好")

    assert result == "/tmp/fake.wav"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://127.0.0.1:8788/tts"
    assert call["request_type"] == "post"
    assert call["json"]["text"] == "你好"
    assert call["json"]["format"] == "wav"
    assert call["suffix"] == "wav"


def test_empty_voice_and_model_are_not_sent():
    tts = build_tts({"custom_tts": {"api_ip_port": "http://127.0.0.1:8788",
                                    "voice": "", "model": "", "lang_code": ""}})
    _result, calls = run(tts, "你好")
    body = calls[0]["json"]
    # 留空 = 用服务端默认，不能把空串发出去
    assert "voice" not in body
    assert "model" not in body
    assert "lang_code" not in body


def test_all_options_forwarded():
    cfg = {
        "api_ip_port": "http://192.168.1.10:8788/",
        "api_path": "v1/audio/speech",
        "voice": "我的声音",
        "model": "qwen3_tts",
        "lang_code": "z",
        "speed": 1.2,
        "format": "mp3",
        "preprocess": False,
        "ref_audio": "/tmp/ref.wav",
        "ref_text": "参考原文",
        "timeout": 30,
    }
    tts = build_tts({"custom_tts": cfg})
    _result, calls = run(tts, "说话")
    call = calls[0]

    # 结尾斜杠 + 没有前导斜杠的 api_path 都要被拼对
    assert call["url"] == "http://192.168.1.10:8788/v1/audio/speech"
    assert call["timeout"] == 30
    assert call["suffix"] == "mp3"
    assert call["json"] == {
        "text": "说话",
        "format": "mp3",
        "voice": "我的声音",
        "model": "qwen3_tts",
        "speed": 1.2,
        "lang_code": "z",
        "preprocess": False,
        "ref_audio": "/tmp/ref.wav",
        "ref_text": "参考原文",
    }


def test_payload_config_wins_over_file_config():
    """助播那套配置是跟着消息走的，不能被主配置盖掉。"""
    tts = build_tts({"custom_tts": {"api_ip_port": "http://127.0.0.1:1111", "voice": "主音色"}})
    _result, calls = run(tts, "你好", cfg={"api_ip_port": "http://127.0.0.1:2222", "voice": "副音色"})
    assert calls[0]["url"] == "http://127.0.0.1:2222/tts"
    assert calls[0]["json"]["voice"] == "副音色"


def test_missing_config_falls_back_to_default_endpoint():
    tts = build_tts({})
    _result, calls = run(tts, "你好")
    assert calls[0]["url"] == "http://127.0.0.1:8788/tts"


def test_bad_speed_does_not_crash_into_none():
    tts = build_tts({"custom_tts": {"api_ip_port": "http://127.0.0.1:8788", "speed": "abc"}})
    result, _calls = run(tts, "你好")
    # 参数坏掉时返回 None（上游会当作「这次没合成」跳过），而不是抛异常打断整条队列
    assert result is None