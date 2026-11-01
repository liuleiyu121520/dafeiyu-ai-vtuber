#!/usr/bin/env bash
# 大肥鱼 · 开播前自检。逐项给出 [OK] / [!!] / [XX]，最后打印修复建议。
#
# 用法：
#   scripts/macos/doctor.sh           # 常规自检
#   scripts/macos/doctor.sh --online  # 额外真实调用一次大模型（会消耗极少额度）
#   scripts/macos/doctor.sh --clean   # 清理 out/ 里的临时音频（保留最近 200 个）
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'

BAD=0; WARN=0
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$*"; WARN=$((WARN+1)); }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; BAD=$((BAD+1)); }
fix()  { printf "%s     -> %s%s\n" "$DIM" "$*" "$RESET"; }
head() { printf "\n%s== %s ==%s\n" "$BOLD" "$*" "$RESET"; }

ONLINE=0
for arg in "$@"; do
    case "$arg" in
        --online) ONLINE=1 ;;
        --clean)
            head "清理临时音频"
            TOTAL=$(find out -maxdepth 1 \( -name '*.wav' -o -name '*.mp3' \) -type f 2>/dev/null | wc -l | tr -d ' ')
            if [ "$TOTAL" -gt 200 ]; then
                find out -maxdepth 1 \( -name '*.wav' -o -name '*.mp3' \) -type f -print0 2>/dev/null \
                    | xargs -0 ls -t 2>/dev/null | tail -n +201 | xargs rm -f 2>/dev/null
                ok "已清理，保留最近 200 个（原有 $TOTAL 个）"
            else
                ok "只有 $TOTAL 个音频，无需清理"
            fi
            exit 0 ;;
        -h|--help)
            echo "用法: doctor.sh [--online] [--clean]"
            exit 0 ;;
    esac
done

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

head "1. 宿主机环境"
if [ "$(uname -m)" = "arm64" ]; then ok "Apple Silicon（arm64）"; else warn "非 arm64：$(uname -m)（镜像按 linux/arm64 构建）"; fi
ok "macOS $(sw_vers -productVersion)"

if docker info >/dev/null 2>&1; then
    ok "Docker $(docker --version | awk '{print $3}' | tr -d ,) 运行中（$(docker info --format '{{.Architecture}}' 2>/dev/null)）"
    MEM=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
    if [ "${MEM:-0}" -gt 0 ] 2>/dev/null; then
        MEM_GB=$((MEM / 1073741824))
        if [ "$MEM_GB" -lt 4 ]; then
            warn "Docker 内存上限 ${MEM_GB}GB，建议在 Docker Desktop -> Settings -> Resources 调到 8GB+"
        else
            ok "Docker 内存上限 ${MEM_GB}GB"
        fi
    fi
else
    fail "Docker Desktop 未运行"
    fix "open -a Docker，等鲸鱼图标变绿"
fi

head "2. 关键文件"
if [ -f .env ]; then ok ".env 存在"; else warn ".env 不存在"; fix "cp .env.example .env"; fi
if [ -f config.json ]; then ok "config.json 存在"; else fail "config.json 缺失"; fi
if [ -f compose.yaml ]; then ok "compose.yaml 存在"; else fail "compose.yaml 缺失"; fi

MODEL_DIR="Live2D/live2d-model/大肥鱼"
if [ -f "$MODEL_DIR/大肥鱼.model3.json" ]; then
    ok "大肥鱼模型已就位"
    [ -f "$MODEL_DIR/大肥鱼.moc3" ] || warn "模型缺文件：大肥鱼.moc3"
    [ -f "$MODEL_DIR/大肥鱼.physics3.json" ] || warn "模型缺文件：大肥鱼.physics3.json"
    if [ -d "$MODEL_DIR/EXP" ]; then
        ok "表情目录 EXP/（$(find "$MODEL_DIR/EXP" -name '*.exp3.json' 2>/dev/null | wc -l | tr -d ' ') 个）"
    else
        warn "缺少 EXP/ 表情目录"
    fi
    if [ -d "$MODEL_DIR/大肥鱼.8192" ] || [ -d "$MODEL_DIR/大肥鱼.2048" ]; then
        ok "纹理目录已就位"
    else
        warn "缺少纹理目录（大肥鱼.8192/）"
    fi
else
    warn "缺少大肥鱼 Live2D 模型（不在开源仓库里）"
    fix "从 https://github.com/xkk6663/mao-plugin-feiyu 的 assets/da-feiyu/ 拷到 $MODEL_DIR/"
fi

[ -f Live2D/js/stage-audio.js ] && ok "浏览器音频桥前端存在" || fail "Live2D/js/stage-audio.js 缺失"
[ -f tools/caption_printer.py ] && ok "舞台服务存在" || fail "tools/caption_printer.py 缺失"
[ -f integrations/douyin/adapter/hub.py ] && ok "弹幕 Hub 存在" || fail "弹幕 Hub 缺失"

head "3. 配置"
if [ -f config.json ]; then
    python3 - <<'PY'
import json

try:
    config = json.load(open("config.json", encoding="utf-8"))
