# 大肥鱼 AI 虚拟主播 · macOS Docker 部署指南

> 目标：在一台 Apple Silicon Mac 上用 Docker 跑起完整的大肥鱼，OBS 显示画面和声音，
> 直播伴侣可以推流。**不装 Windows、不用 CUDA、不部署外部 Linux 服务器。**

---

## 0. 这套方案是什么

```
抖音直播间
   │  (A) dy2 容器内直连抖音网页 wss
   │  (B) 本机弹幕 Hub :8888（Mock 或外部采集器）
   ▼
Docker 容器 dafeiyu
   ├── 字幕 / 舞台信号 / 音频桥     :5500
   ├── 弹幕 Hub                     :8888
   └── WebUI :8081 ──> main.py ──> :8082 API / :12345 Live2D
        │
        │  弹幕解析 → 大模型 → TTS → 播放队列
        ▼
   HTTP 音频字节（/audio/file/<id>）
        ▼
OBS 浏览器源（:12345 舞台页）
   ├── Live2D 渲染（海底场景）
   ├── Web Audio 播放声音（真的响）
   ├── AnalyserNode 取真实振幅 → 口型
   └── 字幕
        ▼
抖音直播伴侣（OBS 虚拟摄像头 / 窗口采集 + 音频）
```

**关键点**：容器里没有声卡，所以音频不在容器里播，而是把字节交给浏览器播。
浏览器用 Web Audio 解码，用 AnalyserNode 分析正在播放的 PCM 驱动口型。
这样声音、字幕、口型在同一台机器、同一份时钟上，不会错位，也不需要 BlackHole。

---

## 1. 环境要求

| 项目 | 要求 |
|---|---|
| 机器 | Apple Silicon（M1/M2/M3/M4/M5），本文在 **M5 Pro / 64GB / macOS 27.0.1** 上验证 |
| Docker | Docker Desktop 4.x+（本文 29.9.2 / Compose v5.6.1） |
| 内存给 Docker | ≥ 8GB（Settings → Resources） |
| 磁盘 | ≥ 10GB（镜像约 2.05GB + 模型 32MB + 缓存） |
| 网络 | 需要能访问 pypi / 微软 TTS / 网易云（BGM）；大模型看你的网关 |

Intel Mac 也能跑，把 `compose.yaml` 的 `platform` 和 `platforms` 改成 `linux/amd64`。

---

## 2. 三分钟起步

```bash
cd dafeiyu-ai-vtuber

# 1) 安装（幂等，会检查环境、建 .env、建目录、构建镜像）
scripts/macos/install.sh

# 2) 填 Key（至少填一项，也可以先用 Mock 模式跳过）
open -e .env

# 3) 启动
scripts/macos/start.sh

# 4) 自检
scripts/macos/doctor.sh
```

启动后打开：

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:8081 | 管理后台 |
| http://127.0.0.1:12345/Live2D/stage-landscape.html | 横屏舞台（OBS 用这个） |
| http://127.0.0.1:12345/Live2D/stage-portrait.html | 竖屏舞台 |
| http://127.0.0.1:5500/health | 字幕/音频桥 |
| http://127.0.0.1:8888/health | 弹幕 Hub |

---

## 3. 配置

### 3.1 两种配置方式（二选一，不要混着改）

**方式 A：改 .env（推荐给 Docker）**

```bash
# .env
CHAT_TYPE=chatgpt
OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OPENAI_API_KEY=你的key
LLM_MODEL=glm-4.7-flash

TTS_PROVIDER=edge-tts          # 先免费验证链路
PLAY_AUDIO_PLAYER=browser      # Docker 必须选这个
```

容器启动时 `docker/apply_env_config.py` 会把它们**幂等地**写进 `config.json`。
只写你显式给了值的键，其余键一个都不动。

**方式 B：改 config.json / 用 WebUI**

WebUI 保存后 `config.json` 立刻变（它是 bind mount）。但注意：
**下次容器启动时 .env 里的值会覆盖同名键**。不想被覆盖就把 .env 里那几行删掉或留空。

### 3.2 大模型

