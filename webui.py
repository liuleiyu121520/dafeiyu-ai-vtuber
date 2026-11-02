from nicegui import ui, app
import sys, os, json
import traceback
from pathlib import Path

from utils.my_log import logger
from utils.my_log import file_path as log_file_path
from utils.config import Config
from utils.common import Common
from utils.audio import Audio

from webui_app.api import register_api
from webui_app.chat_log import chat_log
from webui_app.config_store import update_config
from webui_app.confirm import confirm
from webui_app.dirty_guard import DirtyGuard
from webui_app.enabled_filter import EnabledFilter
from webui_app.errors import explain
from webui_app.fields import INVALID_RING, jump_to_element, set_reveal_hook
from webui_app.groups import refresh_pickers
from webui_app.helpers import AUDIO_SYNTHESIS_TYPE_OPTIONS
from webui_app.hints import move_long_placeholders_to_hints
from webui_app.labels import add_show_card_tooltips, name_bare_enable_switches, rename_show_card_switches
from webui_app.theme import LINK, internal_button_color
from webui_app.login import build_login, start_expiration_timer
from webui_app.overview import OVERVIEW, build_nav_chips, build_overview_tab, page_order
from webui_app.pages.assistant_anchor import build_assistant_anchor_tab
from webui_app.pages.chat import build_chat_tab
from webui_app.pages.common_config import build_common_config_tab, validate_config, write_local_qa_file
from webui_app.pages.copywriting import build_copywriting_tab
from webui_app.pages.data_analysis import build_data_analysis_tab
from webui_app.pages.image_recognition import build_image_recognition_tab
from webui_app.pages.interact_filter import build_interact_filter_tab
from webui_app.pages.integral import build_integral_tab
from webui_app.pages.llm import build_llm_tab
from webui_app.pages.serial import build_serial_tab
from webui_app.pages.static_pages import build_about_tab, build_docs_tab
from webui_app.pages.svc import build_svc_tab
from webui_app.pages.talk import build_talk_tab
from webui_app.pages.translate import build_translate_tab
from webui_app.pages.tts import build_tts_tab
from webui_app.pages.visual_body import build_visual_body_tab
from webui_app.pages.web_page import build_web_page_tab
from webui_app.repage import apply_page_plan, order_tabs
from webui_app.safe_select import install as install_safe_select
from webui_app.run_log import RunLog
from webui_app.processes import (
    restart_application,
    run_external_program,
    scroll_to_top,
    stop_external_program,
)
from webui_app.search import ConfigSearch
from webui_app.undo import undo
from webui_app.sections import add_section_indexes
from webui_app.state import state

"""

@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@.:;;;++;;;;:,@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@:;+++++;;++++;;;.@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@:++++;;;;;;;;;;+++;,@@@@@@@@@@@@@@@@@
@@@@@@@@@@@.;+++;;;;;;;;;;;;;;++;:@@@@@@@@@@@@@@@@
@@@@@@@@@@;+++;;;;;;;;;;;;;;;;;;++;:@@@@@@@@@@@@@@
@@@@@@@@@:+++;;;;;;;;;;;;;;;;;;;;++;.@@@@@@@@@@@@@
@@@@@@@@;;+;;;;;;;;;;;;;;;;;;;;;;;++:@@@@@@@@@@@@@
@@@@@@@@;+;;;;:::;;;;;;;;;;;;;;;;:;+;,@@@@@@@@@@@@
@@@@@@@:+;;:;;:::;:;;:;;;;::;;:;:::;+;.@@@@@@@@@@@
@@@@@@.;+;::;:,:;:;;+:++:;:::+;:::::++:+@@@@@@@@@@
@@@@@@:+;;:;;:::;;;+%;*?;;:,:;*;;;;:;+;:@@@@@@@@@@
@@@@@@;;;+;;+;:;;;+??;*?++;,:;+++;;;:++:@@@@@@@@@@
@@@@@.++*+;;+;;;;+?;?**??+;:;;+.:+;;;;+;;@@@@@@@@@
@@@@@,+;;;;*++*;+?+;**;:?*;;;;*:,+;;;;+;,@@@@@@@@@
@@@@@,:,+;+?+?++?+;,?#%*??+;;;*;;:+;;;;+:@@@@@@@@@
@@@@@@@:+;*?+?#%;;,,?###@#+;;;*;;,+;;;;+:@@@@@@@@@
@@@@@@@;+;??+%#%;,,,;SSS#S*+++*;..:+;?;+;@@@@@@@@@
@@@@@@@:+**?*?SS,,,,,S#S#+***?*;..;?;**+;@@@@@@@@@
@@@@@@@:+*??*??S,,,,,*%SS+???%++;***;+;;;.@@@@@@@@
@@@@@@@:*?*;*+;%:,,,,;?S?+%%S?%+,:?;+:,,,@@@@@@@@
@@@@@@@,*?,;+;+S:,,,,%?+;S%S%++:+??+:,,,:@@@@@@@@
@@@@@@@,:,@;::;+,,,,,+?%*+S%#?*???*;,,,,,.@@@@@@@@
@@@@@@@@:;,::;;:,,,,,,,,,?SS#??*?+,.,,,:,@@@@@@@@@
@@@@@@;;+;;+:,:%?%*;,,,,SS#%*??%,.,,,,,:@@@@@@@@@
@@@@@.+++,++:;???%S?%;.+#####??;.,,,,,,:@@@@@@@@@
@@@@@:++::??+S#??%#??S%?#@#S*+?*,,,,,,:,@@@@@@@@@@
@@@@@:;;:*?;+%#%?S#??%SS%+#%..;+:,,,,,,@@@@@@@@@@@
@@@@@@,,*S*;?SS?%##%?S#?,.:#+,,+:,,,,,,@@@@@@@@@@@
@@@@@@@;%?%#%?*S##??##?,..*#,,+:,,;*;.@@@@@@@@@@@
@@@@@@.*%??#S*?S#@###%;:*,.:#:,+;:;*+:@@@@@@@@@@@@
@@@@@@,%S??SS%##@@#%S+..;;.,#*;???*?+++:@@@@@@@@@@
@@@@@@:S%??%####@@S,,*,.;*;+#*;+?%??#S%+.@@@@@@@@@
@@@@@@:%???%@###@@?,,:**S##S*;.,%S?;+*?+.,..@@@@@@
@@@@@@;%??%#@###@@#:.;@@#@%%,.,%S*;++*++++;.@@@@@
@@@@@@,%S?S@@###@@@%+#@@#@?;,.:?;??++?%?***+.@@@@@
@@@@@@.*S?S####@@####@@##@?..:*,+:??**%+;;;;..@@@@
@@@@@@:+%?%####@@####@@#@%;:.;;:,+;?**;++;,:;:,@@@
@@@@@@;;*%?%@##@@@###@#S#*:;*+,;.+***?******+:.@@@
@@@@@@:;:??%@###%##@#%++;+*:+;,:;+%?*;+++++;:.@@@@
@@@@@@.+;:?%@@#%;+S*;;,:::**+,;:%??*+.@....@@@@@@@
@@@@@@@;*::?#S#S+;,..,:,;:?+?++*%?+::@@@@@@@@@@@@@
@@@@@@@.+*+++?%S++...,;:***??+;++:.@@@@@@@@@@@@@@@
@@@@@@@@:::..,;+*+;;+*?**+;;;+;:.@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@,+*++;;:,..@@@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@@::,.@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@

"""


