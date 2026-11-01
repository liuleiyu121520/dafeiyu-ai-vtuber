#!/usr/bin/env bash
# 大肥鱼 AI 虚拟主播 · 一键启动（双击运行）
#
# 做四件事：
#   1. 确保 Docker Desktop 在跑
#   2. 启动容器
#   3. 等 WebUI 就绪后自动「一键运行」把主程序拉起来
#   4. 打开 OBS 和舞台页
cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; }
info() { printf "%s     %s%s\n" "$DIM" "$*" "$RESET"; }

printf "\n%s🐟 大肥鱼 AI 虚拟主播 · 启动%s\n\n" "$BOLD" "$RESET"

# ---- 1. Docker ----
if ! docker info >/dev/null 2>&1; then
    warn "Docker Desktop 没在运行，正在启动（首次可能要 30 秒）..."
    open -a Docker >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 2
    done
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker Desktop 起不来。请手动打开 Docker.app 后重试"
    read -r -p "按回车关闭..." _
    exit 1
fi
ok "Docker 就绪"

# ---- 2. 启动容器 ----
if ! docker compose up -d 2>&1 | tail -2; then
    fail "容器启动失败，看上面的输出"
    read -r -p "按回车关闭..." _
    exit 1
fi
ok "容器已启动"

# ---- 3. 等 WebUI ----
info "等待 WebUI 就绪（最多 120 秒）..."
READY=0
for _ in $(seq 1 60); do
    if curl -sf --max-time 2 http://127.0.0.1:8081/ >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 2
done
if [ "$READY" != "1" ]; then
    fail "WebUI 没起来，看日志：docker compose logs --tail 50"
    read -r -p "按回车关闭..." _
    exit 1
fi
ok "WebUI 就绪"

# ---- 4. 拉主程序 ----
if curl -sf --max-time 3 http://127.0.0.1:8082/get_sys_info >/dev/null 2>&1; then
    ok "主程序已在运行"
else
    info "启动主程序（大肥鱼开始上班）..."
    curl -s -X POST http://127.0.0.1:8081/sys_cmd \
        -H 'Content-Type: application/json' \
        -d '{"type":"run","api_type":"api","data":{"config_path":"config.json"}}' >/dev/null
    for _ in $(seq 1 30); do
        curl -sf --max-time 2 http://127.0.0.1:8082/get_sys_info >/dev/null 2>&1 && break
        sleep 2
    done
    if curl -sf --max-time 3 http://127.0.0.1:8082/get_sys_info >/dev/null 2>&1; then
        ok "主程序已启动"
    else
        warn "主程序还没起来，稍等一会儿或看日志"
    fi
fi

# ---- 5. 打开 OBS + 舞台 ----
if [ -d /Applications/OBS.app ]; then
    if pgrep -f "OBS.app/Contents/MacOS/OBS" >/dev/null 2>&1; then
        ok "OBS 已在运行"
    else
        info "打开 OBS..."
        open -a OBS >/dev/null 2>&1 || true
    fi
else
    warn "没装 OBS。舞台页可以直接用浏览器打开看效果"
fi

open "http://127.0.0.1:12345/Live2D/stage-landscape.html" >/dev/null 2>&1 || true
ok "已在浏览器打开舞台页"

cat <<BANNER

  管理后台     http://127.0.0.1:8081
  横屏舞台     http://127.0.0.1:12345/Live2D/stage-landscape.html
  竖屏舞台     http://127.0.0.1:12345/Live2D/stage-portrait.html

  发测试弹幕   ./scripts/macos/mock-chat.sh --text "你好，大肥鱼"
  查看状态     ./scripts/macos/status.sh
  停止         ./一键停止.command

BANNER
printf "%s启动完成。%s\n\n" "$BOLD$GREEN" "$RESET"
read -r -p "按回车关闭这个窗口（不影响后台运行）..." _