| 场景 | 配置 |
|---|---|
| 智谱（默认，免费模型） | `CHAT_TYPE=chatgpt` + `OPENAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4` + `OPENAI_API_KEY=...` + `LLM_MODEL=glm-4.7-flash` |
| 任意 OpenAI 兼容网关 | 同上，换 `OPENAI_BASE_URL` 和 `LLM_MODEL` |
| 智谱原生 SDK | `CHAT_TYPE=zhipu` + `ZHIPU_API_KEY=...` |
| **完全离线测试** | `CHAT_TYPE=mock`（回复带 `[mock]` 前缀，绝不冒充真实调用） |

### 3.3 语音合成

| Provider | 说明 |
|---|---|
| `edge-tts` | 免费，需要外网。默认音色 `zh-CN-XiaoyiNeural` |
| `fish_audio` | 大肥鱼原音色，需要 Key + `reference_id` |
| `mock_tts` | **离线合成音**（按字数节奏起伏），用来验证播放/口型/字幕时序。日志会标注「不是真实配音」 |

### 3.4 播放方式

```bash
PLAY_AUDIO_PLAYER=browser   # Docker 唯一可行方案
PLAY_AUDIO_PLAYER=pygame    # 只有非容器部署（直接装 macOS）才用
```

### 3.5 弹幕来源

```bash
PLATFORM=dy       # 通过本机 Hub :8888 收弹幕（推荐，配合 BARRAGE_MODE）
PLATFORM=dy2      # 容器内直连抖音网页 wss（需要直播间正在开播）
```

`BARRAGE_MODE`：

| 值 | 行为 |
|---|---|
| `mock` | 启动 Hub，可用 `scripts/macos/mock-chat.sh` 注入模拟弹幕 |
| `hub` | 启动 Hub，等外部采集器连进来 |
| `none` | 不启动 Hub（用 dy2 直连，或不需要弹幕） |

#### 收真实抖音弹幕：本机采集器

Windows 上弹幕靠 `tools/DouyinBarrageGrab/WssBarrageServer.exe` 推到 :8888，
macOS 没有这个 .NET 程序。仓库提供等价的纯 Python 采集器
`integrations/douyin/collector.py`：直连抖音 wss、解 protobuf，然后把事件
POST 给 Hub 的 `/inject`。**`dy.py` 与业务侧一行都不用改**，和 Windows 跑
exe 走的是同一套入口。

```bash
scripts/macos/collector.sh probe     # 三步自检：ttwid → roomId → wss 握手
scripts/macos/collector.sh start     # 开播前启动（后台常驻，自动重连）
scripts/macos/collector.sh status    # 看采集器与 Hub 计数
scripts/macos/collector.sh logs      # 跟随日志
scripts/macos/collector.sh stop      # 停止（不影响主容器）
```

直播间号复用 `.env` 的 `ROOM_ID`，采集器是独立的 compose profile，
`docker compose up -d` 不会带上它，必须显式 `--profile collector`（或用上面脚本）。

probe 结果怎么读：

| 卡在第几步 | 含义 | 处理 |
|---|---|---|
| 1 ttwid | 出网被拦或风控 | 换网络；关代理软件 |
| 2 roomId | **直播间没开播**（最常见） | 开播后自动接上，无需重启 |
| 3 wss 握手 | 返回 `DEVICE_BLOCKED` 等 | 浏览器里抓完整 wss 地址填进 `.env` 的 `DY_WSS_URL`，`{room_id}` 会被替换 |

> 采集器复用的 wss 参数是公开抓包值，抖音随时可能收紧。第 3 步失败但
> `dy2` 也同时失效，说明是平台侧变更，不是本机配置问题。

---

## 4. OBS 配置

### 4.1 添加浏览器源

1. 来源 → `+` → **浏览器**
2. 名称：`大肥鱼横屏`
3. URL：`http://127.0.0.1:12345/Live2D/stage-landscape.html`
4. 宽 **1920**、高 **1080**
5. **勾选「通过 OBS 控制音频」** ← 这一项必须勾，否则声音进不来
6. 勾选「关闭源时关闭浏览器」建议不勾（刷新更稳）
7. 自定义 CSS 留空

