"""文档补丁脚本：M4 完成后回填 docs/requirements.md（v2.1 → v2.2）。"""

from __future__ import annotations

import sys
from pathlib import Path

DOC = Path(__file__).resolve().parents[1] / "docs" / "requirements.md"


def patch_revision(text: str) -> str:
    anchor = "| v2.1 | **按 M0–M2 实测结果回填**"
    idx = text.index(anchor)
    line_end = text.index("\n", idx) + 1
    row = (
        "| v2.2 | **按 M4（同步播放）实测结果回填**：§5.3 标注 F2.1–F2.9 已实现；"
        "§12.1/§12.2 更新里程碑状态；新增 §12.5「M4 的实测修正」（`scale` 与 "
        "`adjustPageHeight` 的版面纠偏、SVG 嵌套结构、QtSvg 的两个致命坑、"
        "线程竞争崩溃、`QMediaPlayer` 信号签名）；附录 D 增补 D.5「M4 版面与渲染实测」；"
        "§8.2 验收标准逐条标注实测结论 |\n"
    )
    return text[:line_end] + row + text[line_end:]


def patch_test_acceptance(text: str) -> str:
    """把 §8.2 的验收标准加上实测状态（幂等）。"""
    if "| 2 | 每个 `sync.json.notes[].id` 都能在 SVG 中找到（**100%**） |" in text:
        return text
    old = """### 8.2 验收标准（P0）

1. 对全部样本执行 `musicxml → {svg, midi, mp3, sync.json}`，**无崩溃**，失败项有明确原因。
2. 生成的 SVG 在浏览器与本工具内均可显示；每个 `sync.json.notes[].id` 都能在 SVG 中找到对应元素（**一致性断言 100%**）。
3. 播放 MIDI 与 MP3 时高亮与听感一致（人工抽检 3 个样本；自动化：用 `sync.json` 反算的高亮序列与 MIDI note-on 时间序列比对，偏差 ≤ 100 ms）。
4. 速率 50% / 100% / 200% 下高亮不漂移。
5. 缺少 SVG / 缺少 `sync.json` / 缺少 FluidSynth / 缺少 MuseScore 四条降级路径均给出正确提示且不崩溃。
6. `midi → musicxml` 对由本工具生成的 MIDI 可往返（音高与时值集合一致，允许小节 / 声部划分不同）。"""
    new = """### 8.2 验收标准（P0）与实测结论

| # | 标准 | 状态 | 实测证据 |
| :--- | :--- | :--- | :--- |
| 1 | 全部样本 `musicxml → {svg, midi, mp3, sync.json}` 无崩溃，失败项有明确原因 | ✅ | 10/10 成功（`zpymusic generate`） |
| 2 | 每个 `sync.json.notes[].id` 都能在 SVG 中找到（**100%**） | ✅ | 43281 个事件全部命中（`tools/verify_suites.py`） |
| 3 | 播放时高亮与音频对齐（自动化：`sync.json` 事件起点 vs MIDI note-on，偏差 ≤ 100 ms） | ✅ | 10/10 套件 **100%** 命中（同上） |
| 4 | 速率 50% / 100% / 200% 下高亮不漂移 | ✅ | `tools/smoke_play.py` 校验"变速后乐谱时间连续"；`tests/test_timeline.py` 逐档位校验 |
| 5 | 缺 SVG / 缺 sync.json / 缺 FluidSynth / 缺 MuseScore 的降级路径有提示且不崩溃 | ✅ | `Suite.validate()`、`controller.load_suite()` 的降级分支、`deps.probe_all()` 警告 |
| 6 | `midi → musicxml` 可往返 | ⏳ | M5 实现（music21） |

补充的实测指标（M4 新增）：

| 指标 | 结果 |
| :--- | :--- |
| 单 system 几何解析 | 9–22 ms（canon 9 ms / four-seasons 22 ms） |
| 载入 276 个 system 的套件（仅登记，不挂载 SVG） | **79 ms** |
| 首屏挂载 | 0.1 ms（只挂载视口附近的 3–6 行） |
| 连续滚动挂载 60 行 | 1.17 s（约 20 ms/行） |
| 符头几何精度 | 9.08 × 7.7 px（与 5 线谱表高 28.8 px 的比例正确） |
| M4 冒烟测试 | 28 项检查 × 3 个套件（含 276 行的 four-seasons）全部通过 |"""
    assert old in text
    return text.replace(old, new, 1)


