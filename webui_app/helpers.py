"""页面共用的小工具与选项常量。"""


def textarea_data_change(data):
    """把字符串列表转成多行文本框内容。"""
    if data is None:
        return ""
    return "".join(f"{item}\n" for item in data)


def input_data_change(data):
    """把字符串列表转成单行输入框内容（逗号分隔或单个值）。"""
    if data is None:
        return ""
    if isinstance(data, list):
        return ", ".join(str(item) for item in data)
    return str(data)


def common_textarea_handle(content):
    """把多行或单行逗号文本框内容转成去空行的字符串列表。"""
    if content is None:
        return []
    lines = [token.strip() for token in str(content).split("\n") if token.strip()]
    out = []
    for line in lines:
        if ',' in line and not line.startswith('{') and not line.startswith('['):
            out.extend([part.strip() for part in line.split(',') if part.strip()])
        else:
            out.append(line)
    return out


def select_value_or_none(value, options):
    """下拉框的初始值只有在当前选项里才有效，否则给 None。

    选项常常是运行时枚举出来的（窗口标题、摄像头索引、配置模板文件），
    配置里存的值完全可能已经不存在了：nicegui 1 会默默显示一个不存在的选项，
    nicegui 3 直接抛 ValueError。给 None 才是「没有选中」的正确表达。
    """
    keys = options.keys() if isinstance(options, dict) else options
    return value if value in keys else None


AUDIO_SYNTHESIS_TYPE_OPTIONS = {
    'none': '不启用',
    'edge-tts': 'Edge-TTS',
    'vits': 'VITS',
    'bert_vits2': 'bert_vits2',
    'elevenlabs': 'elevenlabs',
    'fish_audio': 'Fish Audio',
    'openai_tts': 'OpenAI TTS',
    'gradio_tts': 'Gradio',
    'gpt_sovits': 'GPT_SoVITS',
    'azure_tts': 'azure_tts',
    'cosyvoice': 'CosyVoice',
    'f5_tts': 'F5-TTS',
    'multitts': 'MultiTTS',
    'melotts': 'MeloTTS',
    'index_tts': 'Index-TTS',
    # 本地 MLX Audio TTS：Apple Silicon 上用 mlx-audio 在本机跑 Kokoro / Qwen3-TTS，
    # 不用显卡、不用联网、不用 API Key（服务见 pre-study/mlx-audio-tts-service）。
    'custom_tts': 'MLX Audio（本地 TTS）',
    # 离线 Mock TTS：按字数节奏生成的合成音，用来验证播放队列/口型/字幕时序。
    # 日志会明确标注「不是真实配音」。
    'mock_tts': 'Mock TTS（离线测试）',
}