竖屏直播把 URL 换成 `stage-portrait.html`，宽 **1080**、高 **1920**。

### 4.2 音频路由（关键，容易踩坑）

OBS 里这个浏览器源的音频会出现在 **混音器** 面板，名字就是来源名。
确认三件事：

1. 混音器里该来源有音量跳动（有人说话时）
2. 该来源没有被静音
3. 设置 → 音频 → 高级音频属性里，该来源的「音频监听」= **关闭**（避免回声）

### 4.3 检查声音真的进了 OBS

```bash
# 1) 看浏览器端是否连上、通道是否在播
curl -s http://127.0.0.1:5500/audio/state | python3 -m json.tool

# 2) 发一条测试弹幕
scripts/macos/mock-chat.sh --text "你好，大肥鱼"

# 3) 看 OBS 混音器
```

**常见误区**：OBS 虚拟摄像头**不包含音频**。直播伴侣里必须单独选音频输入。

### 4.4 推给直播伴侣

抖音官方 Mac 直播伴侣有两种接法：

| 方式 | 画面 | 声音 |
|---|---|---|
| **窗口采集**（推荐） | 采集 OBS 主窗口/全屏 | 直播伴侣里选「系统声音」或对应输入设备 |
| 虚拟摄像头 | OBS「启动虚拟摄像机」→ 直播伴侣选摄像头 | **仍需单独选音频输入** |

用窗口采集时，记得把 OBS 的「节目」全屏化或用一个固定窗口，避免把操作界面播出去。

---

## 5. 日常操作

```bash
scripts/macos/start.sh              # 启动
scripts/macos/start.sh --build      # 改代码后重建并启动
scripts/macos/stop.sh               # 停止（数据保留）
scripts/macos/stop.sh --clean       # 停止并删镜像
scripts/macos/status.sh             # 状态一览
scripts/macos/doctor.sh             # 开播前自检
scripts/macos/doctor.sh --online    # 额外真实调一次大模型
scripts/macos/doctor.sh --clean     # 清理 out/ 里超过 200 个的临时音频
scripts/macos/mock-chat.sh --scenario mix   # 一组混合模拟事件
```

等价的原生命令：

```bash
docker compose up -d --build     # 一键启动
docker compose ps                # 看状态
docker compose logs -f           # 看日志
docker compose down              # 停止
```

---

## 6. 模拟弹幕

```bash
scripts/macos/mock-chat.sh --text "你好，大肥鱼"
scripts/macos/mock-chat.sh --scenario single
scripts/macos/mock-chat.sh --scenario burst5
scripts/macos/mock-chat.sh --scenario burst20 --interval 0.05
scripts/macos/mock-chat.sh --scenario duplicate   # 重复弹幕（验证去重）
scripts/macos/mock-chat.sh --scenario long        # 超长文本
scripts/macos/mock-chat.sh --scenario gift
scripts/macos/mock-chat.sh --scenario follow
scripts/macos/mock-chat.sh --scenario enter
scripts/macos/mock-chat.sh --scenario like
scripts/macos/mock-chat.sh --scenario mix
scripts/macos/mock-chat.sh --scenario offline     # 断线演练说明
```

Mock 走的是**真实链路**：Hub → `utils/platforms/dy.py` → `My_handle.process_data`，
和真实抖音弹幕共用同一套业务入口，不是旁路。

---

## 7. 换 API Key / 音色 / 模型 / Live2D 形象

### 7.1 换大模型 Key

```bash
# 改 .env
OPENAI_API_KEY=新的key
scripts/macos/start.sh --build      # 或者 docker compose up -d（env 变更会重建容器）
```

或者直接在 WebUI → 通用配置 → 大语言模型 里改，保存后重启主程序即可。

### 7.2 换 TTS 音色

edge-tts：
```bash
EDGE_TTS_VOICE=zh-CN-XiaoxiaoNeural   # 可选值见 data/edge-tts-voice-list.txt
```

Fish Audio：
```bash
TTS_PROVIDER=fish_audio
FISH_AUDIO_KEY=你的key
FISH_AUDIO_REFERENCE_ID=音色id
FISH_AUDIO_MODEL=s2.1-pro-free
```