"""
全局变量
"""
common = None
config = None
audio = None
config_path = None


"""
初始化基本配置
"""
def init():
    """
    初始化基本配置
    """
    global config_path, config, common, audio

    common = Common()

    if getattr(sys, 'frozen', False):
        # 当前是打包后的可执行文件
        bundle_dir = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
        file_relative_path = bundle_dir.resolve()
    else:
        # 当前是源代码
        file_relative_path = Path(__file__).parent.resolve()

    # logger.info(file_relative_path)

    # 初始化文件夹
    def init_dir():
        # 创建日志文件夹
        log_dir = file_relative_path / 'log'
        # mkdir 方法的 parents=True 参数可以确保父目录的创建（如有必要），exist_ok=True 则避免在目录已存在时抛出异常。
        log_dir.mkdir(parents=True, exist_ok=True)

        # 创建音频输出文件夹
        audio_out_dir = file_relative_path / 'out'
        audio_out_dir.mkdir(parents=True, exist_ok=True)

    init_dir()
    logger.debug("项目相关文件夹初始化完成")

    # 配置文件路径（可用 LUNA_CONFIG_PATH 覆盖，便于多实例与自动化测试）
    config_path = os.environ.get("LUNA_CONFIG_PATH") or str(file_relative_path / 'config.json')

    logger.debug("配置文件路径=" + str(config_path))

    # 实例化音频类
    audio = Audio(config_path, type=2)
    # 实例化配置类
    config = Config(config_path)

# 初始化基本配置
init()

# 下拉框取值兜底：配置里的值不在选项表里时（新增取值、旧版本遗留、无声卡设备号），
# 以前会让整个 WebUI 构建期抛 ValueError 打不开。这里统一降级成「置空显示」，
# 页面照常渲染，用户重新选一次即可修正。必须在建页面之前安装。
install_safe_select(ui)

# 将本地目录中的静态文件（如 CSS、JavaScript、图片等）暴露给 web 服务器，以便用户可以通过特定的 URL 访问这些文件。
if config.get("webui", "local_dir_to_endpoint", "enable"):
    for tmp in config.get("webui", "local_dir_to_endpoint", "config"):
        app.add_static_files(tmp['url_path'], tmp['local_dir'])

# 对 main.py 暴露的 HTTP 接口，注册一次即可
register_api(config, common, audio)

# 自托管字体（Mistral 规范：Inter 承担全部 UI 文字，Newsreader 替代 PP Editorial Old
# 做展示级衬线——商用授权字体拿不到，用同为「近衬线、编辑感」的开源 Newsreader 顶替，
# 不走外链 CDN，内网/离线部署不缺字）
_fonts_dir = Path(__file__).parent.resolve() / 'ui' / 'fonts'
if _fonts_dir.is_dir():
    app.add_static_files('/fonts', str(_fonts_dir))

# 暗夜模式：五套主题全是按浅色设计的（深色字、白渐变卡），锁死浅色，
# 不再跟随系统——深色系统下透明页面底会透出黑色画布，tab 条黑字直接隐身
ui.dark_mode(False)

"""
通用函数
"""
r"""
                                                                                                    
                                               .@@@@@                           @@@@@.              
                                               .@@@@@                           @@@@@.              
        ]]]]]   .]]]]`   .]]]]`   ,]@@@@@\`    .@@@@@,/@@@\`   .]]]]]   ]]]]]`  ]]]]].              
        =@@@@^  =@@@@@`  =@@@@. =@@@@@@@@@@@\  .@@@@@@@@@@@@@  *@@@@@   @@@@@^  @@@@@.              
         =@@@@ ,@@@@@@@ .@@@@` =@@@@^   =@@@@^ .@@@@@`  =@@@@^ *@@@@@   @@@@@^  @@@@@.              
          @@@@^@@@@\@@@^=@@@^  @@@@@@@@@@@@@@@ .@@@@@   =@@@@@ *@@@@@   @@@@@^  @@@@@.              
          ,@@@@@@@^ \@@@@@@@   =@@@@^          .@@@@@.  =@@@@^ *@@@@@  .@@@@@^  @@@@@.              
           =@@@@@@  .@@@@@@.    \@@@@@]/@@@@@` .@@@@@@]/@@@@@. .@@@@@@@@@@@@@^  @@@@@.              
            \@@@@`   =@@@@^      ,\@@@@@@@@[   .@@@@^\@@@@@[    .\@@@@@[=@@@@^  @@@@@.    
            
"""
# 配置
webui_ip = config.get("webui", "ip")
webui_port = config.get("webui", "port")
webui_title = config.get("webui", "title")

# CSS
theme_choose = config.get("webui", "theme", "choose")
tab_panel_css = config.get("webui", "theme", "list", theme_choose, "tab_panel")
card_css = config.get("webui", "theme", "list", theme_choose, "card")
button_bottom_css = config.get("webui", "theme", "list", theme_choose, "button_bottom")
button_bottom_color = config.get("webui", "theme", "list", theme_choose, "button_bottom_color")
button_internal_css = config.get("webui", "theme", "list", theme_choose, "button_internal")
# 五套主题这里的 button_internal_color 全是空串，留空的意思就是「用主题主色」，见 theme.py
button_internal_color = internal_button_color(
    config.get("webui", "theme", "list", theme_choose, "button_internal_color"))
switch_internal_css = config.get("webui", "theme", "list", theme_choose, "switch_internal")
echart_css = config.get("webui", "theme", "list", theme_choose, "echart")

# 未保存改动守卫：角标与浏览器关闭拦截，页面构建完成后在 goto_func_page 里接线
dirty_guard = DirtyGuard()

# 右下角两枚状态徽标的底样式。徽标是 ui.button，NiceGUI 会给它挂上 Quasar 的 text-primary，
# 那是条 !important 规则，普通内联色压不过，所以状态色一律要带 !important。
PILL_CSS = ("font-size:12px;padding:5px 14px;border-radius:9999px;background:#ffffff;"
            "border:1px solid #e2e8f0;box-shadow:0 2px 8px rgba(15,23,42,0.06);color:{} !important;"
            "font-weight:500;transition:all .15s ease;")

