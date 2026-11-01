# 大肥鱼 · macOS Docker 故障排查

按「现象 → 原因 → 处理」组织。每条都对应真实踩过或验证过的场景。

---

## 快速定位

```bash
scripts/macos/status.sh          # 一眼看全：容器、端口、音频桥、配置
scripts/macos/doctor.sh          # 逐项自检，带修复建议
docker compose logs -f           # 实时日志
docker compose logs --tail 200 | grep -i error
```

---

## A. 启动类

### A1. 容器一直 Restarting

```bash
docker compose logs --tail 60
```

**最常见原因**：`config.json` 里某个下拉值不在选项表里，WebUI 构建期抛
`ValueError: Invalid value: xxx`。

我们已加了 `webui_app/safe_select.py` 兜底（把不认识的值临时置空显示，页面照常渲染）。
如果仍报这个错，说明是没被 `ui.select` 覆盖的地方：

```bash
docker compose logs | grep "Invalid value"
python3 -c "import json;c=json.load(open('config.json'));print(c.get('play_audio'))"
```

### A2. 构建失败：`ResolutionImpossible`

依赖冲突。当前 `requirements/docker-arm64.txt` 已验证可解析。
如果你改了依赖：

```bash
docker run --rm --platform linux/arm64 -v "$PWD/requirements/docker-arm64.txt:/tmp/r.txt:ro" \
  python:3.11-slim-bookworm bash -c "pip install --dry-run -r /tmp/r.txt" 2>&1 | tail -20
```

已知冲突：`google-generativeai` 要求 `protobuf<5`，与仓库的 `protobuf 7.36.2` 互斥。

### A3. 构建失败：`Failed to build PyAudio`

PyAudio 在 PyPI 上只有 Windows 轮子，Linux 必须现场编译。
Dockerfile 里已经临时装 `portaudio19-dev gcc` 再 purge。如果你自己改了 Dockerfile
把那行删了就会失败。

### A4. 镜像太大 / 构建太慢

- 首次构建 5~10 分钟正常（要下 python 基础镜像 + 编译 PyAudio）
- 之后改代码只重建最后一层，约 10 秒

---

## B. 网络类

### B1. **端口能连上但一个字节都不回**（重要）

现象：`curl -v http://127.0.0.1:5500/health` 显示
`Connected` → `Request completely sent off` → **`Empty reply from server`**

原因：容器内服务绑的是 `127.0.0.1`。Docker Desktop 的端口转发从容器外连进来时，
请求到了容器网络命名空间，但服务只监听 loopback，于是连接被接受却没有响应。

处理：容器内必须绑 `0.0.0.0`。本项目已经通过 compose 的环境变量设好了：

```yaml
WEBUI_HOST: 0.0.0.0
CAPTION_BIND: 0.0.0.0
BARRAGE_HUB_BIND: 0.0.0.0
```

### B2. 拉不到 pypi / apt

国内网络：Docker Desktop → Settings → Resources → Proxies，填你的代理。

### B3. 容器访问宿主机服务

容器里的 `127.0.0.1` 是容器自己。要访问 Mac 上的服务用 `host.docker.internal`：

```bash
BGM_API=http://host.docker.internal:9000
```

---

## C. 音频类

### C1. 有声音但 OBS 里没有

按顺序查：

1. **OBS 浏览器源有没有勾「通过 OBS 控制音频」**
   没勾 → OBS 拿不到这个源的音频。右键源 → 属性 → 勾上。
2. **OBS 混音器里该来源是否静音**
3. **浏览器端有没有连上**
   ```bash
   curl -s http://127.0.0.1:5500/audio/state | python3 -c "import json,sys;print(json.load(sys.stdin)['client'])"
   ```
   `connected: False` → 舞台页没打开或已刷新。
4. **AudioContext 是不是 suspended**
   在 OBS 里右键浏览器源 → 交互 → 点一下画面。
   Chrome 里调试时同理（浏览器自动播放策略）。

### C2. 浏览器里也没声音（但状态显示在播）

```bash
curl -s http://127.0.0.1:5500/audio/state | python3 -m json.tool
```