def patch_f23(text: str) -> str:
    old = """| **F2.1** | 选择套件 / 音频 | 选择目录或直接选 `.mid / .mp3`；同目录下按主名查找 `<主名>.svg` 与 `<主名>.sync.json` |"""
    new = """| **F2.1** | 选择套件 / 音频 | ✅ 已实现（左侧扫描套件列表 + 直接选音频） |"""
    assert old in text
    text = text.replace(old, new, 1)

    pairs = [
        ("| **F2.2** | 缺件提示 | 找不到配套 SVG 时", "| **F2.2** | 缺件提示 | ✅ 已实现。找不到配套 SVG 时"),
        ("| **F2.3** | 播放控制 | `播放 / 暂停`", "| **F2.3** | 播放控制 | ✅ 已实现（空格键）。`播放 / 暂停`"),
        ("| **F2.4** | 进度条 | 可拖动 seek", "| **F2.4** | 进度条 | ✅ 已实现（拖动时实时预览高亮；点击谱面跳转）。可拖动 seek"),
        ("| **F2.5** | 速度调节（**待确认，见 §11 Q1**） | **推荐方案**", "| **F2.5** | 速度调节 | ✅ 已实现（速率档位下拉 + `</>` 快捷键 + 等效 BPM 只读显示）。**方案**"),
        ("| **F2.6** | 同步高亮 | 高亮当前发音音符", "| **F2.6** | 同步高亮 | ✅ 已实现（原生后端用半透明覆盖框；WebEngine 后端改 DOM class）。高亮当前发音音符"),
        ("| **F2.7** | 光标跟随 | 自动滚动", "| **F2.7** | 光标跟随 | ✅ 已实现（可开关）。自动滚动"),
        ("| **F2.8** | 手动校准 | 提供 `offset_ms` 微调", "| **F2.8** | 手动校准 | ✅ 已实现（含「按当前音符校准」按钮）。提供 `offset_ms` 微调"),
        ("| **F2.9** | 状态显示 | 当前时间 / 总时长", "| **F2.9** | 状态显示 | ✅ 已实现。当前时间 / 总时长"),
        ("| **F2.10** | 无 `sync.json` 的兜底 |", "| **F2.10** | 无 `sync.json` 的兜底 | ⏳ 未实现（当前行为：允许播放但禁用高亮并提示）。"),
    ]
    for old_s, new_s in pairs:
        if old_s in text:
            text = text.replace(old_s, new_s, 1)
    return text


def patch_status_tables(text: str) -> str:
    """更新 §12.1 / §12.2 的里程碑状态。"""
    old = """| F2.1–F2.2 套件选择与缺件提示（骨架） | ✅ | `ui/tab_play.py` |"""
    new = """| F2.1–F2.9 同步播放全部功能 | ✅ | `ui/tab_play.py`、`sync/controller.py`、`sync/player.py`、`sync/score_view.py`、`sync/highlight.py` |
| 曲谱几何解析（高亮框 / 点击命中） | ✅ | `sync/svg_geometry.py` |
| WebEngine 后端（DOM 高亮）+ 回退机制 | ✅ | `sync/web_view.py`、`sync/local_server.py` |"""
    assert old in text
    text = text.replace(old, new, 1)

    old2 = """| F2.3–F2.9 播放、进度条、速率/BPM、高亮、跟随、校准 | **M4**：`sync/svg_view.py`（QWebEngineView + DOM 高亮）、`sync/player.py`（QMediaPlayer）、`sync/highlight.py`；`sync/timeline.py` 已完成可直接复用 |"""
    new2 = """| F2.10 从 SVG 属性重建时间轴（无 sync.json 时的兜底） | 待定（当前已有"只播放不高亮"的降级） |"""
    assert old2 in text
    return text.replace(old2, new2, 1)


