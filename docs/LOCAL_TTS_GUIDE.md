# 大肥鱼 · 本地 TTS 接入说明

> 回答「TTS 用本地的行不行？要不要给个 HTTP 地址？」——**行，而且是给 HTTP 地址**。
> 但地址的格式取决于你用什么 TTS 服务端。

---

## 一句话结论

**本项目不直接加载本地模型文件，而是调用一个「已经在跑」的 TTS 服务端的 HTTP 接口。**

所以你需要：
1. 先把本地 TTS 服务端跑起来（它自己监听一个端口）
2. 把那个**地址**填进 config.json / .env
3. 选对 `audio_synthesis_type`

---

## 支持的本地 TTS 方案（按推荐顺序）

| 方案 | 配置值 | 你要提供的地址 | 服务端项目 |
|---|---|---|---|
| **GPT-SoVITS** | `gpt_sovits` | `http://host:9880` | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 的 `api_v2.py` |
| **VITS-Simple-API** | `vits` | `http://host:23456` | [Artrajz/vits-simple-api](https://github.com/Artrajz/vits-simple-api) |
| **CosyVoice** | `cosyvoice` | `http://host:50000` | [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) |
| **Index-TTS** | `index_tts` | `http://host:11996` | [index-tts](https://github.com/index-tts/index-tts) |
| **MeloTTS** | `melotts` | `http://host:13100` | [myshell-ai/MeloTTS](https://github.com/myshell-ai/MeloTTS) |
| **任意 OpenAI 兼容 TTS** | `openai_tts` | `http://host:xxxx/v1` | 只要实现了 `POST /v1/audio/speech` |
| **任意 Gradio 界面** | `gradio_tts` | `http://host:7860` | 大多数 TTS 项目默认的 WebUI |

> **关键**：如果 TTS 跑在 **Mac 宿主机**上，容器里必须用 `host.docker.internal`，
> 不能用 `127.0.0.1`（那是容器自己）。

---

## 三种最常见的接法

### 接法 A：GPT-SoVITS（最像大肥鱼音色的本地方案）

1. 启动 GPT-SoVITS 的 API 服务：

```bash
# 在 GPT-SoVITS 目录下
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

2. 准备一段 3~10 秒的参考音频（就是你想要的音色），以及它的文本。

3. 改 `.env`：

```bash
TTS_PROVIDER=gpt_sovits
```

4. 改 `config.json` 的 `gpt_sovits` 段（WebUI → 语音合成 → GPT-SoVITS 也能改）：

```json
"gpt_sovits": {
  "type": "api_0322",
  "api_ip_port": "http://host.docker.internal:9880",
  "ref_audio_path": "/app/song/ref.wav",
  "prompt_text": "参考音频里说的那句话",
  "prompt_language": "中文",
  "language": "中文",
  "api_0322": {
    "text_lang": "中文",
    "ref_audio_path": "/app/song/ref.wav",
    "prompt_text": "参考音频里说的那句话",
    "prompt_lang": "中文",
    "top_k": 5,
    "top_p": 1,
    "temperature": 1,
    "text_split_method": "按标点符号切",
    "batch_size": 1,
    "speed_factor": 1.0,
    "split_bucket": true,
    "return_fragment": false,
    "fragment_interval": 0.3
  }
}
```

> `ref_audio_path` 是**容器内**路径。把参考音频放到项目的 `song/` 目录，
> 它会以 `/app/song/xxx.wav` 出现在容器里。

### 接法 B：任意 OpenAI 兼容 TTS（最通用）

只要你的本地服务实现了 `POST /v1/audio/speech`（OpenAI 格式），就能直接接：

```bash
# .env
TTS_PROVIDER=openai_tts
```

```json
// config.json
"openai_tts": {
  "type": "api",
  "api_ip_port": "http://host.docker.internal:8880/v1",
  "model": "tts-1",
  "voice": "zh-CN-XiaoxiaoNeural",
  "api_key": "任意值（本地服务通常不校验）"
}
```

请求体是标准的：
```json
{"model": "tts-1", "voice": "...", "input": "要合成的文本"}
```

响应要返回**音频二进制**（mp3/wav 都行）。

> 这类服务端很多：**Kokoro-FastAPI**、**openedai-speech**、**Chatterbox**、
> 以及 LM Studio / Ollama 生态里的 TTS 插件等。

### 接法 C：Gradio 界面（最省事，但有坑）

很多 TTS 项目只提供 Gradio WebUI。填地址 + 函数索引就能调：

```bash
# .env
TTS_PROVIDER=gradio_tts
```

```json
// config.json
"gradio_tts": {
  "request_parameters": {
    "url": "http://host.docker.internal:7860",
    "fn_index": 0,
    "data_analysis": 1,
    "参数1": "值1"
  }
}
```

**坑**：`fn_index` 和参数顺序随项目版本变化，需要你自己打开 Gradio 的
`/config` 接口确认。稳定性不如 A/B 两种，**正式直播不建议用**。

---

## 地址怎么填：三种 localhost 别搞混

| TTS 服务端跑在哪 | config.json 里填什么 |
|---|---|
| **Mac 宿主机**（最常见） | `http://host.docker.internal:端口` |
| **另一个 Docker 容器** | `http://容器名:端口`（同一 compose 网络） |
| **容器内部** | `http://127.0.0.1:端口` |

**验证地址通不通**（在容器里执行，不是宿主机）：

```bash
docker compose exec dafeiyu python -c "
import requests
try:
    r = requests.get('http://host.docker.internal:9880/', timeout=5)
    print('OK', r.status_code)
except Exception as e:
    print('FAIL', type(e).__name__, e)
"
```

---

## 改完怎么验证

1. 改配置后重启容器：
   ```bash
   docker compose up -d --force-recreate
   ```

2. 确认配置生效：
   ```bash
   python3 -c "import json;c=json.load(open('config.json'));print(c['audio_synthesis_type'])"
   ```

3. 用 WebUI 试听（最快）：
   - 打开 http://127.0.0.1:8081
   - 语音合成 → 填一句「待合成音频内容」→ 点「试听」
   - 这一步不经过直播链路，只验证 TTS 本身通不通

4. 走完整链路：
   ```bash
   ./scripts/macos/mock-chat.sh --text "你好，大肥鱼"
   docker compose logs -f | grep -E "合成成功|合成失败"
   ```

---

## 常见错误

| 报错 | 原因 | 处理 |
|---|---|---|
| `合成失败，请排查服务端是否启动` | 地址填的是 `127.0.0.1` | 容器里要用 `host.docker.internal` |
| `Connection refused` | TTS 服务端没启动，或只监听了 `127.0.0.1` | 服务端要监听 `0.0.0.0`，或确认端口 |
| `合成成功` 但没声音 | `play_audio.player` 不是 `browser` | 改成 `browser`（Docker 必须） |
| Gradio 报 `fn_index` 错 | 项目版本变了 | 打开 `http://host:7860/config` 查真实的 `fn_index` |
| 音色不像 | 参考音频太短/太长/有噪音 | 用 3~10 秒干净人声，文本要和音频完全对应 |

---

## 本项目当前的默认值

```
TTS_PROVIDER=edge-tts      # 免费、开箱即用、需要外网
```

edge-tts **不是本地模型**，是调用微软的在线服务。它的好处是零配置、
中文自然、不需要显卡。

想完全离线 → 按上面 A/B 接本地 TTS。
想先用起来 → 保持 edge-tts。