- `state: starting` 长时间不变 → 浏览器没去取音频。查 CORS：
  ```bash
  curl -s -X OPTIONS http://127.0.0.1:5500/audio/sync \
    -H "Origin: http://127.0.0.1:12345" \
    -H "Access-Control-Request-Method: POST" -i | head -12
  ```
  应该看到 `204` + `Access-Control-Allow-Origin`。
- `state: error` + `不支持的音频扩展名` → TTS 产出了非音频文件
- `error: 浏览器未连接` → 见 C1 第 3 条

### C3. 声音断断续续 / 有回声

- **回声**：OBS 里同一路音频被采集了两次。检查「音频监听」是不是开着。
- **断断续续**：Docker 内存不足。Settings → Resources → Memory 调到 8GB+。
- **口型和声音不同步**：确认 `PLAY_AUDIO_PLAYER=browser`。

### C4. 容器里为什么不能直接播声音

Docker Desktop 的 Linux 虚拟机不把宿主声卡暴露给容器，`/dev/snd` 不存在。
映射 `/dev/snd` 也不会生效。这是设计限制，不是配置问题。
所以本项目的方案是「容器生成音频 → 浏览器播放」。

---

## D. 弹幕类

### D1. Hub 有客户端但收不到弹幕

```bash
curl -s http://127.0.0.1:8888/health
```

`clients: 0` → 主程序没连上来：
1. 主程序启动了吗？`curl -s http://127.0.0.1:8082/get_sys_info`
2. `config.json` 的 `platform` 是 `dy` 吗？
3. 日志里有 `连接不上弹幕服务` 吗？

### D2. 主程序反复「连接不上弹幕服务」

本项目已实现指数退避重连（1s → 2s → 4s → ... 最长 30s），Hub 恢复后会自动接上。
看到 `弹幕 WebSocket 连接成功` 就说明恢复了。

### D3. Hub 挂了怎么恢复

```bash
docker compose restart dafeiyu
```

只重启 Hub 不重启主程序（断线演练）：

```bash
# 镜像里没有 pkill/ps，用 /proc 找 PID
docker compose exec dafeiyu sh -c 'for d in /proc/[0-9]*; do c=$(tr "\0" " " < $d/cmdline 2>/dev/null); case "$c" in *adapter.hub*) echo ${d#/proc/};; esac; done'
# 重新拉起
docker compose exec -d dafeiyu sh -c 'cd /app && BARRAGE_HUB_BIND=0.0.0.0 python -m integrations.douyin.adapter.hub >> /app/log/barrage-hub.log 2>&1'
```

### D4. dy2 直连拿不到 roomId

日志出现 `无法获取 真 roomId`：

1. 直播间**没在开播** —— 未开播时页面 SSR 里 `roomId` 是 `$undefined`，这是最典型的原因
2. 直播间号写错（`ROOM_ID`）
3. 被抖音风控 —— 换网络/等一会儿

**推荐做法**：用 `PLATFORM=dy` + 本机 Hub。Hub 不依赖抖音网页，稳定得多。

---

## E. 画面类

### E1. 舞台页 404 / 模型不显示

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:12345/Live2D/stage-landscape.html
curl -s -o /dev/null -w "%{http_code}\n" \
  "http://127.0.0.1:12345/Live2D/live2d-model/%E5%A4%A7%E8%82%A5%E9%B1%BC/%E5%A4%A7%E8%82%A5%E9%B1%BC.model3.json"
```

- 舞台页 404 → 主程序没启动（12345 是 main.py 提供的）
- 模型 404 → 模型没放对位置，见 `Live2D/live2d-model/大肥鱼/README.md`

### E2. 模型显示但没背景

背景图在 `Live2D/assets/aquarium-landscape-20261028.png`。
确认它在镜像里：`docker compose exec dafeiyu ls -la /app/Live2D/assets/`

### E3. 中文文件名乱码

镜像已设 `LANG=C.UTF-8`。如果还乱，检查宿主机文件名是否 NFC/NFD 不一致
（macOS 默认 NFD）。可以重命名成 ASCII 目录名再改 `model_name.js`。

### E4. 口型不动

浏览器控制台执行：

```js
window.__stageAudio           // 桥是否就绪
window.__songroomLipSync      // 当前口型值 0~1
window.__songroomMouthApplied // 模型参数写入情况
```

- `__songroomMouthApplied.index = -1` → 模型没有 `ParamMouthOpenY` 参数
- 有值但画面不动 → 该模型对 `ParamMouthOpenY` 响应弱（换模型测试）

---

## F. 大模型 / TTS 类

### F1. 回复一直是 `[mock] ...`

`CHAT_TYPE` 还是 `mock`。改成 `chatgpt` 并填 `OPENAI_API_KEY`。

### F2. 大模型超时

```bash
curl -s -X POST http://127.0.0.1:8082/llm -H 'Content-Type: application/json' \
  -d '{"type":"chatgpt","username":"测试","content":"你好"}' | head -c 300
