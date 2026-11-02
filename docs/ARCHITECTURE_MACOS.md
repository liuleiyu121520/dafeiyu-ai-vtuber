# 大肥鱼 AI 虚拟主播 · macOS / Docker 架构说明

> 本文记录**实际源码**的模块依赖、端口归属、Windows 专有依赖，以及本次 macOS + Docker
> 改造的最小改动清单。所有结论都来自阅读 `main.py` / `webui.py` / `utils/` / `tools/`
> 的真实实现，不是推测。

---

## 1. 进程模型（原项目）

```
启动WebUI管理后台.bat ──> python webui.py            # NiceGUI，端口 8081
                              │
                              └─(「一键运行」/ auto_run)─> subprocess: python main.py
                                                              │
                                                              ├─ 线程: Live2D 静态服务  :12345
                                                              ├─ 线程: FastAPI 内部 API :8082
                                                              ├─ 线程: 平台弹幕监听（按 platform 分发）
                                                              ├─ 线程: Audio 合成队列 / 播放队列 / 文案
                                                              ├─ 线程: BGM（网易云）
                                                              └─ 线程: 点歌通道 / 闲时任务 / 定时任务

启动字幕和口型.bat ──> python tools/caption_printer.py   # 端口 5500（字幕 + 舞台信号）
                        python tools/songroom_lipsync.py # 只读 Windows CoreAudio 峰值
```

**关键事实**：WebUI 的「一键运行」`webui_app/processes.py::start_programs()` 会
`subprocess.Popen([sys.executable, "main.py"])`。所以 **Compose 不能再起第二个 main.py**，
否则会出现两套播放队列、两套弹幕监听、两次 TTS。

## 2. 端口归属（以源码为准）

| 端口 | 归属 | 代码位置 | 绑定 |
|---|---|---|---|
| 8081 | WebUI 管理后台 | `webui.py` → `ui.run(host=webui.ip, port=webui.port)` | config `webui.ip` |
| 8082 | 主程序内部 HTTP API | `main.py::http_api_thread` → `uvicorn.run(app, host="0.0.0.0", port=api_port)` | **硬编码 0.0.0.0** |
| 12345 | Live2D 静态资源 | `main.py::web_server_thread` → `utils/live2d_server.py` | `("", port)` 全接口 |
| 5500 | 字幕 / 舞台信号 | `tools/caption_printer.py` → `ThreadingHTTPServer(('127.0.0.1', 5500))` | **硬编码 127.0.0.1** |
| 8888 | 抖音弹幕监听（Windows exe） | `utils/platforms/dy.py` → `ws://127.0.0.1:8888` | 外部程序 |
| 5001 | dy2 内置抓取（可选） | `utils/platforms/dy2.py` | 直连抖音 wss |
| 5600 | 外部 audio_player（可选） | `utils/audio_handle/audio_player.py` | 外部程序 |

## 3. 启动链路逐问逐答

1. **WebUI 如何启动？** `python webui.py`，NiceGUI `ui.run()` 起 8081。
2. **主程序如何启动？** WebUI 的子进程（`webui_app/processes.py`）或手动 `python main.py`。
3. **一键运行会不会创建主程序进程？** 会，`start_programs()` 用当前解释器 Popen `main.py`。
4. **Live2D 页面由谁提供？** `main.py` 的 `web_server_thread` 用
   `utils/live2d_server.py`（`SimpleHTTPRequestHandler`，工作目录 = 项目根），
   所以 `http://host:12345/Live2D/stage-landscape.html`。
5. **字幕服务由谁提供？** 独立进程 `tools/caption_printer.py`（5500），
   主程序通过 `config.web_captions_printer.api_ip_port` 调用它的 `/send_message`。
   同一服务还提供 `/thinking`、`/effect`、`/state`（思考气泡、鼓掌特效）。
6. **口型同步依赖什么数据？** 浏览器端 `Live2D/js/songroom-lipsync.js` 轮询
   `out/songroom/mouth.json` 的 `peak` 字段，写入 `window.__songroomLipSync`；
   `bundle.songroom-lipsync.js` 在每帧把它 `setParameterValueByIndex(ParamMouthOpenY)`。
   而这个 `mouth.json` 由 `tools/songroom_lipsync.py` 用 **Windows CoreAudio
   (`IAudioMeterInformation`)** 读「核心进程 8082 的播放会话峰值」生成 —— **Windows 专有**。
