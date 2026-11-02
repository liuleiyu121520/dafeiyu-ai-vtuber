#!/usr/bin/env bash
# 抖音弹幕采集器：纯 Python 直连抖音 wss，替代 Windows 上的 WssBarrageServer.exe。
#
# 链路：抖音 wss -> collector -> Hub(:8888)/inject -> utils/platforms/dy.py -> 主程序
# 与 Windows 跑 exe 共用同一套业务入口，dy.py 一行都没改。
#
# 用法：
#   scripts/macos/collector.sh start        启动（后台常驻，直播间没开播也会重试）
#   scripts/macos/collector.sh stop         停止
#   scripts/macos/collector.sh status       看状态与 Hub 计数
#   scripts/macos/collector.sh logs         跟随日志
#   scripts/macos/collector.sh probe        三步连通性自检（ttwid/roomId/wss 握手）
#
# 直播间号取自 .env 的 ROOM_ID，也可以临时指定：
#   DY_LIVE_ID=261378947940 scripts/macos/collector.sh probe
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$1"; }
bad()  { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$1"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$1"; }

LIVE_ID="${DY_LIVE_ID:-$(grep -E '^ROOM_ID=' .env 2>/dev/null | cut -d= -f2- | tr -d '"[:space:]')}"

if [ -z "$LIVE_ID" ]; then
    bad ".env 里没有 ROOM_ID，采集器不知道要抓哪个直播间"
    echo "    在 .env 里加一行 ROOM_ID=直播间号（URL 最后那串数字），或临时用 DY_LIVE_ID=xxx $0"
    exit 1
fi

if ! docker ps --filter "name=^dafeiyu$" --filter "status=running" --format '{{.Names}}' | grep -q dafeiyu; then
    warn "主容器没在运行，Hub(:8888) 连不上。先执行 scripts/macos/start.sh"
fi

case "${1:-}" in
  start)
    DY_LIVE_ID="$LIVE_ID" docker compose --profile collector up -d >/dev/null 2>&1 \
      || { bad "启动失败，看 docker compose --profile collector ps"; exit 1; }
    ok "采集器已启动，直播间 $LIVE_ID"
    sleep 3
    docker logs --tail 5 dafeiyu-collector 2>&1
    echo
    warn "roomId 解析失败多半是直播间没开播，开播后会自动接上，不用重启"
    ;;
  stop)
    docker compose --profile collector stop douyin-collector >/dev/null 2>&1
    ok "采集器已停止（主容器不受影响）"
    ;;
  status)
    st=$(docker ps -a --filter "name=dafeiyu-collector" --format '{{.Status}}' 2>/dev/null)
    if [ -z "$st" ]; then bad "采集器从未启动"; exit 1; fi
    ok "采集器: $st"
    if hub=$(curl -s -m 3 "http://127.0.0.1:8888/health" 2>/dev/null); then
      printf "     Hub: %s\n" "$hub"
      echo "$hub" | grep -q '"clients": 0' && warn "clients=0 表示 dy.py 没连上 Hub，弹幕进不了主程序"
      echo "$hub" | grep -qE '"received": 0' && warn "received=0 表示还没有弹幕进来（直播间可能没开播）"
    else
      bad "Hub(:8888) 不可达，主容器可能没起来"
    fi
    ;;
  logs)
    docker logs -f --tail 30 dafeiyu-collector 2>&1
    ;;
  probe)
    echo "自检直播间 $LIVE_ID 的三步连通性…"
    DY_LIVE_ID="$LIVE_ID" docker compose --profile collector run --rm --entrypoint python \
      douyin-collector /app/integrations/douyin/collector.py --live-id "$LIVE_ID" --probe
    rc=$?
    case $rc in
      0) ok "三步全通，可以收真实弹幕" ;;
      1) bad "拿不到 ttwid：网络问题或被风控，换个网络再试" ;;
      2) warn "拿不到 roomId：直播间没开播 / 房间号填错 / 被风控。开播后再试一次" ;;
      3) warn "wss 握手被拒（见上面 handshake-msg）。风控时把自己浏览器抓到的地址填进 .env 的 DY_WSS_URL" ;;
      *) bad "自检异常退出 $rc" ;;
    esac
    exit $rc
    ;;
  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