```

- 返回 `code:-1` → Key / BaseURL / 网络问题
- 返回很慢 → 网关慢，考虑换模型或加 `LLM_STREAM=true`

### F3. TTS 失败

```bash
docker compose logs | grep -i "合成失败"
```

- edge-tts：需要外网访问微软 TTS
- fish_audio：检查 `FISH_AUDIO_KEY` 和 `FISH_AUDIO_REFERENCE_ID`
- 实在不行先用 `TTS_PROVIDER=mock_tts` 确认播放链路是通的

### F4. 字幕不显示

```bash
curl -s "http://127.0.0.1:5500/send_message?content=测试"
curl -s http://127.0.0.1:5500/state
```

有 `text` 但页面不显示 → 舞台页 URL 加了 `?captions=0`，去掉。

---

## G. 性能类

### G1. 容器 CPU 占用高

```bash
docker stats --no-stream dafeiyu
```

正常空闲时 < 5%。持续高的话：
- BGM 在不停换歌（网易云搜索）→ 检查 `bgm_cache/` 是否有文件
- 闲时任务触发太频繁 → `idle_time_task.idle_time_min` 调大

### G2. OBS 掉帧

- OBS 设置 → 高级 → 渲染器选 `Metal`
- 舞台是 1920x1080 固定尺寸，OBS 里源尺寸必须也是 1920x1080
- OBS 设置 → 视频 → 常用 FPS 值 = 30

### G3. 首声延迟高

```bash
docker compose logs | grep -E "等待合成消息|合成成功|待播放音频列表|播放完成"
```

- 「等待合成消息」→「合成成功」= TTS 耗时
- 「合成成功」→「待播放音频列表」= 队列耗时
- 「待播放音频列表」→ 实际出声 = 浏览器取音频耗时

---

## H. 数据与重置

### H1. 配置改坏了

```bash
cp config.json.bak config.json
docker compose restart
```

### H2. 彻底重置（会丢配置，不丢日志/音频）

```bash
docker compose down
cp config.json.bak config.json
docker compose up -d --build
```

### H3. 磁盘被音频撑满

```bash
scripts/macos/doctor.sh --clean   # 保留最近 200 个
du -sh out/ log/ bgm_cache/
```

### H4. 完全干净重建

```bash
docker compose down --rmi local -v
docker builder prune -f
scripts/macos/install.sh
```

**注意**：本项目用 bind mount，`-v` 不影响数据目录。

---

## I. 日志位置

| 日志 | 位置 |
|---|---|
| 容器 stdout | `docker compose logs` |
| 舞台服务 | `log/caption-service.log` |
| 弹幕 Hub | `log/barrage-hub.log` |
| 主程序 | `log/log-YYYYMMDDHH.txt` |
| 弹幕记录 | `log/comment-YYYYMMDDHH.txt` |
| 字幕 | `log/字幕.txt` |

---

## J. 收集诊断信息（提 issue 用）

```bash
{
  echo "=== 环境 ==="; uname -m; sw_vers; docker --version; docker compose version
  echo "=== 容器 ==="; docker compose ps
  echo "=== 端口 ==="; for p in 8081 8082 12345 5500 8888; do echo -n "$p "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 3 http://127.0.0.1:$p/; done
  echo "=== 音频桥 ==="; curl -s http://127.0.0.1:5500/audio/state
  echo "=== 日志 ==="; docker compose logs --tail 300
} > dafeiyu-diagnose.txt 2>&1
```

分享前请自己检查一遍输出里有没有明文 Key。
