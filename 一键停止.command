#!/usr/bin/env bash
# 大肥鱼 AI 虚拟主播 · 一键停止（双击运行）
#
# 按顺序做：
#   1. 先停主程序（让大肥鱼停止说话、清空播放队列）
#   2. 再停容器（优雅关闭，最多等 20 秒）
#   3. 检查有没有残留进程
#
# 数据目录（log/ out/ data/ song/ bgm_cache/）不会被删。
cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; }
info() { printf "%s     %s%s\n" "$DIM" "$*" "$RESET"; }

printf "\n%s🐟 大肥鱼 AI 虚拟主播 · 停止%s\n\n" "$BOLD" "$RESET"

if ! docker info >/dev/null 2>&1; then
    warn "Docker Desktop 没在运行，容器本来就是停的"
    read -r -p "按回车关闭..." _
    exit 0
fi

# ---- 1. 先停主程序 ----
if curl -sf --max-time 3 http://127.0.0.1:8081/ >/dev/null 2>&1; then
    info "让大肥鱼停止播报..."
    curl -s -X POST http://127.0.0.1:8081/sys_cmd \
        -H 'Content-Type: application/json' \
        -d '{"type":"stop","api_type":"api","data":{}}' >/dev/null 2>&1 || true
    sleep 2
    ok "主程序已停止"
else
    info "WebUI 没在跑，跳过停主程序"
fi

# ---- 2. 停容器 ----
printf "> 停止容器\n"
if docker compose down --timeout 20 2>&1 | tail -3; then
    ok "容器已停止"
else
    fail "容器停止失败，尝试强制停止"
    docker compose kill >/dev/null 2>&1 || true
    docker compose down >/dev/null 2>&1 || true
fi

# ---- 3. 残留检查 ----
LEFTOVER=$(docker ps -a --filter "name=dafeiyu" --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')
if [ "$LEFTOVER" = "0" ]; then
    ok "无残留容器"
else
    warn "还有 $LEFTOVER 个 dafeiyu 容器（不影响下次启动）"
fi

# 端口是否释放
STILL=0
for port in 8081 8082 12345 5500 8888; do
    if curl -sf --max-time 1 "http://127.0.0.1:$port/" >/dev/null 2>&1 \
       || nc -z 127.0.0.1 "$port" 2>/dev/null; then
        STILL=$((STILL+1))
    fi
done
if [ "$STILL" = "0" ]; then
    ok "端口已全部释放"
else
    warn "还有 $STILL 个端口被占用（可能是别的程序）"
fi

cat <<BANNER

  数据都保留着：
    log/        运行日志
    out/        合成过的音频
    data/       数据库与素材
    song/       点歌用的本地歌曲
    bgm_cache/  背景音乐缓存

  下次启动：双击「一键启动.command」

BANNER
printf "%s已停止。%s\n\n" "$BOLD$GREEN" "$RESET"
read -r -p "按回车关闭..." _
