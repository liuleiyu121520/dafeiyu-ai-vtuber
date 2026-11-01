#!/usr/bin/env bash
# 往运行中的容器里注入模拟弹幕（Mock 模式）。
#
# 走的是**真实链路**：Hub(:8888) -> utils/platforms/dy.py -> My_handle.process_data
# 与真实抖音弹幕共用同一套业务入口，不是旁路。
#
# 用法：
#   scripts/macos/mock-chat.sh --text "你好，大肥鱼"
#   scripts/macos/mock-chat.sh --scenario burst5
#   scripts/macos/mock-chat.sh --scenario mix
#   scripts/macos/mock-chat.sh --scenario offline
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

GREEN=$'\033[32m'; RED=$'\033[31m'; RESET=$'\033[0m'

if ! docker ps --filter "name=dafeiyu" --filter "status=running" --format '{{.Names}}' | grep -q dafeiyu; then
    printf "%s[XX]%s 容器没在运行。先执行 scripts/macos/start.sh\n" "$RED" "$RESET"
    exit 1
fi

if [ $# -eq 0 ]; then
    cat <<'EOF'
用法: mock-chat.sh --text "内容"
      mock-chat.sh --scenario <名称>

场景：
  single     单条弹幕
  burst5     连续 5 条
  burst20    短时间 20 条
  duplicate  重复弹幕（验证去重）
  long       超长文本
  gift       礼物
  follow     关注
  enter      进场
  like       点赞
  mix        混合事件
  offline    断线演练说明
EOF
    exit 1
fi

docker compose exec -T dafeiyu python -m integrations.douyin.adapter.mock_cli "$@"
