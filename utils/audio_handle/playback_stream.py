"""一条独立的播放通道。

以前 `Audio.mixer_normal` 和 `Audio.mixer_copywriting` 都被赋成 `pygame.mixer` 模块
本身，而 `pygame.mixer.music` 是**全局唯一**的一条流：所谓「弹幕音频」和「文案音频」
两条通道其实共用一个播放器。后果是暂停文案会把正在播的弹幕回复一起暂停、
`music.get_busy()` 的忙等会互相牵制、两路音量/淡出无法分别控制。

这里给每条流各分一个独立的播放器实例。接口名保持成 `.music.load/play/...`，
和 `pygame.mixer` 原来那套一样，调用点不用改。

两种实现（`make_playback_stream` 工厂按配置选）：

* `PlaybackStream`        —— pygame `mixer.Channel`，本机声卡（Windows / macOS 直装）
* `BrowserPlaybackStream` —— 浏览器 Web Audio，Docker / OBS 浏览器源

两者对外接口完全一致，并额外提供 `now_ms()` / `tick(ms)`，
把 `pygame.time.get_ticks()` 和 `pygame.time.Clock().tick()` 也一起抽象掉，
这样播放等待循环里不会残留 pygame 调用。
"""

import threading
import time

from utils.my_log import logger


class MusicFacade:
    """让调用方继续写 stream.music.load(...) / stream.music.play()。"""

    def __init__(self, stream):
        self._stream = stream

    def load(self, path):
        self._stream.load(path)

    def play(self):
        self._stream.play()

    def stop(self):
        self._stream.stop()

    def pause(self):
        self._stream.pause()

    def unpause(self):
        self._stream.unpause()

    def get_busy(self):
        return self._stream.get_busy()

    def fadeout(self, ms):
        self._stream.fadeout(ms)

    def set_volume(self, value):
        self._stream.set_volume(value)