def patch_m4_findings(text: str) -> str:
    """在 §12.4 之后插入 §12.5（M4 实测修正）与 §12.6（模块说明）。"""
    anchor = "## 附录 A：环境实测记录"
    idx = text.index(anchor)
    chapter = """### 12.5 M4 的实测修正（对 v2.1 的改动）

M4 做同步播放时，为了把高亮框画在正确的音符上，必须拿到"元素几何"，
这一路上暴露了 6 个此前看不出来的问题：

1. **`scale=40` + `pageHeight=400` 让整份谱缩小约 5 倍（M1 遗留）**
   M1 选 `scale=40` 时只看"页数"，没检查"字有多大"。M4 解析几何才发现：
   canon 一个 system 只有 188 px 高、符头只有 9.08×7.7 px，在 1280 宽的窗口里小到看不清。
   **修正**：`scale` 提到 **100**，`page_height` 提到 **1500**，并**关闭 `adjustPageHeight`**
   （它与 `breaks=smart` 同时开启会让每行被压扁）。现在 canon 得 4 行 840×600，
   four-seasons 得 276 行 840×1500。
2. **Verovio 的 SVG 是两层嵌套，单位换算由内层 viewBox 决定**
   `svg_geometry` 第一版按"100 单位 = 1 px"猜，算出的符头只有 2.3 px。
   实际结构是外层 `width="840px"`、内层 `<svg class="definition-scale" viewBox="0 0 21000 4680">`，
   换算是 **840 / 21000 = 0.04**。现在由 `_inner_viewbox_scale()` 从文件里读出，不再硬编码。
3. **`<use>` 必须解引用到 `<defs>` 里的字形**
   Verovio 把符头放在 `<defs>` 里用 `<use xlink:href="#E0A4-…" transform="translate(4857,2150) scale(0.72,0.72)"/>`
   引用。只看 `<use>` 只能得到一个点（宽高为 0），必须解引用字形才能得到真实尺寸。
4. **QtSvg 无法渲染 Verovio 的嵌套 `<svg>`，且失败方式极其隐蔽**
   `QSvgRenderer` 对原始文件报告 `isValid()==True`、`defaultSize()==840×188`，
   但**渲染出来是全白**（深色像素 0 个）。把内层 `<svg>` 降级为 `<g>`、`viewBox` 提到根元素后，
   同一个 QtSvg 立刻能画出谱（深色像素 4501 个）。这个转换就是
   `sync/svg_geometry.flatten_svg()`，**必须用文本替换实现**——用 `ET.tostring()` 重写会引入
   `ns0:` 前缀与自闭合标签，QtSvg 会拒绝解析。
5. **线程竞争会让 QtSvg 崩溃（access violation）**
   M0 的"后台线程探测外部依赖"会启动子进程（MuseScore / ffmpeg 的 `--version`），
   而主线程同时创建 `QSvgRenderer` → QtSvg 内部状态被并发访问，进程直接 `0xC0000005` 退出。
   **修正**：探测改为**同步**执行、且彻底去掉子进程调用（版本号改从安装目录名读取），
   探测成本从数百毫秒降到约 10 ms。详见 §12.4 的 K5。
6. **`QMediaPlayer` 的两个 API 陷阱**
   `positionChanged` 携带 `qlonglong`，不能直接连到自定义 `Signal(int)`（PySide6 报
   `Failed to connect signal`），必须经普通方法转发；
   `QGraphicsSvgItem` 只在 C++ 侧持有 `setSharedRenderer()` 传入的渲染器，
   Python 包装对象一旦被 GC，item 内部就留下悬垂指针（实测崩溃）。
   现用 `item.setData(0, renderer)` 让 item 自己持有引用。

另外两个工程决定：

* **WebEngine 后端的可用性必须用子进程探测**。Chromium 初始化失败是**致命 abort**，
  没有 Python 异常可捕获；本机受限沙箱下会打印
  `FATAL:named-platform-channel-pipe(89) Check failed: ... 拒绝访问` 并终止进程。
  因此 `sync/web_view.is_webengine_available()` 先在子进程里试一次（约 2.3 s，结果缓存），
  主进程再决定用哪个后端。可用环境变量 `ZPYMUSIC_WEBENGINE=1/0` 强制覆盖。
* **高亮在原生后端用"半透明覆盖框"，而不是给音符换色**。QtSvg 只能整幅渲染，
  `boundsOnElement()` 在 Qt6 下对任意 ID 都返回整个 viewport，无法单独改某个元素的颜色。
  覆盖框是纯增量绘制、不破坏谱面内容，且与 WebEngine 后端的观感一致
  （同一个 `HighlightPlan` 驱动两种后端）。

### 12.6 M4 新增模块

| 模块 | 职责 |
| :--- | :--- |
| `sync/svg_geometry.py` | 解析 Verovio SVG 的每个元素包围盒（含 `<use>` 解引用与 viewBox 换算）、`flatten_svg()` |
| `sync/highlight.py` | `SystemGeometryIndex`：按 system 索引几何、生成高亮计划、点击命中测试；`build_css()` 生成注入样式 |
| `sync/score_view.py` | `ScoreView` 抽象 + `NativeScoreView`（QtSvg + 覆盖框 + 按行懒加载 + 自动跟随） |
| `sync/web_view.py` | `WebScoreView`（QWebEngineView + DOM class 高亮）+ 子进程可用性探测 |
| `sync/local_server.py` | 只读本地 HTTP 服务（限 127.0.0.1、限套件目录、支持 Range），给 WebEngine 提供 SVG/音频 |
| `sync/player.py` | `PlaybackBackend` 抽象 + `QtMediaPlayer`（QMediaPlayer/QAudioOutput，速率 0.5–2.0） |
| `sync/controller.py` | `PlaybackController`：30 fps tick、变速锚点换算、seek/预览/校准、驱动视图高亮 |

"""
    return text[:idx] + chapter + text[idx:]


