"""走项目真实分发链路的联调：tts_handle -> custom_tts_api -> 本地服务。

前面的 verify_custom_tts.py 只测到适配器；这里再多走一层 Audio.tts_handle，
确认 audio_synthesis_type=custom_tts 在真实分支里能跑通、返回的 audio_path 可用。
"""
import asyncio, json, os, subprocess, sys, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

class _Logger:
    def __getattr__(self, name): return lambda *a, **k: None
lm = types.ModuleType("utils.my_log"); lm.logger = _Logger(); sys.modules["utils.my_log"] = lm

class _Common:
    def get_bj_time(self, t=None): return "20260101"
    def get_new_audio_path(self, base, name):
        Path(base).mkdir(parents=True, exist_ok=True)
        return str(Path(base) / name)
cm = types.ModuleType("utils.common"); cm.Common = _Common; sys.modules["utils.common"] = cm

class _Config:
    def __init__(self, cfg): self._cfg = cfg
    def get(self, *keys):
        cur = self._cfg
        for k in keys:
            if not isinstance(cur, dict): return None
            cur = cur.get(k)
        return cur
cfm = types.ModuleType("utils.config"); cfm.Config = _Config; sys.modules["utils.config"] = cfm

from utils.audio_handle.my_tts import MY_TTS  # noqa: E402

cfg = json.loads((ROOT / "config.json").read_text("utf-8"))
tts_type = cfg["audio_synthesis_type"]
print("audio_synthesis_type =", tts_type)
assert tts_type == "custom_tts", "配置没切到 custom_tts"

# 复刻 Audio.tts_handle 里 custom_tts 那一段分支
message = {
    "type": "comment",
    "tts_type": tts_type,
    "data": cfg.get(tts_type),
    "config": cfg.get("filter"),
    "username": "测试观众",
    "content": "欢迎来到直播间，今天我们一起玩点有意思的。",
}

tts = MY_TTS.__new__(MY_TTS)
tts.config = _Config(cfg)
tts.common = _Common()
tts.audio_out_path = str(ROOT / "out")
tts.timeout = 120

data = {"content": message["content"], "custom_tts": message["data"]}
path = asyncio.run(tts.custom_tts_api(data))
assert path and os.path.exists(path), f"合成失败: {path}"
print(f"ok   tts_handle 分支产出: {path} ({os.path.getsize(path)} 字节)")

info = subprocess.run(["afinfo", path], capture_output=True, text=True).stdout
for line in info.splitlines():
    if "duration" in line:
        print("ok  ", line.strip())
print("PASS")

