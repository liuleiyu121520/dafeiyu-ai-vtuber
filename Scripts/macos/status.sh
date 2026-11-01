#!/usr/bin/env bash
# 查看大肥鱼容器与各服务的运行状态。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "  %s[OK]%s %-14s %s\n" "$GREEN" "$RESET" "$1" "$2"; }
warn() { printf "  %s[!!]%s %-14s %s\n" "$YELLOW" "$RESET" "$1" "$2"; }
bad()  { printf "  %s[XX]%s %-14s %s\n" "$RED" "$RESET" "$1" "$2"; }

env_get() {
    local key="$1" default="$2"
    local value
    value=$(grep -E "^[[:space:]]*$key=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d ' "'"'"'')
    if [ -n "$value" ]; then echo "$value"; else echo "$default"; fi
}

WEBUI_PORT=$(env_get WEBUI_PORT 8081)
API_PORT=$(env_get API_PORT 8082)
LIVE2D_PORT=$(env_get LIVE2D_PORT 12345)
CAPTION_PORT=$(env_get CAPTION_PORT 5500)
BARRAGE_HUB_PORT=$(env_get BARRAGE_HUB_PORT 8888)

probe() {
    local name="$1" port="$2" path="$3" required="$4"
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port$path" 2>/dev/null)
    if [ "$code" = "200" ]; then
        ok "$name" ":$port  200"
    elif [ "$code" = "000" ]; then
        if [ "$required" = "1" ]; then bad "$name" ":$port  端口不通"; else warn "$name" ":$port  未启动（主程序还没运行？）"; fi
    else
        warn "$name" ":$port  HTTP $code"
    fi
}

printf "\n%s容器%s\n" "$BOLD" "$RESET"
if ! docker info >/dev/null 2>&1; then
    bad "docker" "Docker Desktop 未运行"
else
    ok "docker" "$(docker --version | awk '{print $3}' | tr -d ,)"
    STATE=$(docker inspect -f '{{.State.Status}}' dafeiyu 2>/dev/null || echo "missing")
    HEALTH=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' dafeiyu 2>/dev/null || echo "none")
    case "$STATE" in
        running) ok "container" "running / health=$HEALTH" ;;
        missing) bad "container" "不存在（先 scripts/macos/start.sh）" ;;
        *)       warn "container" "$STATE / health=$HEALTH" ;;
    esac

    if [ "$STATE" = "running" ]; then
        STATS=$(docker stats --no-stream --format '{{.CPUPerc}} / {{.MemUsage}}' dafeiyu 2>/dev/null | head -1)
        if [ -n "$STATS" ]; then ok "资源" "CPU/MEM $STATS"; fi
        UPTIME=$(docker inspect -f '{{.State.StartedAt}}' dafeiyu 2>/dev/null)
        if [ -n "$UPTIME" ]; then ok "启动于" "$UPTIME"; fi
    fi
fi

printf "\n%s服务端口%s\n" "$BOLD" "$RESET"
probe "WebUI"        "$WEBUI_PORT"        "/"                                   1
probe "主程序 API"   "$API_PORT"          "/get_sys_info"                        0
probe "Live2D"       "$LIVE2D_PORT"       "/Live2D/stage-landscape.html"         0
probe "字幕/音频桥"  "$CAPTION_PORT"      "/health"                              1
probe "弹幕 Hub"     "$BARRAGE_HUB_PORT"  "/health"                              0

printf "\n%s舞台与音频桥%s\n" "$BOLD" "$RESET"
STAGE=$(curl -s --max-time 3 "http://127.0.0.1:$CAPTION_PORT/audio/state" 2>/dev/null)
if [ -n "$STAGE" ]; then
    echo "$STAGE" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    print("  (解析失败)"); raise SystemExit
client = data.get("client") or {}
print("  浏览器音频桥：", "已连接" if client.get("connected") else "未连接（在 OBS 里打开舞台页）")
names = {"0": "弹幕回复", "1": "文案", "2": "BGM", "3": "点歌"}
for channel in ("0", "1", "2", "3"):
    item = data["channels"].get(channel) or {}
    print("  %-9s %-10s seq=%-3s vol=%.2f %s" % (
        names.get(channel, channel), item.get("state", "?"),
        item.get("seq", 0), float(item.get("volume") or 0),
        (item.get("error") or "")[:40]))
'
else
    warn "舞台状态" "拿不到 /audio/state"
fi

printf "\n%s配置摘要%s\n" "$BOLD" "$RESET"
if [ -f config.json ]; then
    python3 -c '
import json
config = json.load(open("config.json", encoding="utf-8"))
def get(*keys):
    node = config
    for key in keys:
        if not isinstance(node, dict): return None
        node = node.get(key)
    return node
def mask(value):
    if isinstance(value, list): value = value[0] if value else ""
    text = str(value or "")
    return "已填写(长度%d)" % len(text) if text else "未填写"
print("  平台          ", get("platform"), "/ 房间", get("room_display_id"))
print("  聊天模型      ", get("chat_type"), "/", get("chatgpt", "model"))
print("  模型 Key      ", mask(get("openai", "api_key")))
print("  TTS           ", get("audio_synthesis_type"))
print("  TTS Key       ", mask(get("fish_audio", "api_key")))
print("  播放方式      ", get("play_audio", "player"))
print("  BGM / 点歌    ", get("bgm", "enable"), "/", get("choose_song", "enable"))
print("  礼物/进场/关注", get("thanks", "gift_enable"), "/", get("thanks", "entrance_enable"), "/", get("thanks", "follow_enable"))
'
else
    bad "config.json" "不存在"
fi

printf "\n%s最近日志%s\n" "$BOLD" "$RESET"
docker compose logs --tail 12 2>/dev/null | sed 's/^/  /' || echo "  (无)"
printf "\n"