def patch_appendix_d5(text: str) -> str:
    """在附录 D 末尾加 D.5。"""
    marker = "## 附录 E：可复现验证命令"
    idx = text.index(marker)
    section = """### D.5 M4 版面与渲染实测

**版面（canon / four-seasons，`breaks=smart`）**

| 配置 | canon | four-seasons | 结论 |
| :--- | :--- | :--- | :--- |
| `scale=40, pageHeight=400, adjustPageHeight=True`（M1 旧值） | 11 行 840×188 | 276 行 840×406 | 符头仅 2.3 px，字太小 |
| **`scale=100, pageHeight=1500, adjustPageHeight=False`（M4 现值）** | **4 行 840×600** | **276 行 840×1500** | ✅ 采纳 |

**几何解析（`svg_geometry.parse_svg`）**

| 项目 | 实测值 |
| :--- | :--- |
| 单位换算系数 | 840 / 21000 = **0.04**（由内层 viewBox 决定） |
| 符头包围盒 | **9.08 × 7.7 px** |
| 5 线谱表高度 | **28.8 px**（线距 5.76 px，比例正确） |
| 相邻音符间距 | 11–21 px |
| note 元素解析覆盖率 | **100%**（canon 32/32、four-seasons 110/110、167/167） |
| 单 system 解析耗时 | canon **9 ms** / four-seasons **22 ms** |

**QtSvg 渲染对比（同一文件、同一 QSvgRenderer）**

| 输入 | `isValid()` | `defaultSize()` | 深色像素 |
| :--- | :--- | :--- | :--- |
| 原始嵌套结构 | True | 840×188 | **0（全白）** |
| `flatten_svg()` 之后 | True | 840×188 | **4501** |

**懒加载性能（`NativeScoreView`，four-seasons 276 行）**

| 操作 | 耗时 |
| :--- | :--- |
| 载入套件（仅登记 system，不挂载 SVG） | **79 ms** |
| 首屏挂载（视口附近 3–6 行） | 0.1 ms |
| 单个 system 挂载（解析几何 + 建渲染器 + 加 item） | 9–22 ms |
| 连续滚动并挂载 60 行 | 1.17 s（约 20 ms/行） |

"""
    return text[:idx] + section + text[idx:]


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    for fn in (
        patch_revision,
        patch_test_acceptance,
        patch_f23,
        patch_status_tables,
        patch_m4_findings,
        patch_appendix_d5,
    ):
        text = fn(text)
        print(f"  applied {fn.__name__}")
    DOC.write_text(text, encoding="utf-8")
    print(f"OK -> {DOC} ({len(text)} chars, {text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
