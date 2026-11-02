"""MLX Audio 卡片：模型/音色下拉 + 上传音色。

这几个能力都是「界面看起来有、其实不工作」的高危区：
- 建页时预填下拉，写错了不会报错，只是下拉点开是空的；
- 上传音色走 multipart，字段名对不上服务端只会返回 400；
- 下拉没设初始值，配置里明明有音色，界面上却是空白。

所以这里不测像素，只测「代码里该接的线都接了」。
"""

import ast
from pathlib import Path

PAGE = Path(__file__).resolve().parents[2] / "webui_app" / "pages" / "tts.py"


def _build_fn():
    tree = ast.parse(PAGE.read_text("utf-8"))
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "build_tts_tab"
    )


def test_card_builds_model_and_voice_selects():
    src = PAGE.read_text("utf-8")
    assert "select_custom_tts_model = ui.select" in src
    assert "select_custom_tts_voice = ui.select" in src
    # 两个下拉都要能直接输入新值，否则用户没法填服务端没列出来的模型
    assert src.count("new_value_mode='add-unique'") >= 2


def test_mapping_uses_the_selects_not_old_inputs():
    """mapping 写的是 `mapping["custom_tts"] = {...}`，是赋值语句不是字典字面量。"""
    fn = _build_fn()
    found = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "custom_tts"
                    and isinstance(node.value, ast.Dict)):
                for k, v in zip(node.value.keys, node.value.values):
                    if isinstance(k, ast.Constant) and isinstance(v, ast.Tuple):
                        elt = v.elts[0]
                        if isinstance(elt, ast.Name):
                            found[k.value] = elt.id
    assert found.get("model") == "select_custom_tts_model", found
    assert found.get("voice") == "select_custom_tts_voice", found


def test_upload_uses_correct_multipart_field_names():
    src = PAGE.read_text("utf-8")
    # 服务端 /v1/voices/upload 的字段名是 file / name / model / ref_text
    assert "/v1/voices/upload" in src
    for field in ("file", "name", "model"):
        assert f'"{field}"' in src, field
    # 文件字段必须带 filename，否则服务端当普通表单值处理
    assert "filename=e.file.name" in src


def test_initial_options_are_set_at_build_time_not_by_timer():
    """建页期同步预填。

    ui.timer / on_connect 两条路都试过，都不可靠：
    定时器常在建页跑完前就打完了（once 不补枪），on_connect 的回调没有 slot 栈。
    """
    src = PAGE.read_text("utf-8")
    assert "custom_tts_build_initial_options()" in src
    # 预填必须发生在建页函数体内
    assert "def custom_tts_build_initial_options" in src
    assert "ui.timer" not in src.split("def custom_tts_build_initial_options")[0][-200:]


def test_prefill_sets_value_so_dropdown_is_not_blank():
    """只 set_options 不设 value，控件当前值是 None，界面上就是空下拉。"""
    src = PAGE.read_text("utf-8")
    assert "value=current_voice if current_voice in voice_options else None" in src
