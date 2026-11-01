#!/usr/bin/env bash
# 大肥鱼 AI 虚拟主播 · 容器入口
#
# 启动顺序（每一项都可单独关闭）：
#   1. apply_env_config.py  把 .env 里的配置写进 config.json（幂等）
#   2. caption_printer.py   字幕 + 舞台信号 + 音频桥（:5500）  ← 主程序依赖它
#   3. barrage hub          Mock / 本机 WS 弹幕源（:8888）      ← 可选
#   4. webui.py             管理后台（:8081）→ 自己拉起 main.py（:8082/:12345）
#
# 注意：这里**不**直接启动 main.py。原项目 WebUI 的「一键运行」会 Popen main.py，
# 容器再起一份就会出现两套播放队列和两套弹幕监听。
set -euo pipefail

APP_DIR="${APP_DIR:-/app}"
cd "$APP_DIR"

log() { printf '[entrypoint] %s\n' "$*"; }

# ---------- 0. 目录准备（bind mount 可能是空的） ----------
mkdir -p "$APP_DIR/log" "$APP_DIR/out" "$APP_DIR/out/songroom" \
         "$APP_DIR/song" "$APP_DIR/bgm_cache" "$APP_DIR/data"
# 兜底：bind mount 进来的目录属主可能是 root（macOS Docker Desktop 常见），
# 写不进去就报出来，别让主程序在合成音频时才失败
for d in log out song bgm_cache data; do
    if [ ! -w "$APP_DIR/$d" ]; then
        log "WARN: $APP_DIR/$d 不可写（属主 $(stat -c %U "$APP_DIR/$d" 2>/dev/null || echo '?')）"
    fi
done

# ---------- 1. 环境变量 → config.json ----------
if [ "${APPLY_ENV_CONFIG:-1}" = "1" ]; then
    python /app/docker/apply_env_config.py --config "$APP_DIR/config.json" || \
        log "WARN: 环境变量应用配置失败，继续用现有 config.json"
fi

# ---------- 2. 舞台服务（字幕 + 音频桥） ----------
if [ "${ENABLE_CAPTION_SERVICE:-1}" = "1" ]; then
    log "启动舞台服务 :${CAPTION_PORT:-5500}"
    CAPTION_PORT="${CAPTION_PORT:-5500}" \
        python "$APP_DIR/tools/caption_printer.py" > "$APP_DIR/log/caption-service.log" 2>&1 &
    CAPTION_PID=$!
else
    CAPTION_PID=""
fi

# ---------- 3. 弹幕源 ----------
# BARRAGE_MODE=mock    本机 WS Hub（:8888），由 Mock 脚本注入，不需要抖音
# BARRAGE_MODE=hub     同上，但等外部采集器连进来
# BARRAGE_MODE=none    不启动（用平台内置的 dy2 直连，或不需要弹幕）
BARRAGE_MODE="${BARRAGE_MODE:-mock}"
HUB_PID=""
if [ "$BARRAGE_MODE" != "none" ]; then
    log "启动弹幕 Hub :${BARRAGE_HUB_PORT:-8888} (mode=$BARRAGE_MODE)"
    BARRAGE_HUB_PORT="${BARRAGE_HUB_PORT:-8888}" \
        python -m integrations.douyin.adapter.hub > "$APP_DIR/log/barrage-hub.log" 2>&1 &
    HUB_PID=$!
fi

# ---------- 优雅停止 ----------
# 镜像里没有 pkill（procps 未装），用 /proc 手工按命令行匹配找 PID。
# 不用 ps/pgrep 是为了少装一层包，也避免容器精简后工具缺失。
kill_by_cmdline() {
    local signal="$1"; shift
    local pattern="$1"; shift
    local self=$$
    for pid_dir in /proc/[0-9]*; do
        local pid="${pid_dir#/proc/}"
        [ "$pid" = "$self" ] && continue
        local cmdline
        cmdline=$(tr '\0' ' ' < "$pid_dir/cmdline" 2>/dev/null) || continue
        case "$cmdline" in
            *"$pattern"*)
                kill "-$signal" "$pid" 2>/dev/null || kill "$signal" "$pid" 2>/dev/null || true
                ;;
        esac
    done
}

shutdown() {
    log "收到停止信号，清理子进程"
    [ -n "$HUB_PID" ] && kill -TERM "$HUB_PID" 2>/dev/null || true
    [ -n "$CAPTION_PID" ] && kill -TERM "$CAPTION_PID" 2>/dev/null || true
    # WebUI 拉起的 main.py 是它的子进程，PID 1 是 tini，这里把整棵进程树收掉
    kill_by_cmdline TERM "main.py"
    kill_by_cmdline TERM "webui.py"
    sleep 2
    kill_by_cmdline KILL "main.py"
    kill_by_cmdline KILL "webui.py"
    wait 2>/dev/null || true
    log "已停止"
    exit 0
}
trap shutdown TERM INT

# ---------- 4. 前台跑 WebUI ----------
case "${1:-webui}" in
  webui)
    log "启动 WebUI :${WEBUI_PORT:-8081}"
    if [ "${AUTO_RUN:-1}" = "1" ]; then
        # WebUI 起来后自动点「一键运行」，把 main.py 拉起来
        export DAFEIYU_AUTORUN=1
    fi
    exec python "$APP_DIR/webui.py"
    ;;
  main)
    # 调试用：跳过 WebUI 直接跑主程序（没有管理后台）
    log "直接启动主程序"
    exec python "$APP_DIR/main.py"
    ;;
  bash|sh)
    exec /bin/bash
    ;;
  *)
    exec "$@"
    ;;
esac
