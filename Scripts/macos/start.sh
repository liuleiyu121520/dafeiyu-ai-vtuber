#!/usr/bin/env bash
# 启动大肥鱼容器，并等健康检查通过。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; }
info() { printf "%s     %s%s\n" "$DIM" "$*" "$RESET"; }

REBUILD=0
NO_WAIT=0
for arg in "$@"; do
    case "$arg" in
        --build|-b) REBUILD=1 ;;
        --no-wait)  NO_WAIT=1 ;;
        -h|--help)
            echo "用法: start.sh [--build] [--no-wait]"
            echo "  --build    重新构建镜像（改了代码或依赖时用）"
            echo "  --no-wait  启动后不等待健康检查"
            exit 0 ;;
    esac
done

if ! docker info >/dev/null 2>&1; then
    warn "Docker Desktop 未运行，尝试启动..."
    open -a Docker >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 2
    done
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker Desktop 未就绪，请手动打开 Docker.app"
    exit 1
fi

if [ ! -f .env ]; then
    warn ".env 不存在，从 .env.example 复制一份"
    cp .env.example .env
    chmod 600 .env
fi

env_get() {
    local key="$1" default="$2"
    local value
    value=$(grep -E "^[[:space:]]*$key=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d ' "'"'"'')
    if [ -n "$value" ]; then echo "$value"; else echo "$default"; fi
}

WEBUI_PORT=$(env_get WEBUI_PORT 8081)
LIVE2D_PORT=$(env_get LIVE2D_PORT 12345)
CAPTION_PORT=$(env_get CAPTION_PORT 5500)
BARRAGE_HUB_PORT=$(env_get BARRAGE_HUB_PORT 8888)

printf "%s> 启动容器%s\n" "$BOLD" "$RESET"
if [ "$REBUILD" = "1" ]; then
    docker compose up -d --build || { fail "启动失败"; exit 1; }
else
    docker compose up -d || { fail "启动失败"; exit 1; }
fi

if [ "$NO_WAIT" = "1" ]; then
    ok "已启动（未等待健康检查）"
    docker compose ps
    exit 0
fi

printf "%s> 等待服务就绪（最多 120 秒）%s\n" "$BOLD" "$RESET"
READY=0
for _ in $(seq 1 60); do
    if curl -sf --max-time 2 "http://127.0.0.1:$CAPTION_PORT/health" >/dev/null 2>&1 \
       && curl -sf --max-time 2 "http://127.0.0.1:$WEBUI_PORT/" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 2
done

printf "\n"
if [ "$READY" = "1" ]; then
    ok "服务已就绪"
else
    warn "120 秒内没等到 WebUI/字幕服务，请看日志：docker compose logs -f"
fi

cat <<EOF

  管理后台     http://127.0.0.1:$WEBUI_PORT
  横屏舞台     http://127.0.0.1:$LIVE2D_PORT/Live2D/stage-landscape.html
  竖屏舞台     http://127.0.0.1:$LIVE2D_PORT/Live2D/stage-portrait.html
  字幕/音频桥  http://127.0.0.1:$CAPTION_PORT/health
  弹幕 Hub     http://127.0.0.1:$BARRAGE_HUB_PORT/health

  查看日志     docker compose logs -f
  查看状态     scripts/macos/status.sh
  自检         scripts/macos/doctor.sh
  发送测试弹幕 scripts/macos/mock-chat.sh --text "你好，大肥鱼"
  停止         scripts/macos/stop.sh

EOF
docker compose ps