"""
页面级脚本与样式

- .q-page 底部留白：浮在页面上的 .q-page-sticky 会挡住最后一行，留白由 ResizeObserver 跟着它们算
- <900px 的流式化：定宽控件/卡片改成整行、网格降单列
- .narrow-tip：窄屏提示条，默认不显示，窗口 <900px 才显
- ?tab= 深链：NiceGUI 是单页应用，9 个 tab 都挂在同一个 URL 上，
  不写进地址栏就没法把「哪个配置页」发给别人，刷新也回不到原来那一页
"""
PAGE_SCRIPTS = """
<style>
/* ============ 现代清爽科技设计体系（Modern Studio Console） ============
   基调: 清爽高对比度、纯白卡片、浅灰底、精炼活力橙品牌色，统一规范排版。 */
@font-face{font-family:Inter;font-style:normal;font-weight:100 900;font-display:swap;
  src:url(/fonts/inter-var.woff2) format('woff2');}
:root{
  --luna-primary:#ea580c;        /* 品牌橙：活力温暖珊瑚橙 */
  --luna-primary-deep:#c2410c;   /* 深橙：文字/链接强调色，白底与浅底 > 4.5:1 过 AA */
  --luna-primary-hover:#f97316;  /* 悬停亮橙 */
  --luna-ink:#0f172a;           /* 现代 Slate 900 纯正标题与正文 */
  --luna-slate:#475569;         /* 次级文字 Slate 600 */
  --luna-steel:#64748b;         /* 辅助与说明微文本 Slate 500 */
  --luna-bg:#f8fafc;            /* 页面外壳底色：现代极简 Slate 50，告别发黄陈旧感 */
  --luna-card-bg:#ffffff;       /* 纯白卡片背景 */
  --luna-card-sub:#f8fafc;      /* 嵌套子卡片背景 */
  --luna-border:#e2e8f0;        /* 细致描边 Slate 200 */
  --luna-border-hover:#cbd5e1;  /* 悬停描边 Slate 300 */
  --luna-radius-sm:6px;
  --luna-radius-md:8px;
  --luna-radius-lg:12px;
}
/* 页面外壳打底：现代清爽浅灰蓝底色，彻底告别老旧发黄纸张感 */
html,body{background:var(--luna-bg);color:var(--luna-ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Inter,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:14px;line-height:1.5;-webkit-font-smoothing:antialiased;}
.q-page{padding-bottom:100px;}
/* 浏览器原生面：选区、滚动条、焦点环都从现代色板走，精致干净 */
::selection{background:rgba(234,88,12,0.18);color:var(--luna-ink);}
*{scrollbar-width:thin;scrollbar-color:#cbd5e1 transparent;}
::-webkit-scrollbar{width:8px;height:8px;}
::-webkit-scrollbar-track{background:transparent;}
::-webkit-scrollbar-thumb{background:#cbd5e1;border-radius:9999px;border:2px solid transparent;
  background-clip:content-box;}
::-webkit-scrollbar-thumb:hover{background:#94a3b8;background-clip:content-box;}
/* 触控与点击目标统一规范 */
.q-tab-panel .q-btn{min-height:34px;}
.q-tab-panel .q-toggle{padding:4px 0;min-height:34px;}
.q-tab-panel a.nicegui-link{min-height:28px;display:inline-flex;align-items:center;color:var(--luna-primary-deep);}
/* 动效偏好：尊重系统「减少动态效果」 */
@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{transition-duration:0.01ms !important;animation-duration:0.01ms !important;
    scroll-behavior:auto !important;}
}
/* 键盘焦点环：Quasar 聚焦还给键盘用户 */
.q-btn:focus-visible,.q-tab:focus-visible,.q-toggle:focus-visible .q-checkbox__inner,
input:focus-visible,textarea:focus-visible,.q-field__native:focus-visible{
  outline:2px solid var(--luna-primary);outline-offset:2px;}
/* ---- 卡片：纯白底、12px 圆角、细致发丝边框与柔和微阴影 ---- */
.q-tab-panel .q-card{border-radius:var(--luna-radius-lg);border:1px solid var(--luna-border);
  background:var(--luna-card-bg);box-shadow:0 1px 3px rgba(15,23,42,0.04);
  transition:box-shadow .2s ease,border-color .2s ease;}
.q-tab-panel .q-card:hover{border-color:var(--luna-border-hover);box-shadow:0 4px 14px rgba(15,23,42,0.06);}
/* 卡片首行标题：统一现代无衬线粗体，字号 16px/600，杜绝老式衬线体割裂感 */
.q-tab-panel .q-card > div:first-child:not([class]),
.q-tab-panel .q-expansion-item > .q-expansion-item__container > .q-item .q-item__label{
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif !important;
  font-size:16px;font-weight:600;letter-spacing:-0.01em;color:var(--luna-ink);}
/* 卡片描述行（descriptions.show_card_desc 挂的 luna-desc） */
.q-tab-panel .luna-desc{font-size:13px;color:var(--luna-steel);line-height:1.5;margin-bottom:6px;}
/* ---- 按钮：8px 圆角，规范高度与层级 ---- */
.q-tab-panel .q-btn,.q-page-sticky .q-btn{border-radius:var(--luna-radius-md);font-weight:500;
  transition:filter .15s ease,background-color .15s ease,box-shadow .15s ease,transform .1s ease;}
.q-tab-panel .q-btn--standard:hover,.q-page-sticky .q-btn--standard:hover{filter:brightness(0.96);transform:translateY(-1px);}
.q-tab-panel .q-btn--standard:active,.q-page-sticky .q-btn--standard:active{filter:brightness(0.90);transform:translateY(0);}
/* flat 按钮悬停背景 */
.q-tab-panel .q-btn--flat:hover,.luna-nav .q-btn--flat:hover{background:#f1f5f9;}
/* primary 实底按钮 */
.q-btn.bg-primary{color:#ffffff !important;background-color:var(--luna-primary) !important;}
.q-btn[style*="234, 88, 12"],.q-btn[style*="204, 58, 5"],.q-btn[style*="31, 31, 31"]{color:#ffffff !important;}
/* ---- 输入控件：纯白底、40px 高、清晰细边框，聚焦现代高亮 ---- */
.q-tab-panel .q-field--outlined .q-field__control{border-radius:var(--luna-radius-md);
  background:#ffffff;min-height:40px;height:40px;transition:border-color .15s ease,box-shadow .15s ease;}
.q-tab-panel .q-textarea .q-field__control{height:auto;min-height:40px;}
.q-tab-panel .q-field__control:before{border:1px solid var(--luna-border) !important;transition:border-color .15s ease;}
.q-tab-panel .q-field--outlined:hover .q-field__control:before{border-color:var(--luna-border-hover) !important;}
.q-tab-panel .q-field--focused .q-field__control:after{border-color:var(--luna-primary) !important;border-width:2px !important;}
.q-tab-panel .q-field--focused{box-shadow:0 0 0 3px rgba(234,88,12,0.12);border-radius:var(--luna-radius-md);}
.q-tab-panel .q-field--filled .q-field__control{background:#f8fafc;}
.q-tab-panel .q-field__label{color:var(--luna-slate);font-size:13px;font-weight:500;}
.q-tab-panel .q-field--focused .q-field__label{color:var(--luna-primary-deep);}
/* 开关/勾选选中色 */
.q-tab-panel .q-toggle__thumb--truthy{color:var(--luna-primary);}
.q-tab-panel .q-toggle__track--truthy{background:rgba(234,88,12,0.35);}
/* ---- 提示条：现代清新风格，告别脏黄色 ---- */
.tip-bar{padding:10px 16px;border-radius:var(--luna-radius-md);background:#eff6ff;
  border:1px solid #bfdbfe;border-left:4px solid #3b82f6;
  font-size:13px;line-height:1.6;color:#1e40af;}
/* ---- 折叠区标题加一点分量 ---- */
.q-tab-panel .q-expansion-item > .q-expansion-item__container > .q-item{font-weight:600;}
.q-tab-panel .q-item{border-radius:var(--luna-radius-md);}
/* 对话框/菜单也吃同一套几何 */
.q-dialog .q-card,.q-menu .q-card{border-radius:var(--luna-radius-lg);box-shadow:0 10px 25px -5px rgba(15,23,42,0.12);}
.q-notification{border-radius:var(--luna-radius-md);box-shadow:0 4px 12px rgba(15,23,42,0.1);}
</style>
<script>
// 底部留白跟着操作条的真实高度走。写死一个数迟早被超：这条栏会随展开/收起、
// 窗口换行改高度（390 展开后实测 225px，把「密码」那个框压到了栏底下点不到）。
(function () {
    const shown = function (e) {
        const r = e.getBoundingClientRect();
        return r.height > 0 && getComputedStyle(e).display !== 'none';
    };
    const apply = function () {
        const page = document.querySelector('.q-page');
        const bars = [...document.querySelectorAll('.q-page-sticky')].filter(shown);
        if (!page || !bars.length) return;
        // 取所有浮层里最靠上的那条顶边：右下角是操作条与状态徽标，底部居中是删除的撤销托盘
        const top = Math.min.apply(null, bars.map(e => e.getBoundingClientRect().top));
        page.style.paddingBottom = Math.round(innerHeight - top + 12) + 'px';
    };
    if (!window.ResizeObserver) return;
    const poll = setInterval(function () {
        const bars = document.querySelectorAll('.q-page-sticky');
        if (!bars.length) return;
        clearInterval(poll);
        const ro = new ResizeObserver(apply);
        bars.forEach(e => ro.observe(e));
        apply();
    }, 300);
})();
</script>
<style>
.narrow-tip{display:none !important;}
/* 顶部工作区导航：纯白微透质感 + 细描边，吸顶常驻 */
.luna-nav{background:rgba(255,255,255,0.96);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);
  border-bottom:1px solid var(--luna-border);padding:8px 20px !important;
  box-shadow:0 1px 3px rgba(15,23,42,0.04);position:sticky;top:0;z-index:50;}
.luna-nav .q-btn{font-size:13px !important;font-weight:500 !important;padding:6px 12px !important;
  min-height:34px !important;border-radius:8px !important;}
/* 字段下方的说明微文本：排版紧凑清晰，统一最多两行排版，杜绝七八行竖向撑爆栅格 */
.q-tab-panel .q-field__messages{
  white-space:normal;font-size:11px !important;line-height:1.35 !important;
  color:var(--luna-steel);margin-top:2px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden;text-overflow:ellipsis;max-height:32px;
}
.q-tab-panel .q-field:hover .q-field__messages{
  color:var(--luna-slate);
}
.q-tab-panel .has-persistent-hint .q-field__bottom--animated{position:static;transform:none;padding:2px 4px 0 4px;}
.q-tab-panel .q-field:not(.q-field--error) .q-field__messages{color:var(--luna-steel);}
/* 卡片是 align-items:flex-start 的纵向 flex：给满宽，列宽由卡片决定 */
.q-tab-panel .q-card > .nicegui-grid{width:100%;}
.q-tab-panel > .q-card{width:100%;}
/* 字段宽度统一阶梯规范：130 / 220 / 340 / 560 四档标准阶梯 */
.q-tab-panel .q-field{max-width:100%;}
.q-tab-panel .q-field[style*="width: 50px"],
.q-tab-panel .q-field[style*="width: 64px"],
.q-tab-panel .q-field[style*="width: 80px"],
.q-tab-panel .q-field[style*="width: 93px"],
.q-tab-panel .q-field[style*="width: 97px"],
.q-tab-panel .q-field[style*="width: 100px"],
.q-tab-panel .q-field[style*="width: 106px"],
.q-tab-panel .q-field[style*="width: 120px"]{width:130px !important;}
.q-tab-panel .q-field[style*="width: 150px"],
.q-tab-panel .q-field[style*="width: 153px"],
.q-tab-panel .q-field[style*="width: 155px"],
.q-tab-panel .q-field[style*="width: 158px"],
.q-tab-panel .q-field[style*="width: 160px"],
.q-tab-panel .q-field[style*="width: 177px"],
.q-tab-panel .q-field[style*="width: 189px"],
.q-tab-panel .q-field[style*="width: 200px"],
.q-tab-panel .q-field[style*="width: 215px"],
.q-tab-panel .q-field[style*="width: 250px"]{width:220px !important;}
.q-tab-panel .q-field[style*="width: 300px"],
.q-tab-panel .q-field[style*="width: 320px"],
.q-tab-panel .q-field[style*="width: 350px"],
.q-tab-panel .q-field[style*="width: 400px"]{width:340px !important;}
.q-tab-panel .q-field[style*="width: 500px"],
.q-tab-panel .q-field[style*="width: 700px"],
.q-tab-panel .q-field[style*="width: 800px"],
.q-tab-panel .q-field[style*="width: 1000px"]{width:560px !important;}
/* ============ Top-Label 表单系统（上面 Label，下面 Input，彻底消灭框内挤迫感） ============ */
.q-tab-panel .q-field--labeled {
  padding-top: 24px !important;
  position: relative !important;
  overflow: visible !important;
}
.q-tab-panel .q-field--labeled .q-field__inner {
  position: relative !important;
  overflow: visible !important;
}
.q-tab-panel .q-field--labeled .q-field__control {
  overflow: visible !important;
  border: 1px solid #cbd5e1 !important;
  border-radius: 6px !important;
  background: #ffffff !important;
  min-height: 38px !important;
  height: 38px !important;
  padding: 0 10px !important;
  transition: border-color .15s ease, box-shadow .15s ease !important;
}
/* 去除 Quasar 原生 outlined / standard 边框与缺口干扰 */
.q-tab-panel .q-field--labeled .q-field__control:before,
.q-tab-panel .q-field--labeled .q-field__control:after {
  display: none !important;
}
/* 获得焦点时现代微光外环 */
.q-tab-panel .q-field--focused .q-field__control {
  border-color: var(--luna-primary) !important;
  box-shadow: 0 0 0 3px rgba(234, 88, 12, 0.14) !important;
}
/* 标签放在输入框正上方外部，左沿与输入框外边框严格对齐 */
.q-tab-panel .q-field--labeled .q-field__label {
  position: absolute !important;
  top: -24px !important;
  left: -11px !important;
  transform: none !important;
  font-size: 13px !important;
  font-weight: 600 !important;
  color: #334155 !important;
  line-height: 20px !important;
  letter-spacing: 0.01em !important;
  pointer-events: none !important;
  z-index: 10 !important;
  white-space: nowrap !important;
}
.q-tab-panel .q-field--focused .q-field__label {
  color: var(--luna-primary) !important;
}
/* 彻底清除 Quasar 在 labeled 模式下 control-container 自带的 24px 顶部 padding，
   确保下拉菜单（q-select）选中的值文本与原生输入框严格垂直居中在 38px 容器内，绝不下坠溢出 */
.q-tab-panel .q-field--labeled .q-field__control-container {
  padding-top: 0 !important;
  align-items: center !important;
}
/* 单行原生输入框垂直居中 */
.q-tab-panel .q-field--labeled .q-field__native {
  padding-top: 0 !important;
  padding-bottom: 0 !important;
  height: 36px !important;
  line-height: 36px !important;
  font-size: 13px !important;
  color: #0f172a !important;
}
/* 下拉菜单单选选中文本对齐并居中 */
.q-tab-panel .q-select.q-field--labeled .q-field__native {
  min-height: 36px !important;
  height: 36px !important;
  line-height: 36px !important;
  align-items: center !important;
  padding: 0 !important;
}
.q-tab-panel .q-select.q-field--labeled .q-field__native span {
  line-height: 36px !important;
}
/* 下拉框与输入框的附带图标容器（下拉箭头、清空按钮等）垂直居中对齐 */
.q-tab-panel .q-field__marginal {
  height: 38px !important;
  min-height: 38px !important;
  align-items: center !important;
  padding: 0 4px !important;
}
.q-tab-panel .q-field__marginal .q-icon {
  font-size: 20px !important;
  line-height: 1 !important;
}
/* 多行文本框 textarea 自适应高度 */
.q-tab-panel .q-textarea.q-field--labeled .q-field__control {
  min-height: 80px !important;
  height: auto !important;
}
.q-tab-panel .q-textarea.q-field--labeled .q-field__native {
  min-height: 64px !important;
  height: auto !important;
  line-height: 1.5 !important;
  padding: 6px 0 !important;
}
/* 辅助说明与提示信息（bottom / hint）排在输入框下方 */
.q-tab-panel .q-field__bottom {
  padding: 4px 0 0 0 !important;
  font-size: 12px !important;
  color: #64748b !important;
  line-height: 1.4 !important;
}
/* 行内节奏与垂直基线：flex-start 对齐顶部 */
.q-tab-panel .nicegui-row:not([class*="gap-"]){gap:12px 16px;align-items:flex-start;margin-bottom:8px;}
/* 对齐同行按钮与开关：当行内有带 label 的输入框时，给没有 label 的按钮和开关补上 24px 顶部距离，与输入框本体对齐 */
.q-tab-panel .nicegui-row:has(.q-field--labeled) > .q-btn,
.q-tab-panel .nicegui-grid:has(.q-field--labeled) > .q-btn {
  margin-top: 24px !important;
  min-height: 38px !important;
  height: 38px !important;
  align-self: flex-start !important;
}
.q-tab-panel .nicegui-row:has(.q-field--labeled) > .q-toggle,
.q-tab-panel .nicegui-grid:has(.q-field--labeled) > .q-toggle {
  margin-top: 24px !important;
  min-height: 38px !important;
  height: 38px !important;
  display: inline-flex !important;
  align-items: center !important;
  align-self: flex-start !important;
  padding: 0 4px !important;
}
/* 嵌套卡片降一档现代 Slate-50 底色与细描边，层级鲜明 */
.q-tab-panel .q-card .q-card{background:var(--luna-card-sub) !important;
  border-color:var(--luna-border) !important;box-shadow:none !important;}
@media (max-width:899px){
  .narrow-tip{display:flex !important;}
  /* 窄屏用样式表 !important 覆盖内联宽度：定宽控件降整行，卡片和按钮最多撑满一行 */
  .q-tab-panel .q-field{min-width:0 !important;}
  .q-tab-panel .q-field,
  .q-tab-panel .q-img,
  .q-tab-panel img,
  .q-tab-panel video,
  .q-tab-panel audio,
  .q-tab-panel .q-table{width:100% !important;max-width:100% !important;}
  .q-tab-panel .q-card{width:100% !important;max-width:100% !important;}
  .q-tab-panel .q-btn{max-width:100%;}
  /* flex 子项默认 min-width:auto，有一个定宽控件就会把整行顶出视口；
     NiceGUI 的 row/column 落到 DOM 是 .nicegui-row / .nicegui-column */
  .q-tab-panel .nicegui-row > *,
  .q-tab-panel .nicegui-column > *{min-width:0;}
  .q-tab-panel .nicegui-grid{grid-template-columns:1fr !important;}
  /* 开关/勾选框被挤窄时「启用」会竖排两行，这类控件保持自身宽度、标签不折行 */
  .q-tab-panel .q-toggle,
  .q-tab-panel .q-checkbox,
  .q-tab-panel .q-radio{flex:none;}
  .q-tab-panel .q-q-item__label{white-space:nowrap;}
  .q-tab-panel pre{overflow-x:auto;max-width:100%;}
  /* 区块索引窄屏收成一行横向滑，同时压住宽度，否则 nowrap 会把面板顶宽 */
  .section-index{flex-wrap:nowrap !important;overflow-x:auto;
                 width:100% !important;max-width:100% !important;min-width:0;}
  .section-index > *{flex:none;}
  /* 平铺页面导航同理：收成横向滑 */
  .luna-nav{flex-wrap:nowrap !important;overflow-x:auto;}
  .luna-nav > *{flex:none;}
  /* 图表容器 height:600px 写在主题里，窄屏改按宽度等比，ECharts 自己会重排 */
  .nicegui-echart{height:auto !important;aspect-ratio:1/1;max-height:70vh;}
}
/* 「只看已启用」：藏只靠 tab_panels 上的 luna-only-on 命中卡上的 luna-off-card，
   控件 visible 一直是 True，搜索与未保存清单照旧收得到被藏项——跳转前先自动取消过滤。
   权重压过 .q-card 自己的 display:flex，不用 !important。 */
.luna-only-on .luna-off-card{display:none;}
/* 整页被藏光时补一句解释，文字写在样式表里，服务端只负责挂 luna-panel-empty */
.luna-only-on .luna-panel-empty::after{content:'这一页的功能都没启用，所以没有可显示的卡片；'
    '关掉上面的「只看已启用」就能看到全部配置。';
    display:block;margin-top:8px;padding:14px 16px;border-radius:var(--luna-radius-md);
    background:var(--luna-cream-soft);border:1px solid var(--luna-beige);
    color:var(--luna-slate);font-size:13px;line-height:1.6;}
/* 区块索引栏吸顶与卡片跳转防遮挡偏移量 */
.q-tab-panel .q-card,
.q-tab-panel .q-expansion-item {
  scroll-margin-top: 85px;
}
</style>
<script>
(function () {
    function tabByName(name) {
        return Array.prototype.find.call(document.querySelectorAll('.q-tab'),
                                        function (el) { return el.textContent.trim() === name; });
    }

    var want = new URLSearchParams(location.search).get('tab');
    if (want) {
        var tries = 0;
        var timer = setInterval(function () {
            var el = tabByName(want);
            if (el) { clearInterval(timer); el.click(); }
            else if (++tries > 40) { clearInterval(timer); }
        }, 100);
    }

    document.addEventListener('click', function (ev) {
        if (!ev.target.closest) { return; }
        // 地址栏 ?tab= 的常规同步在 mark() 回调里做（分组芯片点的组名不是页面名，不能直接写）。
        // 这里保留 .q-tab 一支：深链恢复时对隐藏 tab 的程序化 click 仍走这里
        var el = ev.target.closest('.q-tab');
        if (el) { history.replaceState(null, '', '?tab=' + encodeURIComponent(el.textContent.trim())); }
    }, true);
})();
</script>
"""

