"""配置读写：``<project_root>/config.json``。

契约见 docs/requirements.md §3.3。首次运行写出默认值；缺失键用默认值补齐（前向兼容）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .paths import CONFIG_FILE, MUSICXML_DIR, SUITES_DIR

__all__ = ["PathsConfig", "AudioConfig", "RenderConfig", "UiConfig", "AppConfig", "load_config"]

# 默认音色库：MIT 授权、体积与质量平衡最好（需求 §3.4 / Q5）
DEFAULT_SOUNDFONT = "sound/MuseScore_General.sf3"


@dataclass
class PathsConfig:
    """外部工具路径。``None`` 表示"自动探测"。"""

    musescore: str | None = None
    ffmpeg: str | None = None
    fluidsynth_dir: str | None = None
    verovio_resources: str | None = None

    def resolved(self) -> "PathsConfig":
        """把 ``None`` 的字段替换为自动探测结果。"""
        from .deps import default_ffmpeg, default_fluidsynth_dir, default_musescore

        return PathsConfig(
            musescore=self.musescore or (str(default_musescore()) if default_musescore() else None),
            ffmpeg=self.ffmpeg or (str(default_ffmpeg()) if default_ffmpeg() else None),
            fluidsynth_dir=self.fluidsynth_dir
            or (str(default_fluidsynth_dir()) if default_fluidsynth_dir() else None),
            verovio_resources=self.verovio_resources,
        )


@dataclass
class AudioConfig:
    """音频渲染参数（需求 §3.4 / Q3 / Q4）。"""

    default_soundfont: str = DEFAULT_SOUNDFONT
    sample_rate: int = 44100
    gain: float = 0.6
    reverb: bool = True
    chorus: bool = True
    keep_wav: bool = False
    mp3_bitrate_kbps: int = 192

    def soundfont_path(self, project_root: Path) -> Path:
        p = Path(self.default_soundfont)
        return p if p.is_absolute() else project_root / p


@dataclass
class RenderConfig:
    """SVG 渲染参数（需求 §5.1.2 / Q2）。

    版面策略（M0 实测选定，见 docs/requirements.md 附录 A 的布局试验）：

    * ``breaks="smart"`` + ``page_height=400`` + ``adjust_page_height=True``
      → Verovio 把**每一个 system（一行谱）渲染成一个高度贴合内容的 SVG**：
        canon 得 11 个 840x186，four-seasons 得 276 个 840x406。
      宽度恒定、高度自适应，非常适合"按行懒加载 + 自动滚动跟随"。
    * 若 ``single_page=True``（需求 Q2 的"单页连续"字面实现）则改用
      ``breaks="none"`` + 巨大 ``page_height``：小曲目确实是 1 页，
      但 four-seasons 会输出 167972x568 的单页 SVG（23 MB / 渲染 16.7 s），
      **不推荐**，仅在导出或打印场景使用。
    """

    engine: str = "verovio"
    scale: int = 100
    page_width: int = 2100
    breaks: str = "smart"
    """``smart`` = 一行一个 SVG（默认，推荐）；``none`` = 不自动换行；``auto``/``line`` 备选。"""
    page_height: int = 1500
    """只在 ``breaks != "none"`` 时作为"每页最多容纳高度"的提示值。

    M1 曾用 400，会让 Verovio 把每个 system 压得只有 188 px 高（"每行很小"）。
    M4 修正为 1500：canon 得 4 个 system、每个 840×600，four-seasons 得 276 个 840×1500。
    """
    adjust_page_height: bool = True
    """让 SVG 高度**贴合内容**，避免页面下方大片空白。

    M4 处理"横向太宽/留白过多"时重新实测，**修正了 M0 的错误结论**：
    当时认为"`adjustPageHeight=True` 会把乐谱整体缩小"，其实那是因为当时
    `scale=40` + `pageHeight=400` 共同造成的（页面高度不足以放下一个 system，
    Verovio 只能整体缩放）。在 `scale=100` 下实测：

    | 配置 | rachmaninoff | canon | 符头宽 |
    | :--- | :--- | :--- | :--- |
    | `pageHeight=1500, 不贴合` | 2100×1500 | 2100×1500（空白 390px） | 22.7 px |
    | **`pageHeight=1500, 贴合`** | **2100×1439** | **2100×1260** | **22.7 px** |

    即：开启后高度贴合内容、**字号完全不变**。因此默认开启。
    """
    single_page: bool = False
    """True 时改用单页连续纵向（小曲目 1 页；大曲目会产生超大 SVG）。"""
    max_page_height: int = 60000
    """Verovio 对 pageHeight 的上限（实测 60000）。"""
    svg_additional_attributes: list[str] = field(
        default_factory=lambda: ["note@pname", "note@oct", "note@dur"]
    )
    footer: str = "none"
    header: str = "none"
    include_rests_in_sync: bool = False
    """是否把休止符写进 sync.json（需求 §5.1.4）。"""
    max_systems: int = 0
    """0 = 不限制；>0 时只渲染前 N 个 system（调试用）。"""


@dataclass
class UiConfig:
    """界面偏好（需求 §5.5）。"""

    tab_position: str = "west"
    highlight_color: str = "#FF8C00"
    highlight_opacity: float = 0.35
    follow_playback: bool = True
    score_backend: str = "auto"
    """曲谱视图后端：``auto``（默认）| ``native`` | ``web``。

    * ``auto`` —— **默认**。优先 WebEngine（DOM 高亮，缩放/滚动/命中测试由浏览器负责），
      失败自动回退原生，永不失败。可用性由 ``sync/web_view.is_webengine_available()``
      用子进程探测决定（先测默认/GPU 配置，只有"建不出 GL 上下文"时才降级软件渲染）。
    * ``native`` —— 强制 QtSvg 原生渲染（半透明高亮框），零 Chromium 依赖。
    * ``web`` —— 强制 WebEngine，失败则明确报错（便于排障）。

    Note:
        旧版本默认 ``native``，并在启动时无条件注入 ``--disable-gpu`` /
        ``QT_OPENGL=software`` / ``QT_QUICK_BACKEND=software``。M6 实测证明
        那套注入**自身**就会产生 ``Failed to create GLES3 context`` /
        ``ContextResult::kFatalFailure`` 两条报错（同时真的关掉了 GPU 加速），
        因此默认值改为 ``auto`` 且不再注入软件渲染（见 ``sync/web_view.py`` 模块文档）。
    """

    skip_fidelity_prompts: list[str] = field(default_factory=list)
    """已勾选"不再提示"的保真度提示（F3.3），元素形如 ``"musicxml→mp3"``。"""

    convert_name_template: str = "{base}"
    """格式转换的命名模板（F3.7）：``{base}`` / ``{format}`` / ``{ext}`` / ``{date}``。"""


@dataclass
class AppConfig:
    """应用总配置。"""

    paths: PathsConfig = field(default_factory=PathsConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    musicxml_dir: str = str(MUSICXML_DIR)
    suites_dir: str = str(SUITES_DIR)
    overwrite_policy: str = "skip"
    """``skip`` | ``overwrite`` | ``newdir``（需求 F1.10）。"""

    # ---------------------------------------------------------------- 序列化
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """从 dict 构造；忽略未知键，缺失键用默认值（前向 / 向后兼容）。"""
        return cls(
            paths=_build(PathsConfig, data.get("paths")),
            audio=_build(AudioConfig, data.get("audio")),
            render=_build(RenderConfig, data.get("render")),
            ui=_build(UiConfig, data.get("ui")),
            musicxml_dir=data.get("musicxml_dir", str(MUSICXML_DIR)),
            suites_dir=data.get("suites_dir", str(SUITES_DIR)),
            overwrite_policy=data.get("overwrite_policy", "skip"),
        )

    # ---------------------------------------------------------------- 读写
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        p = Path(path) if path else CONFIG_FILE
        if not p.exists():
            cfg = cls()
            cfg.save(p)
            return cfg
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # 配置损坏时退回默认值，不阻断启动（需求 §7.2）
            return cls()
        return cls.from_dict(raw if isinstance(raw, dict) else {})

    def save(self, path: Path | None = None) -> Path:
        p = Path(path) if path else CONFIG_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return p

    def suites_path(self) -> Path:
        return Path(self.suites_dir)

    def musicxml_path(self) -> Path:
        return Path(self.musicxml_dir)


def _build(dc_type: type, data: Any) -> Any:
    """用 ``data`` 中已知字段构造 dataclass，容忍未知 / 缺失键。"""
    if not isinstance(data, dict):
        return dc_type()
    known = {f.name for f in fields(dc_type)}
    return dc_type(**{k: v for k, v in data.items() if k in known})


def load_config(path: Path | None = None) -> AppConfig:
    """便捷函数：读取配置。"""
    return AppConfig.load(path)
