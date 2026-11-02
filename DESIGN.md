# 大肥鱼 AI Vtuber · WebUI 设计规范（Modern Studio Console）

本文件是 WebUI 管理后台的视觉权威。针对原先发黄脏乱、排版错位不齐的问题，按现代化控制台（Modern Studio Console）标准进行重构：追求清爽、呼吸感、基线对齐与高扫描性。
实现入口：`webui.py` 的 `PAGE_SCRIPTS` 全局样式表 + `webui_app/theme.py` 色板常量 + `config.json` 的 `webui.theme` 主题配置。

## 一、 色板与视觉基调

| Token | 值 | 用途 |
|---|---|---|
| primary | `#ea580c` | 活力珊瑚橙：品牌主色，实底 CTA 按钮、开关选中、强调元素 |
| primary-deep | `#c2410c` | 深橙：文字/链接强调色，聚焦边框，通过 WCAG AA 对比度 |
| primary-hover | `#f97316` | 悬停橙：按钮与交互部件 hover 状态 |
| ink | `#0f172a` | 正文与标题主色（Slate 900），清晰纯正，告别脏灰 |
| slate | `#475569` | 次级文字、标签说明（Slate 600） |
| steel | `#64748b` | 辅助与微文本（Slate 500） |
| bg | `#f8fafc` | 页面外壳底色（Slate 50），彻底去除老旧发黄的复古纸张感 |
| card-bg | `#ffffff` | 纯白卡片主表面 |
| card-sub | `#f8fafc` | 嵌套子卡片底色（如模型子配置、知识库等） |
| border | `#e2e8f0` | 统一卡片与容器描边（Slate 200） |
| border-hover | `#cbd5e1` | 悬停描边（Slate 300） |
| 语义状态 | 成功 `#16a34a` · 警示 `#d97706` · 危险 `#dc2626` | 运行状态徽标、校验高亮 |

## 二、 字体规范

- 全局 UI 统一采用现代化无衬线字体栈：
  `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif`
- 摒弃老旧衬线体（Newsreader）在中文管理后台标题上的违和割裂感。
- 标题：16px / 600 字重，字间距 `-0.01em`，统一与正文、微文本形成清晰层级。
- 正文：14px / 1.5 行高。
- 说明文本：11–12px，规范单行或最多两行省略截断，杜绝拉撑表单。

## 三、 几何与表单对齐规范

- **圆角体系**：
  - 输入框、按钮、提示条：8px（`--luna-radius-md`）
  - 卡片、弹窗：12px（`--luna-radius-lg`）
  - 徽章、状态药丸：9999px（全圆角）
- **垂直对齐基线**：
  - 行内表单（`.nicegui-row`）：统一 `align-items: flex-start`，各控件顶部处于同一水平 Y 轴。
  - 标准输入控件（`Input` / `Select`）：统一 40px 高度（`min-height: 40px; height: 40px`）。
  - 行内伴随按钮（`Button`）：统一 40px 高度，`margin-top: 0`，与输入框顶部底沿完全平齐。
  - 开关（`Switch / Toggle`）：统一 40px 高度并在内部居中，彻底消除同行高低参差问题。
- **宽度标准四阶梯**：
  - 130px：数值、端口号、短参数（温度、top_p、惩罚值等）
  - 220px：名称、短密钥、IP 地址
  - 340px：URL、模型下拉、长键名
  - 560px：预设 prompt、长模板文本

## 四、 核心组件表现

- **顶部工作区导航**（`.luna-nav`）：
  - 纯白半透明玻璃质感（`rgba(255, 255, 255, 0.96); backdrop-filter: blur(12px)`），吸顶常驻。
  - 当前激活页深橙实底白字，未激活项悬停浅灰背景，平滑过渡。
- **区块索引条**（`.section-index`）：
  - 浮动毛玻璃容器，告别原先暗黄色调。
  - 分组标签为干净的浅灰圆角芯片，锚点按钮为白底细边框现代药丸。
- **右下角操作浮层**：
  - 整合式浮动坞，配备毛玻璃阴影容器与状态指示点（`●` 成功绿 / `○` 待命灰）。
- **提示条**（`.tip-bar`）：
  - 清新高可读的现代信息条，浅蓝背景（`#eff6ff`）+ 细描边（`#bfdbfe`）+ 鲜明蓝左边线（`#3b82f6`）。

## 五、 质量验证与防护

- 单元测试：`pytest tests/unit/` 全量通过（守住主题对比度与元素树指纹）。
- 运行时检查：Playwright 无头浏览器遍历 9 大工作区，无任何 JS 异常或布局溢出。