7. **TTS 在哪里生成？** `utils/audio_handle/my_tts.py`，由 `Audio.audio_synthesis_handle`
   按 `config.audio_synthesis_type` 分发（edge-tts / fish_audio / vits / ...）。
8. **音频在哪里播放？** `Audio.only_play_audio()` 循环，默认 `play_audio.player == "pygame"`，
   用 `utils/audio_handle/playback_stream.py::PlaybackStream`（pygame `mixer.Channel`）
   播到**本机声卡**。
9. **BGM 与 TTS 如何避免重叠？** `BGMPlayer` 独占 channel 2，靠 `Audio.speech_active()`
   做 ducking（压低到 `bgm.duck_ratio`），点歌独占 channel 3。四路 channel 互不抢占。
10. **弹幕监听器如何把事件送到主程序？** 各平台模块解析后直接调
    `my_handle.process_data(data, "comment"|"gift"|"entrance"|"follow")`，
    进入 `My_handle` 的定时聚合缓冲 → `process_last_data` → `comment_handle` 等。
11. **原项目是否已有模拟弹幕/手动消息？** **有**。`main.py` 暴露 `POST /send`
    （`type: comment/gift/entrance/reread/tuning`），这是官方的内部注入入口。
12. **Windows 专用组件：**
    - `tools/songroom_lipsync.py` + `utils/audio_handle/core_audio_meter.py`（CoreAudio / iphlpapi）
    - `tools/songroom_overlay.py`（`msvcrt.locking`）
    - `tools/DouyinBarrageGrab/WssBarrageServer.exe`（.NET 抓包程序）
    - `Scripts/*.bat`、`启动*.bat`、`2-1.强制更新...bat`
    - `keyboard`（Linux 需要 /dev/input 与 root，本机不启用）
    - `pyautogui` / `PyGetWindow` / `pyvirtualcam` / `azure-cognitiveservices-speech`（无 linux-arm64 轮子）
13. **哪些组件无法在 Linux ARM64 直接运行？**
    - CoreAudio 峰值读取（口型数据源）→ **必须替换**
    - pygame 直接播到宿主声卡 → **Docker Desktop 不暴露 /dev/snd** → **必须替换**
    - WssBarrageServer.exe → 换成本机 WS Hub / 项目内置 dy2
    - msvcrt 文件锁 → 换成 `fcntl`

## 4. 本次改造的分层

```
                    ┌──────────────────────────────┐
   抖音直播间 ──────>│ 弹幕源（四选一）              │
                    │  A. collector→/inject 推荐   │
                    │  B. Hub:8888 + mock 注入     │
                    │  C. 外部采集器 → /inject     │
                    │  D. dy2 直连（绕过 Hub）     │
                    └──────────────┬───────────────┘
                                   │  统一 data_json
                    ┌──────────────▼───────────────┐
                    │ My_handle.process_data()      │  ← 原封不动
                    │  → comment/gift/entrance/...  │
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
                    │ LLM（chatgpt/zhipu/.../mock） │  ← 只新增 mock provider
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
                    │ TTS（edge-tts/fish/mock_tts） │  ← 只新增 mock_tts
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
                    │ Audio.only_play_audio()       │  ← 原逻辑不变
                    │  → PlaybackStream(ch)         │  ← 抽象点
                    │     ├ pygame（Windows/macOS 本地）
                    │     └ BrowserBridge（Docker） │  ← 新增
                    └──────────────┬───────────────┘
                                   │ HTTP（5500）
                    ┌──────────────▼───────────────┐
                    │ 舞台服务 :5500                │
                    │  /send_message /thinking      │  原字幕接口保留
                    │  /effect /state /health       │
                    │  + /audio/* 音频桥（新增）     │
                    └──────────────┬───────────────┘
                                   │ 浏览器（OBS 浏览器源）
                    ┌──────────────▼───────────────┐
                    │ stage-*.html                  │
                    │  Web Audio 播放 + AnalyserNode│  ← 真实振幅
                    │  → __songroomLipSync → 口型    │
                    │  字幕（原 stage-captions.js）  │
                    └──────────────────────────────┘
```

