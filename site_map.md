# Site Map

## 概览

- 待补充项目整体结构、核心页面、核心模块与主要业务流。

## 待确认更新

### [2026-11-02 22:30] feat: 重构 WebUI 现代控制台视觉、接入本地 MLX-TTS 与原生抖音弹幕采集器

- 新增 integrations/douyin/collector.py：macOS 纯 Python 抖音弹幕采集器，与 Hub 8888 端口对接
- 新增 utils/audio_handle/my_tts.py::custom_tts_api：本地 MLX Audio 语音合成后端
- 更新 webui.py 与 webui_app/：全面升级为 Modern Studio Console 现代控制台视觉与自托管字体体系
- 更新 compose.yaml：新增 douyin-collector 独立服务，支持开播按需启动
