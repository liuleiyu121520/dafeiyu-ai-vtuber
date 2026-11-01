#!/usr/bin/env python3
"""把环境变量（.env）写进 config.json。

设计原则：
  1. **幂等**：同样的环境变量重复启动不会改动文件（值相同就不写盘）。
  2. **不覆盖用户手改**：只写「环境变量里显式给了值」的键，其余键一律不动。
  3. **不打印密钥**：日志里只出现键名和掩码长度。
  4. **可追溯**：写过的键记在 data/.env_managed_keys.json，方便排查「我的配置为什么被改了」。

用法：
    python docker/apply_env_config.py --config /app/config.json
    python docker/apply_env_config.py --config /app/config.json --dry-run
"""
import argparse
import json
import os
import sys
from pathlib import Path

MANAGED_RECORD = "data/.env_managed_keys.json"


def bool_(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def list_first(value):
    """openai.api_key 在 config.json 里是个列表，取第一个元素。"""
    return [str(value)]


# 环境变量 -> (config.json 里的路径, 类型转换)
# 路径是元组，逐层 get/set；类型用来把字符串转成数字/布尔/列表
ENV_MAP = {
    # ---- 大模型 ----
    "CHAT_TYPE": (("chat_type",), str),
    "OPENAI_BASE_URL": (("openai", "api"), str),
    "OPENAI_API_KEY": (("openai", "api_key"), list_first),
    "LLM_MODEL": (("chatgpt", "model"), str),
    "LLM_TEMPERATURE": (("chatgpt", "temperature"), float),
    "LLM_MAX_TOKENS": (("chatgpt", "max_tokens"), int),
    "LLM_TOP_P": (("chatgpt", "top_p"), float),
    "LLM_STREAM": (("chatgpt", "stream"), bool_),
    "LLM_PRESET": (("chatgpt", "preset"), str),
    "ZHIPU_API_KEY": (("zhipu", "api_key"), str),
    "ZHIPU_MODEL": (("zhipu", "model"), str),

    # ---- TTS ----
    "TTS_PROVIDER": (("audio_synthesis_type",), str),
    "EDGE_TTS_VOICE": (("edge-tts", "voice"), str),
    "EDGE_TTS_RATE": (("edge-tts", "rate"), str),
    "EDGE_TTS_VOLUME": (("edge-tts", "volume"), str),
    "FISH_AUDIO_KEY": (("fish_audio", "api_key"), str),
    "FISH_AUDIO_REFERENCE_ID": (("fish_audio", "reference_id"), str),
    "FISH_AUDIO_MODEL": (("fish_audio", "model"), str),

    # ---- 平台与房间 ----
    "PLATFORM": (("platform",), str),
    "ROOM_ID": (("room_display_id",), str),

    # ---- 播放 ----
    # browser: 音频交给 OBS 浏览器源播放（Docker 推荐）
    # pygame : 本机声卡（只有非容器部署才可用）
    "PLAY_AUDIO_PLAYER": (("play_audio", "player"), str),
    "PLAY_AUDIO_ENABLE": (("play_audio", "enable"), bool_),

    # ---- 舞台 / 字幕 ----
    "LIVE2D_PORT": (("live2d", "port"), int),
    "LIVE2D_ENABLE": (("live2d", "enable"), bool_),
    "LIVE2D_MODEL": (("live2d", "name"), str),
    "CAPTION_API": (("web_captions_printer", "api_ip_port"), str),
    "CAPTION_ENABLE": (("web_captions_printer", "enable"), bool_),

    # ---- 互动 ----
    "BGM_ENABLE": (("bgm", "enable"), bool_),
    "BGM_VOLUME": (("bgm", "volume"), float),
    "SONG_ENABLE": (("choose_song", "enable"), bool_),
    "GIFT_THANKS_ENABLE": (("thanks", "gift_enable"), bool_),
    "ENTRANCE_WELCOME_ENABLE": (("thanks", "entrance_enable"), bool_),
    "FOLLOW_THANKS_ENABLE": (("thanks", "follow_enable"), bool_),

    # ---- 回复长度 ----
    "REPLY_MAX_CHARS": (("filter", "max_char_len"), int),
    "REPLY_MAX_LEN": (("filter", "max_len"), int),

    # ---- WebUI ----
    # 容器内 WebUI 必须绑 0.0.0.0，绑 127.0.0.1 时 Docker Desktop 端口转发连进来是空的
    "WEBUI_HOST": (("webui", "ip"), str),
    "WEBUI_PORT": (("webui", "port"), int),
    "WEBUI_AUTO_RUN": (("webui", "auto_run"), bool_),
}


def mask(value):
    """日志里只显示长度，绝不打印内容。"""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = str(value)
    return f"<已设置 长度{len(text)}>" if text else "<空>"


def set_path(config, path, value):
    """逐层写；中间层不存在就建字典。返回是否发生了改动。"""
    node = config
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    last = path[-1]
    if node.get(last) == value:
        return False
    node[last] = value
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/app/config.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"[env-config] 找不到 {config_path}，跳过", file=sys.stderr)
        return 1

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)

    changed = []
    unchanged = []
    for env_name, (path, caster) in ENV_MAP.items():
        raw = os.environ.get(env_name)
        if raw is None or raw == "":
            continue
        try:
            value = caster(raw)
        except Exception as error:  # noqa: BLE001
            print(f"[env-config] WARN: {env_name} 转换失败（{error}），跳过", file=sys.stderr)
            continue
        if set_path(config, path, value):
            changed.append(f"{'.'.join(path)} = {mask(value)}")
        else:
            unchanged.append(".".join(path))

    # 记录本次由环境变量管理的键（给 doctor.sh 用）
    managed = sorted(
        ".".join(path) for env_name, (path, _caster) in ENV_MAP.items() if os.environ.get(env_name)
    )
    if not args.dry_run:
        try:
            record_path = config_path.parent / MANAGED_RECORD
            record_path.parent.mkdir(parents=True, exist_ok=True)
            record_path.write_text(
                json.dumps({"managed": managed}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass  # 只读挂载时写不进去，不影响主流程

    if not changed:
        print(f"[env-config] 无变化（{len(unchanged)} 项已是目标值）")
        return 0

    if args.dry_run:
        print("[env-config] dry-run，以下键**将会**被修改：")
        for item in changed:
            print(f"  - {item}")
        return 0

    # 写回文件。
    # 优先原子替换（避免进程崩溃时写坏 config.json）；
    # 但 config.json 在 Docker 里是**单文件 bind mount**，宿主机文件被挂成一个
    # mount point，os.replace() 会报 EBUSY（Device or resource busy）。
    # 这时退化成原地截断重写——同一个 inode，不会破坏挂载。
    payload = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    temp_path = config_path.with_suffix(config_path.suffix + ".tmp")
    replaced = False
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
        temp_path.replace(config_path)
        replaced = True
    except OSError:
        # bind mount / 只读父目录：退回原地写
        try:
            temp_path.unlink()
        except OSError:
            pass
    if not replaced:
        with config_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)

    print(f"[env-config] 已更新 {len(changed)} 个键：")
    for item in changed:
        print(f"  - {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
