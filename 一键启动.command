#!/usr/bin/env bash
# 大肥鱼 AI 虚拟主播 · 一键启动（双击运行）
#
# 做五件事：
#   1. 启动本地 MLX TTS 服务（audio_synthesis_type=custom_tts 时才真正需要它）
#   2. 确保 Docker Desktop 在跑
#   3. 启动容器
#   4. 等 WebUI 就绪后自动「一键运行」把主程序拉起来
#   5. 打开 OBS 和舞台页
cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; }
info() { printf "%s     %s%s\n" "$DIM" "$*" "$RESET"; }

printf "\n%s🐟 大肥鱼 AI 虚拟主播 · 启动%s\n\n" "$BOLD" "$RESET"

# ---- 1. 本地 MLX TTS 服务 ----
# config.json 里 audio_synthesis_type=custom_tts 时，语音合成走这个服务；
# 没启用的话起一下也无妨（空闲不占 GPU，几十 MB 内存），省得用户切换 TTS 后忘启动。
TTS_PROJ="$HOME/code/pre-study/mlx-audio-tts-service"
if curl -sf --max-time 2 http://127.0.0.1:8788/health >/dev/null 2>&1; then
    ok "本地 TTS 服务已在运行"
elif [ -x "$TTS_PROJ/.venv/bin/python" ]; then
    info "启动本地 TTS 服务（mlx-audio）..."
    (cd "$TTS_PROJ" && nohup env PYTHONPATH=src .venv/bin/python -m mlx_tts_service serve --port 8788 >> "$TTS_PROJ/service.log" 2>&1 &)
    for _ in $(seq 1 20); do
        curl -sf --max-time 2 http://127.0.0.1:8788/health >/dev/null 2>&1 && break
        sleep 1
    done
    if curl -sf --max-time 2 http://127.0.0.1:8788/health >/dev/null 2>&1; then
        ok "本地 TTS 服务已启动"
    else
        # 只提醒不拦：用户可能用的是 edge-tts / fish_audio，不需要本地服务
        warn "本地 TTS 服务没起来（不用 custom_tts 可忽略）；日志：$TTS_PROJ/service.log"
    fi
else
    warn "找不到本地 TTS 服务（$TTS_PROJ），跳过；不用 custom_tts 可忽略"
fi

# ---- 2. Docker ----
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

# ---- 3. 启动容器 ----
if ! docker compose up -d 2>&1 | tail -2; then
    fail "容器启动失败，看上面的输出"
    read -r -p "按回车关闭..." _
    exit 1
fi
ok "容器已启动"

# ---- 4. 等 WebUI ----
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

# ---- 5. 拉主程序 ----
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

# ---- 6. 打开 OBS + 舞台 ----
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
