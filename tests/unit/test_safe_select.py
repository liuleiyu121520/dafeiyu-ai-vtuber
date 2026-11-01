"""下拉框取值兜底的单元测试。

这个兜底很重要：WebUI 是构建期一次性把所有页面建出来的，
任意一个 ui.select 收到不在 options 里的值，整个后台就打不开。
"""
import pytest

from webui_app import safe_select


class FakeUi:
    """记录调用的假 ui 模块。select 是一个可调用对象，calls 挂在 FakeUi 上。"""

    def __init__(self):
        self.calls = []
        self.select = self._select

    def _select(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": dict(kwargs)})
        return "element"


@pytest.fixture(autouse=True)
def reset_module_state():
    safe_select._installed = False
    safe_select.reset()
    yield
    safe_select._installed = False
    safe_select.reset()


def test_valid_value_passes_through():
    ui = FakeUi()
    safe_select.install(ui)
    ui.select(label="播放器", options={"pygame": "pygame", "browser": "browser"}, value="browser")
    assert ui.calls[-1]["kwargs"]["value"] == "browser"


def test_unknown_value_is_cleared():
    ui = FakeUi()
    safe_select.install(ui)
    ui.select(label="播放器", options={"pygame": "pygame"}, value="nonexistent")
    assert ui.calls[-1]["kwargs"]["value"] is None


def test_none_value_is_untouched():
    ui = FakeUi()
    safe_select.install(ui)
    ui.select(label="x", options={"a": "a"}, value=None)
    assert ui.calls[-1]["kwargs"]["value"] is None


def test_empty_string_is_untouched():
    ui = FakeUi()
    safe_select.install(ui)
    ui.select(label="声卡", options={"": "（没有设备）"}, value="")
    assert ui.calls[-1]["kwargs"]["value"] == ""


def test_callable_options_are_not_checked():
    """动态选项（函数）无法预判，不能误判成非法值。"""
    ui = FakeUi()
    safe_select.install(ui)
    ui.select(label="x", options=lambda: {"a": "a"}, value="whatever")
    assert ui.calls[-1]["kwargs"]["value"] == "whatever"


def test_list_options_supported():
    ui = FakeUi()
    safe_select.install(ui)
    ui.select(label="x", options=["a", "b"], value="c")
    assert ui.calls[-1]["kwargs"]["value"] is None


def test_install_is_idempotent():
    ui = FakeUi()
    safe_select.install(ui)
    first = ui.select
    safe_select.install(ui)
    assert ui.select is first


def test_option_keys_helper():
    assert safe_select._option_keys({"a": 1}) == {"a"}
    assert safe_select._option_keys(["a", "b"]) == {"a", "b"}
    assert safe_select._option_keys(("a",)) == {"a"}
    assert safe_select._option_keys(None) is None
    assert safe_select._option_keys(lambda: {}) is None