except Exception as error:
    print("[XX] config.json 解析失败：%s" % error)
    raise SystemExit


def get(*keys):
    node = config
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def key_of(*path):
    value = get(*path)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "")


problems = []
chat_type = get("chat_type")
tts = get("audio_synthesis_type")
player = get("play_audio", "player")

llm_key = key_of("openai", "api_key") or key_of("zhipu", "api_key")
if chat_type == "mock":
    print("[!!] 大模型：mock 模式（离线假回复，带 [mock] 前缀）")
    print("     -> 填好 Key 后把 config.json 的 chat_type 改成 chatgpt")
elif not llm_key:
    problems.append("大模型 Key 未填写（openai.api_key / zhipu.api_key）")
else:
    print("[OK] 大模型：%s，Key 已填写（长度 %d）" % (chat_type, len(llm_key)))

if tts == "mock_tts":
    print("[!!] TTS：mock_tts（离线合成音，不是真实配音）")
    print("     -> 换成 edge-tts（免费）或 fish_audio（大肥鱼音色）")
elif tts == "fish_audio" and not key_of("fish_audio", "api_key"):
    problems.append("TTS 选了 fish_audio 但 fish_audio.api_key 为空")
elif tts == "edge-tts":
    print("[OK] TTS：edge-tts（%s），需要外网" % get("edge-tts", "voice"))
else:
    print("[OK] TTS：%s" % tts)

if player == "browser":
    print("[OK] 播放方式：browser（浏览器 Web Audio，Docker 正确选择）")
elif player == "pygame":
    problems.append("播放方式还是 pygame：Docker Desktop 没有声卡，会完全静音。请改成 browser")
else:
    print("[!!] 播放方式：%s" % player)

print("[OK] 平台：%s / 房间 %s" % (get("platform"), get("room_display_id")))
print("[OK] BGM %s / 点歌 %s / 礼物 %s / 进场 %s" % (
    get("bgm", "enable"), get("choose_song", "enable"),
    get("thanks", "gift_enable"), get("thanks", "entrance_enable")))

for item in problems:
    print("[XX] " + item)
PY
else
    fail "config.json 缺失，无法检查配置"
fi

head "4. 服务端口"
probe() {
    local name="$1" port="$2" path="$3" required="$4"
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port$path" 2>/dev/null)
    if [ "$code" = "200" ]; then
        ok "$name :$port"
    elif [ "$code" = "000" ]; then
        if [ "$required" = "1" ]; then fail "$name :$port 端口不通"; fix "docker compose logs --tail 50"; else warn "$name :$port 未启动（主程序还没运行？）"; fi
    else
        warn "$name :$port HTTP $code"
    fi
}

probe "WebUI"       "$WEBUI_PORT"       "/"                              1
probe "主程序 API"  "$API_PORT"         "/get_sys_info"                 0
probe "Live2D"      "$LIVE2D_PORT"      "/Live2D/stage-landscape.html"  0
probe "字幕/音频桥" "$CAPTION_PORT"     "/health"                       1
probe "弹幕 Hub"    "$BARRAGE_HUB_PORT" "/health"                       0

head "5. 浏览器音频桥"
STATE=$(curl -s --max-time 3 "http://127.0.0.1:$CAPTION_PORT/audio/state" 2>/dev/null)
if [ -n "$STATE" ]; then
    CONNECTED=$(echo "$STATE" | python3 -c 'import json,sys; print(json.load(sys.stdin)["client"]["connected"])' 2>/dev/null)
    if [ "$CONNECTED" = "True" ]; then
        ok "浏览器已连接（OBS 浏览器源已打开舞台页）"
    else
        warn "浏览器还没连上——音频不会响"
        fix "在 OBS 里添加浏览器源：http://127.0.0.1:$LIVE2D_PORT/Live2D/stage-landscape.html"
        fix "或先用 Chrome 打开该地址（可能要点一下页面解锁自动播放）"
    fi
else
    fail "拿不到 /audio/state"
fi

if [ "$ONLINE" = "1" ]; then
    head "6. 在线验证（会消耗额度）"
    RESP=$(curl -s --max-time 40 -X POST "http://127.0.0.1:$API_PORT/llm" \
        -H 'Content-Type: application/json' \
        -d '{"type":"chatgpt","username":"自检","content":"只回复两个字：在的"}' 2>/dev/null)
    if echo "$RESP" | grep -q '"code": *200'; then
        ok "大模型调用成功"
        echo "$RESP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("     回复：", ((d.get("data") or {}).get("content") or "")[:60])' 2>/dev/null
    else
        fail "大模型调用失败：$(echo "$RESP" | head -c 200)"
        fix "检查 OPENAI_API_KEY / OPENAI_BASE_URL，或看 docker compose logs"
    fi
fi

printf "\n"
if [ "$BAD" -eq 0 ]; then
    printf "%s自检通过（%d 项提醒）%s\n\n" "$BOLD$GREEN" "$WARN" "$RESET"
    exit 0
else
    printf "%s自检发现 %d 个必须修复的问题、%d 项提醒%s\n\n" "$BOLD$RED" "$BAD" "$WARN" "$RESET"
    exit 1
fi
