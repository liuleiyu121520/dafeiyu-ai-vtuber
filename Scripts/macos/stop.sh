#!/usr/bin/env bash
# 停止容器。默认保留数据（log/out/data/song/bgm_cache 都在宿主机上）。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

GREEN=$'\033[32m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; }
info() { printf "%s     %s%s\n" "$DIM" "$*" "$RESET"; }

CLEAN=0
for arg in "$@"; do
    case "$arg" in
        --clean) CLEAN=1 ;;
        -h|--help)
            echo "用法: stop.sh [--clean]"
            echo "  --clean  同时删除镜像（数据目录仍保留）"
            exit 0 ;;
    esac
done

printf "> 停止容器\n"
if docker compose down --timeout 20; then
    ok "容器已停止"
else
    fail "停止失败"
    exit 1
fi

LEFTOVER=$(docker ps -a --filter "name=dafeiyu" --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')
if [ "$LEFTOVER" = "0" ]; then
    ok "无残留容器"
else
    fail "还有 $LEFTOVER 个 dafeiyu 容器残留"
fi

if [ "$CLEAN" = "1" ]; then
    printf "> 删除镜像\n"
    if docker rmi dafeiyu-ai-vtuber:macos-arm64 >/dev/null 2>&1; then
        ok "镜像已删除"
    else
        info "镜像不存在或仍被占用"
    fi
fi

info "数据目录保留：log/ out/ data/ song/ bgm_cache/"
info "重新启动：scripts/macos/start.sh"