class BasePlaybackStream:
    """两种实现共用的外壳：`.music` 门面 + 单调时钟。"""

    def __init__(self):
        self.music = MusicFacade(self)
        self._path = None
        self._started_at = 0.0

    # --- 与 pygame.time 等价的工具，播放循环用这两个 ---

    @staticmethod
    def now_ms():
        """等价于 pygame.time.get_ticks()：进程启动以来的毫秒数（单调）。"""
        return int(time.monotonic() * 1000)

    def tick(self, fps=10):
        """等价于 pygame.time.Clock().tick(fps)：睡到下一帧。"""
        time.sleep(max(0.0, 1.0 / float(fps or 10)))

    # --- 子类实现 ---

    def init(self):
        raise NotImplementedError

    def quit(self):
        raise NotImplementedError

    def load(self, path):
        self._path = path

    def play(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def pause(self):
        raise NotImplementedError

    def unpause(self):
        raise NotImplementedError

    def get_busy(self):
        raise NotImplementedError

    def fadeout(self, ms):
        raise NotImplementedError

    def set_volume(self, value):
        raise NotImplementedError


class PlaybackStream(BasePlaybackStream):
    """占用一个固定 pygame channel 的播放流（本机声卡）。

    Args:
        channel_index (int): 这条流独占的 pygame channel 编号
    """

    def __init__(self, channel_index):
        super().__init__()
        self._channel_index = channel_index
        self._channel = None

    def init(self):
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init()
        if pygame.mixer.get_init() and self._channel is None:
            self._channel = pygame.mixer.Channel(self._channel_index)

    def quit(self):
        # 只停自己这条通道。不能调 pygame.mixer.quit()——那是把整个音频设备关掉，
        # 另一条流会跟着哑掉（以前两个对象是同一个模块时就是这个行为，属于 bug）
        self.stop()
        self._path = None

    def play(self):
        if self._path is None:
            logger.error("播放前没有 load 音频路径")
            return

        self.init()
        if self._channel is None:
            logger.error("pygame mixer 未就绪，无法播放 %s" % self._path)
            return

        self._channel.play(self._load_sound())
        self._started_at = time.monotonic()

    def stop(self):
        if self._channel is not None:
            self._channel.stop()

    def pause(self):
        if self._channel is not None:
            self._channel.pause()

    def unpause(self):
        if self._channel is not None:
            self._channel.unpause()

    def get_busy(self):
        if self._channel is None:
            return False
        return self._channel.get_busy()

    def fadeout(self, ms):
        if self._channel is None:
            return
        sound = self._channel.get_sound()
        if sound is None:
            self.stop()
            return
        sound.fadeout(ms)

    def set_volume(self, value):
        """只改本通道音量，0.0~1.0。未初始化时静默忽略。"""
        if self._channel is None:
            return
        self._channel.set_volume(max(0.0, min(1.0, float(value))))

    def _load_sound(self):
        import pygame

        try:
            return pygame.mixer.Sound(self._path)
        except FileNotFoundError:
            raise
        except Exception:
            # m4a/aac 这类 SDL_mixer 的 Sound 支持面不确定，退一步用 pydub 转成 wav
            # 再读（旧代码走 music 流时这些格式是能放的，不能因为换 channel 就倒退）
            logger.debug("Sound 直接读取失败，转 wav 重试：%s" % self._path)
            return pygame.mixer.Sound(self._to_wav())

    def _to_wav(self) -> str:
        import os

        from pydub import AudioSegment

        wav_path = os.path.splitext(self._path)[0] + "_stream_fallback.wav"
        AudioSegment.from_file(self._path).export(wav_path, format="wav")
        return wav_path


class BrowserPlaybackStream(BasePlaybackStream):
    """把音频交给浏览器（OBS 浏览器源）播放的通道。

    接口与 `PlaybackStream` 完全一致，`get_busy()` 的语义也一致：
    音频真的在响时为 True，播完/被停/浏览器没接上时为 False。
    播放循环因此不需要任何改动。

    Args:
        channel_index (int): 通道号，语义同 pygame 版
        base_url (str): 舞台服务地址，默认读 DAFEIYU_STAGE_URL
    """

    def __init__(self, channel_index, base_url=None):
        super().__init__()
        self._channel_index = channel_index
        self._volume = 1.0
        self._bridge = _get_bridge(base_url)
        # 记录"已经在播"的状态，避免 play() 被重复调用时反复重发
        self._playing = False
        self._last_error = ""

    def init(self):
        """浏览器桥没有需要初始化的音频设备，但要确保同步线程在跑。"""
        self._bridge.start()

    def quit(self):
        self.stop()
        self._path = None

    def load(self, path):
        self._path = path

    def play(self):
        if self._path is None:
            logger.error("播放前没有 load 音频路径")
            return
        self.init()
        try:
            self._bridge.play(
                self._channel_index,
                self._path,
                volume=self._volume,
                kind=self._kind(),
            )
            self._playing = True
            self._started_at = time.monotonic()
            self._last_error = ""
        except FileNotFoundError:
            raise
        except Exception as error:  # noqa: BLE001
            self._playing = False
            message = f"{type(error).__name__}: {error}"[:200]
            if message != self._last_error:
                logger.error(f"[音频桥] 播放失败 channel={self._channel_index}: {message}")
                self._last_error = message

    def stop(self):
        if not self._playing:
            # 即使本地认为没在播，也发一次 stop：可能上一次的 ended 还没回收
            pass
        self._playing = False
        try:
            self._bridge.stop(self._channel_index)
        except Exception:  # noqa: BLE001
            pass

    def pause(self):
        try:
            self._bridge.pause(self._channel_index)
        except Exception:  # noqa: BLE001
            pass

    def unpause(self):
        try:
            self._bridge.unpause(self._channel_index)
        except Exception:  # noqa: BLE001
            pass

    def get_busy(self):
        if not self._playing:
            return False
        try:
            busy = self._bridge.is_busy(self._channel_index)
        except Exception:  # noqa: BLE001
            busy = False
        if not busy:
            self._playing = False
        return busy

    def fadeout(self, ms):
        self._playing = False
        try:
            self._bridge.fadeout(self._channel_index, ms)
        except Exception:  # noqa: BLE001
            pass

    def set_volume(self, value):
        self._volume = max(0.0, min(1.0, float(value)))
        try:
            self._bridge.set_volume(self._channel_index, self._volume)
        except Exception:  # noqa: BLE001
            pass

    def _kind(self):
        return {
            0: "comment",
            1: "copywriting",
            2: "bgm",
            3: "song",
        }.get(self._channel_index, "unknown")


# ---------------------------------------------------------------- 工厂

_BRIDGE_LOCK = threading.Lock()


def _get_bridge(base_url=None):
    from utils.audio_handle.browser_bridge import BrowserAudioBridge

    return BrowserAudioBridge.instance(base_url)


def make_playback_stream(channel_index, config, base_url=None):
    """按配置选播放实现。

    `play_audio.player` 取值：
      * `pygame`          —— 本机声卡（默认，Windows/macOS 直装）
      * `browser`         —— 浏览器 Web Audio（Docker / OBS 浏览器源）
      * `browser_bridge`  —— 同上（兼容别名）

    其它值（`audio_player` / `audio_player_v2`）由 Audio 里的旧分支处理，
    不会走到这里。

    Returns:
        BasePlaybackStream: 具有统一接口的播放流
    """
    from utils.audio_handle.browser_bridge import bridge_enabled

    if bridge_enabled(config):
        return BrowserPlaybackStream(channel_index, base_url)
    return PlaybackStream(channel_index)