> **A 与 D 的区别**：A（`integrations/douyin/collector.py`）把弹幕喂给 Hub，
> 走的仍是 `utils/platforms/dy.py` 这条与 Windows 完全一致的业务入口，
> 礼物价格表、动态配置切换等既有行为全都在；D 要把 `PLATFORM` 改成 `dy2`，
> 业务逻辑在 `dy2.py` 里另有一份。收真实弹幕优先 A，A 的 wss 握手被风控时
> 才退回 D，或用 `.env` 的 `DY_WSS_URL` 覆盖抓包参数。
>
> 本节早期版本写「dy2 内置直连（推荐）」，与 `DOCKER_TROUBLESHOOTING.md` D4
> 「推荐 dy + Hub」互相矛盾——那是 collector.py 出现之前留下的说法，现已统一：
> 真实弹幕走 A，联调走 B。采集器用法见 `DOCKER_SETUP_MACOS.md` §3.5。

### 为什么是「替换 PlaybackStream」而不是「重写播放逻辑」

`PlaybackStream` 是整个项目**唯一**的本地音频出口（`Audio.mixer_normal` /
`mixer_copywriting` / `mixer_song` / `BGMPlayer` 四路全部是它）。它的对外接口就是
pygame `mixer.music` 那套（`load/play/stop/pause/unpause/get_busy/fadeout/set_volume`）。
因此新增一个**同接口**的 `BrowserPlaybackStream`，就能让
TTS、点歌、BGM、ducking、播放队列、字幕时序**全部零改动**地跑到浏览器里。

### 口型为什么是「真的」

浏览器用 `AudioContext.decodeAudioData` 解码**真正正在播放的那段 PCM**，
用 `AnalyserNode.getFloatTimeDomainData` 取 RMS，映射到
`window.__songroomLipSync`（与原来 `mouth.json` 的语义完全一致：0~1 的开口度）。
`bundle.songroom-lipsync.js` 每帧 `setParameterValueByIndex(ParamMouthOpenY, level)`。
**没有任何随机数或定时器参与嘴型**。

## 5. 改动清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `Dockerfile` | 重写 | `python:3.11-slim` + ffmpeg + ARM64 依赖 |
| `compose.yaml` | 新增 | 单服务（webui 自己拉 main.py），健康检查、优雅停止 |
| `requirements/docker-arm64.txt` | 新增 | 剔除无 ARM64 轮子/Windows 专有包 |
| `docker/entrypoint.sh` | 新增 | 启舞台服务 → 启弹幕 Hub → 前台跑 webui |
| `docker/healthcheck.py` | 新增 | 4 个端口健康检查 |
| `docker/apply_env_config.py` | 新增 | 环境变量 → config.json（不打印密钥） |
| `utils/audio_handle/browser_bridge.py` | 新增 | `BrowserPlaybackStream` + 与舞台服务的客户端协议 |
| `utils/audio_handle/playback_stream.py` | 改 | 加 `make_playback_stream()` 工厂 + `now_ms()/tick()` |
| `utils/audio.py` | 改 | 用工厂创建 4 路流；播放等待循环改用 `now_ms/tick` |
| `tools/caption_printer.py` | 改 | 新增 `/audio/*` 音频桥路由（原字幕路由不变） |
| `Live2D/js/stage-audio.js` | 新增 | Web Audio 播放 + AnalyserNode 口型 + 状态回报 |
| `Live2D/js/songroom-lipsync.js` | 改 | 检测到 stage-audio 生效时让位 |
| `Live2D/stage-landscape.html` / `stage-portrait.html` | 改 | 引入 stage-audio.js |
| `utils/gpt_model/mock.py` | 新增 | 离线 Mock LLM |
| `utils/audio_handle/my_tts.py` | 改 | 新增 `mock_tts`（离线正弦音，供测试） |
| `integrations/douyin/adapter/` | 新增 | 本机 WS Hub（8888，兼容原协议）+ HTTP 注入 |
| `scripts/macos/*.sh` | 新增 | install/start/stop/status/doctor |
| `tests/unit/test_*` | 新增 | 音频桥、事件映射、mock provider 的单元测试 |

## 6. 不变的部分（明确保留）

- `utils/my_handle.py` 的全部业务（弹幕过滤/去重/队列/礼物/进场/点歌/闲时任务）
- `utils/audio_handle/my_tts.py` 的全部 Provider
- `utils/gpt_model/*` 的全部 Provider
- `Live2D/` 的渲染管线、海底场景、`stage-captions.js` 字幕打字机
- `webui_app/` 全部页面与 `config.json` 结构
- Windows 下的 `.bat`、`WssBarrageServer.exe`、CoreAudio 口型链路（`platform` 判断后走原路）