### 7.3 换 Live2D 形象

1. 把模型目录放到 `Live2D/live2d-model/<目录名>/`，里面要有 `<名字>.model3.json`
2. 改 `Live2D/js/model_name.js`：`var model_name = "<目录名>";`
3. `.env` 里 `LIVE2D_MODEL=<目录名>`
4. `scripts/macos/start.sh --build`（js 在镜像里）
5. OBS 里刷新浏览器源

> 仓库自带的 `Hiyori` / `Haru` / `hibiki` 是 Live2D 官方免费模型，可以直接换上去测试。

### 7.4 换直播间

```bash
ROOM_ID=你的直播间号       # https://live.douyin.com/<这个数字>
```

---

## 8. 端口一览

| 端口 | 服务 | 容器内绑定 | 宿主绑定 |
|---|---|---|---|
| 8081 | WebUI 管理后台 | 0.0.0.0 | 127.0.0.1 |
| 8082 | 主程序内部 API | 0.0.0.0 | 127.0.0.1 |
| 12345 | Live2D 舞台 | 0.0.0.0 | 127.0.0.1 |
| 5500 | 字幕 / 舞台信号 / 音频桥 | 0.0.0.0 | 127.0.0.1 |
| 8888 | 弹幕 Hub | 0.0.0.0 | 127.0.0.1 |

**为什么容器内是 0.0.0.0**：容器内绑 `127.0.0.1` 时，Docker Desktop 的端口转发
会出现「TCP 连得上但一个字节都不回」的现象，宿主机和 OBS 全部拿不到数据。
对外暴露仍然由 `ports: 127.0.0.1:...` 限制在本机，安全性不受影响。

**三种 localhost 不要混淆**：

| 写在哪 | localhost 指谁 |
|---|---|
| 容器内进程（config.json 里的 `127.0.0.1`） | 容器自己 |
| 宿主机脚本 / OBS 浏览器源 | Mac |
| 容器访问宿主机 | `host.docker.internal` |

本项目里字幕/音频桥地址 `http://127.0.0.1:5500` 是**容器内**地址，
OBS 用的 `http://127.0.0.1:12345/...` 是**宿主机**地址，两者靠 compose 的端口映射打通。

---

## 9. 数据与持久化

| 目录 | 内容 | 挂载 |
|---|---|---|
| `config.json` | 全部配置 | bind mount（单文件） |
| `log/` | 运行日志、字幕文件 | bind mount |
| `out/` | 合成的音频 | bind mount |
| `data/` | 数据库、问答库、礼物价格表 | bind mount |
| `song/` | 点歌用的本地歌曲 | bind mount |
| `bgm_cache/` | 网易云 BGM 缓存 | bind mount |
| `Live2D/live2d-model/` | Live2D 模型 | bind mount |

容器重建、`docker compose down`、重新构建镜像都**不会**丢这些。

---

## 10. 许可与合规

- 代码：GPL-3.0（沿用原项目）
- **大肥鱼 Live2D 模型**：作者 @Mao绒绒家园，**非商业使用，禁止盗用与出售**。
  本仓库不随版本库分发，需自行下载（见 `Live2D/live2d-model/大肥鱼/README.md`）
- Fish Audio 音色、背景音乐、点播歌曲各自有授权，需分别确认
- 直播时请遵守平台规则：明确标识 AI 生成内容、开播期间有真人值守

**当前默认只做本地非商业测试。** 未经确认不得直接用于商业直播。

---

## 11. 已知限制

| 项 | 状态 |
|---|---|
| Docker 容器内声卡 | 不可用（Docker Desktop 不暴露 `/dev/snd`），音频走浏览器 |
| 容器内麦克风 / 语音对话 | 不可用（同上）。`talk` 模式请在宿主机直装时使用 |
| Azure TTS | 无 linux-arm64 轮子，容器内不可用 |
| Gemini | 依赖钉死 protobuf<5，与仓库的 protobuf 7.x 冲突，容器内不预装 |
| 千帆（wenxinworkshop） | 走 gitee git 依赖，构建期不稳定，容器内不预装 |
| 容器内按键监听 | 需要 `/dev/input` 与 root，默认关闭 |