def goto_func_page():
    """
    跳转到功能页
    """
    if config.get("login", "enable"):
        # 十分钟一次的检测
        start_expiration_timer(config, common)

    # 开关灯已移除：锁死浅色后按钮只会把页面切成「黑壳 + 浅色卡」的坏样子（见 ui.dark_mode(False) 处）

    # 读取webui配置到dict变量
    def webui_config_to_dict(config_data):
        """读取webui配置到dict变量

        Args:
            config_data (dict): 从本地配置文件读取的dict数据
        """

        # 类型处理函数
        try:
            for page_config in page_config_mappings:
                if page_config is None:
                    continue
                if page_config.mapping:
                    config_data = update_config(page_config.mapping, config, config_data, None)
                if page_config.on_save:
                    page_config.on_save(config_data)

            return config_data
        except Exception as e:
            logger.error(f"无法读取webui配置到变量！\n{e}")
            ui.notify(position="top", type="negative",
                        message=f"读取界面上的值失败：{explain(e)}（完整错误见服务端日志）")
            logger.error(traceback.format_exc())

            return None

    # 保存配置
    def save_config():
        """
        保存配置到本地配置文件中
        """
        # 配置检查：拦下来时不只弹一条 toast，还要把用户带到那个框上描一圈红
        error = validate_config()
        if error:
            message, field = error
            ui.notify(position="top", type="negative",
                      message=message if field is None else f'{message} · 已跳到那个框')
            if field is not None:
                jump_to_element(field, button_bottom_color, select_tab, color=INVALID_RING)
            return False

        try:
            with open(config_path, 'r', encoding="utf-8") as config_file:
                config_data = json.load(config_file)
        except Exception as e:
            logger.error(f"无法读取配置文件！\n{e}")
            ui.notify(position="top", type="negative",
                message=f"配置文件读不出来：{explain(e)}（路径 {config_path}）")
            return False

        # 读取webui配置到dict变量
        config_data = webui_config_to_dict(config_data)
        if config_data is None:
            return False

        # 写入本地问答json数据到文件（控件在通用配置页模块里）
        if not write_local_qa_file():
            return False

        # 写入配置到配置文件
        try:
            with open(config_path, 'w', encoding="utf-8") as config_file:
                json.dump(config_data, config_file, indent=2, ensure_ascii=False)
                config_file.flush()  # 刷新缓冲区，确保写入立即生效

            logger.info("配置数据已成功写入文件！")
            ui.notify(position="top", type="positive", message="配置数据已成功写入文件！")

            # 写盘成功后才清脏状态：放在最后是因为 on_save 回调里还会回写控件值，
            # 提前清会让守卫把「自己写的值」重新记成未保存
            dirty_guard.mark_saved()
            # 已经写盘了，再让撤销把控件恢复回去只会和文件对不上
            undo.forget()

            return True
        except Exception as e:
            logger.error(f"无法写入配置文件！\n{str(e)}")
            ui.notify(position="top", type="negative",
                    message=f"配置文件写不进去：{explain(e)}（路径 {config_path}）")
            return False
        


    r"""

    ..............................................................................................................
    ..............................................................................................................
    ..........................,]].................................................................................
    .........................O@@@@^...............................................................................
    .....=@@@@@`.....O@@@....,\@@[.....................................,@@@@@@@@@@]....O@@@^......=@@@@....O@@@^..
    .....=@@@@@@.....O@@@............................................=@@@@/`..,[@@/....O@@@^......=@@@@....O@@@^..
    .....=@@@@@@@....O@@@....,]]]].......]@@@@@]`.....,/@@@@\`....../@@@@..............O@@@^......=@@@@....O@@@^..
    .....=@@@/@@@\...O@@@....=@@@@....,@@@@@@@@@@^..,@@@@@@@@@@\...=@@@@...............O@@@^......=@@@@....O@@@^..
    .....=@@@^,@@@\..O@@@....=@@@@...,@@@@`........=@@@/....=@@@\..=@@@@....]]]]]]]]...O@@@^......=@@@@....O@@@^..
    .....=@@@^.=@@@^.O@@@....=@@@@...O@@@^.........@@@@......@@@@..=@@@@....=@@@@@@@...O@@@^......=@@@@....O@@@^..
    .....=@@@^..\@@@^=@@@....=@@@@...@@@@^........,@@@@@@@@@@@@@@..=@@@@.......=@@@@...O@@@^......=@@@@....O@@@^..
    .....=@@@^...\@@@/@@@....=@@@@...O@@@^.........@@@@`...........,@@@@`......=@@@@...O@@@^......=@@@@....O@@@^..
    .....=@@@^....@@@@@@@....=@@@@...,@@@@`........=@@@@......,.....=@@@@`.....=@@@@...=@@@@`.....@@@@^....O@@@^..
    .....=@@@^....,@@@@@@....=@@@@....,@@@@@@@@@@`..=@@@@@@@@@@@`....,@@@@@@@@@@@@@@....,@@@@@@@@@@@@`.....O@@@^..
    .....,[[[`.....,[[[[[....,[[[[.......[@@@@@[`.....,[@@@@@[`.........,\@@@@@@[`.........[@@@@@@[........[[[[`..
    ..............................................................................................................
    ..............................................................................................................

    """

    # 语音合成所有配置项
    audio_synthesis_type_options = AUDIO_SYNTHESIS_TYPE_OPTIONS

    # 聊天类型所有配置项
    page_config_mappings = []

    # NiceGUI 的默认 primary 是 #5898d4：白字压上去只有 3.06:1，全站没设颜色的按钮、
    # 芯片、链接全都用它（实测 64 处低于 WCAG AA 的 4.5:1）。换成同色系的深色版，
    # 白字变 6.3:1，一处改动收掉整类。
    ui.colors(primary=LINK)

    # 窄屏提示：定宽控件在 <900px 已被上面那段样式表改成单列，不再裁；
    # 这条只说明「窄屏能看到的东西少」，别再写会被裁掉那种已经不实的话
    with ui.row().style(
            'background:#fff0c2;color:#5c4a1e;padding:8px 14px;border-radius:8px;border:1px solid #e6d5a8;'
            'justify-content:center;align-items:center;margin:0 10px 6px;').classes('narrow-tip'):
        ui.label('窄屏已自动改成单列，配置项多时要滚很久；能放宽窗口就放宽，一屏能看到更多。') \
            .style('font-size:13px;')

    # 分组导航条：16 个平铺 tab 归到「总览 + 6 组」，点组名跳到该组第一页。
    # 这里不重建面板，只是给既有 tab 加一层可读的外壳
    def select_tab(name):
        tabs.set_value(name)

    config_search = ConfigSearch(select_tab, button_bottom_color)
    # 「只看已启用」开关（走查 audit-9）：开关建在导航条里，藏不藏由 tab_panels 上一个类决定
    enabled_filter = EnabledFilter()
    mark_group = build_nav_chips(select_tab, button_bottom_color.rstrip(';'),
                                 config_search.open, enabled_filter.build_switch)
    # 运行日志的对话框也必须在建页时建好，点「运行日志」只是填内容 + 打开
    run_log = RunLog(lambda: log_file_path).build()

    # 9 大核心工作区 tab 条已隐藏：导航由上方的单排工作区按钮负责，
    # 这条只保留作 tab_panels 的绑定与 ?tab= 深链脚本 el.click() 的落点
    # （display:none 的元素程序化 click 依然生效）
    with ui.tabs().classes('w-full').style('display:none;') as tabs:
        overview_page = ui.tab('总览')
        chat_page = ui.tab('聊天')
        live_connect_page = ui.tab('直播接入')
        brain_page = ui.tab('人设与大脑')
        voice_stage_page = ui.tab('声音与表现')
        danmu_page = ui.tab('弹幕与礼仪')
        interaction_page = ui.tab('互动与玩法')
        hardware_page = ui.tab('外设与联动')
        system_page = ui.tab('运维与系统')

    tabs.on_value_change(lambda e: mark_group(e.value))
    mark_group(OVERVIEW)

    with ui.tab_panels(tabs, value=overview_page).classes('w-full') as tab_panels:
        with ui.tab_panel(overview_page).style(tab_panel_css):
            build_overview_tab(config, state, dirty_guard, select_tab, save_config, card_css,
                               button_bottom_color)

        with ui.tab_panel(chat_page).style(tab_panel_css) as chat_panel:
            page_config_mappings.append(build_chat_tab(
                config, common, state, chat_log, card_css, switch_internal_css, button_internal_color, button_internal_css))

        with ui.tab_panel(live_connect_page).style(tab_panel_css) as live_connect_panel:
            page_config_mappings.append(build_common_config_tab(
                config, common, card_css, switch_internal_css, button_internal_color, button_internal_css))

        with ui.tab_panel(brain_page).style(tab_panel_css) as brain_panel:
            page_config_mappings.append(build_llm_tab(
                config, common, card_css, switch_internal_css, button_bottom_color, button_bottom_css, button_internal_color, button_internal_css))
            page_config_mappings.append(build_image_recognition_tab(
                config, common, state, card_css, switch_internal_css, button_internal_color, button_internal_css))
            page_config_mappings.append(build_translate_tab(config, card_css, switch_internal_css))

        with ui.tab_panel(voice_stage_page).style(tab_panel_css) as voice_stage_panel:
            page_config_mappings.append(build_tts_tab(
                config, common, audio, card_css, switch_internal_css, button_internal_color, button_internal_css))
            page_config_mappings.append(build_svc_tab(config, common, card_css, switch_internal_css))
            page_config_mappings.append(build_talk_tab(
                config, common, state, card_css, switch_internal_css, button_internal_color, button_internal_css))
            page_config_mappings.append(build_visual_body_tab(config, common, card_css, switch_internal_css))

        with ui.tab_panel(danmu_page).style(tab_panel_css) as danmu_panel:
            page_config_mappings.append(build_interact_filter_tab(config, card_css, switch_internal_css))

        with ui.tab_panel(interaction_page).style(tab_panel_css) as interaction_panel:
            page_config_mappings.append(build_copywriting_tab(
                config, common, audio, card_css, switch_internal_css, button_internal_color, button_internal_css))
            page_config_mappings.append(build_integral_tab(config, card_css, switch_internal_css))

        with ui.tab_panel(hardware_page).style(tab_panel_css) as hardware_panel:
            page_config_mappings.append(build_assistant_anchor_tab(
                config, common, audio_synthesis_type_options, card_css, switch_internal_css))
            page_config_mappings.append(build_serial_tab(
                config, card_css, switch_internal_css, button_internal_color, button_internal_css))

        with ui.tab_panel(system_page).style(tab_panel_css) as system_panel:
            page_config_mappings.append(build_data_analysis_tab(config, config_path, echart_css))
            page_config_mappings.append(build_web_page_tab(
                config, common, config_path, webui_config_to_dict, card_css, switch_internal_css,
                button_internal_color, button_internal_css, dirty_guard))
            build_docs_tab(card_css)
            build_about_tab(card_css)

    # 底部操作条：原来是一张 position:fixed 的定宽 grid，永久压住内容——1440 宽遮 46px 一条，
    # 390 宽换行成 114px 高（占视口 15%）还有一半按钮掉出视口。改成右下角可折叠的一条，
    # 平时只留「未保存」角标和一个按钮，要用了再展开。
    def ask_stop():
        confirm.ask('确认停止正在运行的直播程序？', lambda: stop_external_program(config),
                    '弹幕监听、文案播报和虚拟身体会一起停下；正在直播的话，这一下就是断播。')

    def ask_restart():
        confirm.ask('确认重启 webui？', lambda: restart_application(config),
                    '会先停掉正在运行的直播程序，再用当前配置重新拉起 webui；'
                    '没点「保存配置」的改动会全部丢失。')

    ops_open = False

    def switch_ops():
        nonlocal ops_open
        ops_open = not ops_open
        ops_row.set_visibility(ops_open)
        button_ops.set_text('收起' if ops_open else '操作')

    run_badge = None
    run_state_shown = None

    def paint_run_state():
        """底栏常驻一枚运行态徽标：原先点完「一键运行」只有条会自己消失的 toast，按钮形态不变。

        运行态也会被 /sys_cmd 接口从别处改掉，所以只能轮询，不能在按钮回调里刷。
        这里刻意只报状态、不禁用按钮：main.py 自己崩掉时 running_flag 会留在 True，
        禁用「一键运行」就等于把人锁在重启不了的状态。
        """
        nonlocal run_state_shown
        on = bool(state.running_flag)
        if on == run_state_shown:
            return
        run_state_shown = on
        run_badge.set_text('● 直播程序运行中' if on else '○ 直播程序未运行')
        run_badge.style(PILL_CSS.format('#15803d' if on else '#6a6a6a'))

    with ui.page_sticky(position="bottom-right", x_offset=12, y_offset=12):
        with ui.column().style("align-items:flex-end;gap:8px;"):
            # 角标做成真按钮：点开「哪些项还没保存、现在是什么值」的清单（story-2 验收标准）
            dirty_badge = ui.button('● 配置已保存', on_click=dirty_guard.open_list).props(
                'flat no-caps dense').style(PILL_CSS.format('#15803d')).tooltip('点开看哪些项还没保存')
            run_badge = ui.button('○ 直播程序未运行', on_click=lambda: select_tab(OVERVIEW)).props(
                'flat no-caps dense').style(PILL_CSS.format('#6a6a6a')).tooltip('到总览页看开播就绪度')

            with ui.row().style(
                    "flex-wrap:wrap;justify-content:flex-end;gap:6px;"
                    "background:rgba(255,255,255,0.96);backdrop-filter:blur(12px);"
                    "border:1px solid #e2e8f0;border-radius:12px;padding:6px 10px;"
                    "box-shadow:0 8px 24px rgba(15,23,42,0.12);"
            ) as ops_row:
                button_save = ui.button('保存配置', on_click=lambda: save_config(), color=button_bottom_color).style(button_bottom_css).tooltip("保存webui的配置到本地文件，有些配置保存后需要重启生效")
                button_run = ui.button('一键运行', on_click=lambda: run_external_program(config), color=button_bottom_color).style(button_bottom_css).tooltip("运行main.py")
                # 创建一个按钮，用于停止正在运行的程序
                button_stop = ui.button("停止运行", on_click=ask_stop, color=button_bottom_color).style(button_bottom_css).tooltip("停止运行main.py")
                # 运行结果原先只有一条会自行消失的 toast，出问题得自己去翻文件（走查 audit-3）
                ui.button('运行日志', icon='receipt_long', on_click=run_log.open,
                          color=button_bottom_color).style(button_bottom_css).tooltip(
                    '看今天的日志末尾 400 行；日志文件在 log/ 下，按大小滚动')
                button_restart = ui.button('重启', on_click=ask_restart, color=button_bottom_color).style(button_bottom_css).tooltip("停止运行main.py并重启webui")
            ops_row.set_visibility(False)

            with ui.row().style(
                    "align-items:center;gap:6px;"
                    "background:rgba(255,255,255,0.96);backdrop-filter:blur(12px);"
                    "border:1px solid #e2e8f0;border-radius:9999px;padding:4px 8px;"
                    "box-shadow:0 4px 16px rgba(15,23,42,0.08);"
            ):
                button_top = ui.button('回到顶部', icon='keyboard_arrow_up', on_click=lambda: scroll_to_top(), color=button_bottom_color).style(button_bottom_css).tooltip("平滑滚动到页面顶部")
                button_ops = ui.button('操作', icon='settings', on_click=switch_ops, color=button_bottom_color).style(button_bottom_css)

    ui.timer(1.0, paint_run_state)

    # 8 大工作区区块挂载：把 通用配置 的区块分流改挂到对应的宿主 TabPanel 上。
    # 只搬 DOM 归属，控件与 save_common 留在原文件，保存链路不动
    panel_map = {
        '直播接入': live_connect_panel,
        '人设与大脑': brain_panel,
        '声音与表现': voice_stage_panel,
        '弹幕与礼仪': danmu_panel,
        '互动与玩法': interaction_panel,
        '外设与联动': hardware_panel,
        '运维与系统': system_panel,
    }
    apply_page_plan(panel_map, live_connect_panel, card_css)
    order_tabs(tabs, page_order())

    # 危险操作确认框：NiceGUI 3 没有 ui.confirm，对话框又是元素（关闭只是隐藏），
    # 所以全站建一次放在布局里，各页面调用 confirm.ask 复用同一个
    confirm.build()

    # 全局搜索：索引在每次打开时现建，所以这里只是把对话框和 Ctrl+K 挂上
    config_search.build()

    # 长 tab 的区块索引（大语言模型 那种一二十块的一页，先让人看得见这页有什么）
    add_section_indexes(button_bottom_color)
    # 把 30 个只写「启用」的开关补成「启用+对象名」，要在挂脏状态监听之前做（文案是搜索与清单的键）
    name_bare_enable_switches()
    # 板块开关墙里 31 个裸配置键开关换成「品牌名/中文名 + 键」（走查 audit-5），同样要在挂监听前
    rename_show_card_switches()
    # 为板块开关墙的各个开关添加通俗易懂的功能说明 Tooltip
    add_show_card_tooltips()
    # 把塞在 placeholder 里的长解释挪到字段下方常驻（placeholder 一输入就没了，规则不能跟着没）
    move_long_placeholders_to_hints()
    # 「要删除的XX组」下拉的选项：行是建页循环里现造的，所以要等这一遍跑完才列得全
    refresh_pickers()
    # 「只看已启用」要在面板都建完之后才拿得着容器，开关本身已经在导航条里建好了
    enabled_filter.bind_panels(tab_panels)
    set_reveal_hook(enabled_filter.turn_off)

    # 未保存改动守卫接线：注入拦截脚本 + 角标 + 给页面上的输入控件挂变更记录
    dirty_guard.install()
    dirty_guard.bind_nav(select_tab, button_bottom_color)
    dirty_guard.build_list()
    # 删除的 5 秒撤销托盘：和确认框同理，必须在构建期建好再显隐
    undo.build()
    ui.add_head_html(PAGE_SCRIPTS)
    dirty_guard.bind_label(dirty_badge)
    dirty_guard.watch(force=True)
    # 「增加配置组」这类按钮是运行期现建控件的，定时补挂一次，别漏掉新加的组
    ui.timer(3.0, dirty_guard.watch)

    # 是否启用自动运行功能
    if config.get("webui", "auto_run"):
        logger.info("自动运行 已启用")
        run_external_program(config, type="api")

