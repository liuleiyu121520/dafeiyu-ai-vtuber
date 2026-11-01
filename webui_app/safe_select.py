"""下拉框取值兜底。

问题
----
NiceGUI 的 ui.select(value=...) 在 value 不在 options 里时会直接抛
ValueError: Invalid value: xxx。WebUI 是在**构建期**一次性把所有页面建出来的，
所以任意一个下拉框取到「配置里有、但选项表里没有」的值，整个管理后台就打不开
（不是那一页不可用，是全站 500）。

这个风险在改造后会变大：
  * 新增了 browser（播放器）、mock（大模型）、mock_tts（语音合成）三个取值
  * 用户可能从旧版本带过来已废弃的取值
  * 声卡设备号在无声卡的机器（Docker / CI）上必然对不上

做法
----
在 webui.py 里安装一次 ui.select 的包装：
  * value 在 options 里 -> 原样调用，行为完全不变
  * value 不在 options 里 -> 记一条 warning，把 value 置空（下拉显示空白），页面照常渲染
  * 用户在下拉里重新选一个再保存，配置就修正了

只包装 ui.select，不动其它元素，也不改任何业务逻辑。
"""
import functools

from utils.my_log import logger

_installed = False
_reported = set()


def _option_keys(options):
    """取 options 的所有合法 key。支持 dict / list / tuple / 可调用对象。"""
    if options is None:
        return None
    if callable(options):
        return None  # 动态选项（函数）无法预判，交给 NiceGUI
    if isinstance(options, dict):
        return set(options.keys())
    if isinstance(options, (list, tuple, set)):
        return set(options)
    return None


def install(ui_module):
    """包装 ui_module.select。重复调用无副作用。"""
    global _installed
    if _installed:
        return
    _installed = True

    original = ui_module.select

    @functools.wraps(original)
    def select(*args, **kwargs):
        value = kwargs.get("value")
        keys = _option_keys(kwargs.get("options"))
        if value is not None and value != "" and keys is not None and value not in keys:
            label = kwargs.get("label") or "(未命名下拉)"
            marker = (str(label), str(value))
            if marker not in _reported:
                _reported.add(marker)
                logger.warning(
                    f"下拉「{label}」的配置值 {value!r} 不在选项表里，已临时置空显示。"
                    f"请在该下拉里重新选一个并保存，配置即被修正。"
                )
            kwargs["value"] = None
        return original(*args, **kwargs)

    ui_module.select = select


def reset():
    """给单元测试用：清空「已报告过」的记录。"""
    _reported.clear()