#!/usr/bin/env bash
# 大肥鱼 AI 虚拟主播 · macOS 一键安装（幂等）
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
warn() { printf "%s[!!]%s %s\n" "$YELLOW" "$RESET" "$*"; }
fail() { printf "%s[XX]%s %s\n" "$RED" "$RESET" "$*"; }
info() { printf "%s     %s%s\n" "$DIM" "$*" "$RESET"; }
step() { printf "\n%s> %s%s\n" "$BOLD" "$*" "$RESET"; }

FAILED=0

step "1/5 检查运行环境"
if [ "$(uname -m)" != "arm64" ]; then
    warn "当前不是 Apple Silicon（$(uname -m)）。镜像按 linux/arm64 构建，"
    info "Intel Mac 请把 compose.yaml 的 platform 改成 linux/amd64"
fi
ok "$(uname -m) / $(sw_vers -productName) $(sw_vers -productVersion)"

if ! command -v docker >/dev/null 2>&1; then
    fail "找不到 docker。请安装 Docker Desktop for Mac"
    exit 1
fi
ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"

if ! docker info >/dev/null 2>&1; then
    warn "Docker Desktop 没有运行，尝试启动..."
    open -a Docker >/dev/null 2>&1 || true
    for _ in $(seq 1 60); do
        docker info >/dev/null 2>&1 && break
        sleep 2
    done
fi
if docker info >/dev/null 2>&1; then
    ok "Docker daemon 正常（$(docker info --format '{{.ServerVersion}} / {{.Architecture}}' 2>/dev/null)）"
else
    fail "Docker Desktop 仍未就绪，请手动打开 Docker.app 后重跑"
    exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
    fail "docker compose v2 不可用，请升级 Docker Desktop"
    exit 1
fi
ok "docker compose $(docker compose version --short 2>/dev/null)"

step "2/5 检查 .env"
if [ -f .env ]; then
    ok ".env 已存在（不会覆盖）"
else
    cp .env.example .env
    ok "已从 .env.example 创建 .env"
    info "稍后请编辑 .env 填入 OPENAI_API_KEY / FISH_AUDIO_KEY"
fi
chmod 600 .env 2>/dev/null || true

step "3/5 检查 Live2D 大肥鱼模型"
MODEL_DIR="Live2D/live2d-model/大肥鱼"
if [ -f "$MODEL_DIR/大肥鱼.model3.json" ]; then
    ok "模型已就位：$MODEL_DIR"
    info "纹理 $(find "$MODEL_DIR" -name '*.png' 2>/dev/null | wc -l | tr -d ' ') 张，表情 $(find "$MODEL_DIR/EXP" -name '*.exp3.json' 2>/dev/null | wc -l | tr -d ' ') 个"
else
    warn "缺少大肥鱼 Live2D 模型（不在开源仓库里）"
    info "来源：https://github.com/xkk6663/mao-plugin-feiyu -> assets/da-feiyu/"
    info "放好后应有：大肥鱼.model3.json / 大肥鱼.moc3 / 大肥鱼.8192/ / EXP/ / 大肥鱼.physics3.json"
    FAILED=$((FAILED+1))
fi

step "4/5 准备运行期目录"
for dir in log out out/songroom data song bgm_cache; do
    if [ -d "$dir" ]; then
        info "已存在 $dir/"
    else
        mkdir -p "$dir"
        ok "创建 $dir/"
    fi
done

if [ -f config.json ]; then
    ok "config.json 已存在"
elif [ -f config.json.bak ]; then
    cp config.json.bak config.json
    ok "已从 config.json.bak 恢复 config.json"
else
    fail "config.json 不存在，仓库里也没有 config.json.bak"
    exit 1
fi

step "5/5 构建镜像"
info "首次构建约 5~10 分钟（下载 python 基础镜像 + 编译 PyAudio）"
info "之后改代码只重建最后一层，约 10 秒"
if docker compose build; then
    ok "镜像构建完成"
else
    fail "镜像构建失败，看上面的输出"
    info "国内网络可先设置 Docker Desktop 的代理"
    exit 1
fi

printf "\n"
if [ "$FAILED" -eq 0 ]; then
    printf "%s安装完成。%s\n\n" "$BOLD$GREEN" "$RESET"
else
    printf "%s安装完成，但有 %d 项需要注意。%s\n\n" "$BOLD$YELLOW" "$FAILED" "$RESET"
fi

cat <<'EOF'
下一步：

  1. 编辑 .env，至少填一项：
       OPENAI_API_KEY=...
       TTS_PROVIDER=edge-tts

  2. 启动：   scripts/macos/start.sh
  3. 自检：   scripts/macos/doctor.sh

  4. OBS 浏览器源（横屏）：
       http://127.0.0.1:12345/Live2D/stage-landscape.html

  5. 没有 Key 想先看效果：
       在 .env 里设 CHAT_TYPE=mock 和 TTS_PROVIDER=mock_tts，然后
       scripts/macos/mock-chat.sh --scenario mix
EOF