# 发送心跳包
ui.timer(9 * 60, lambda: common.send_heartbeat())


# 是否启用登录功能（暂不合理）
login_card_css = config.get("webui", "theme", "list", theme_choose, "login_card")
if config.get("login", "enable"):
    build_login(config, common, login_card_css, goto_func_page)
else:
    # 这里原先还建了一张空的 ui.card().style(login_card_css)：login_card 是
    # position:fixed 的全屏渐变，关掉登录后它就变成一张 0 字符的幽灵层，跟 tab_panel 的
    # 渐变重复、还是个随时跟新浮层抢层级的定位元素（走查 audit-17）
    # 这层 column 只是给功能页一个容器。原先带 text-align:center，它会一路继承到
    # Quasar 的折叠区标题上，把「功能管理 / 互动功能」那几条大标题推到条带正中（audit-21）
    with ui.column().style("width:100%;"):
        # 跳转到功能页
        goto_func_page()



ui.run(host=webui_ip, port=webui_port, title=webui_title, favicon="./ui/favicon-64.ico", language="zh-CN", dark=False, reload=False)
# ui.run(host=webui_ip, port=webui_port, title=webui_title, favicon="./ui/favicon-64.ico", language="zh-CN", dark=False, reload=False,
#        ssl_certfile="F:\\FunASR_WS\\cert.pem", ssl_keyfile="F:\\FunASR_WS\\key.pem")