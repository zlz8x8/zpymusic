"""zpyMusic 命令行入口（需求 Q7 / §6）。

命令行入口的意义：让 M1 的核心链路在**没有 GUI** 的情况下就能跑通和被测试，
从而在 M2 做界面之前先把数据正确性锁死（需求 §10.2 第 1 条）。

用法::

    python -m zpymusic probe                       # 环境自检
    python -m zpymusic inspect <file|suite目录>     # 查看乐谱/套件信息
    python -m zpymusic generate <源...> [选项]      # 生成套件
    python -m zpymusic validate <suite目录>         # 校验套件完整性
    python -m zpymusic list                         # 列出已有套件
    python -m zpymusic convert <源> -t <格式>       # 格式转换（M5：与 GUI 共用引擎）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .common.config import AppConfig
from .common.deps import probe_all
from .common.errors import ZpyMusicError
from .common.log import get_logger, setup_logging
from .common.paths import PROJECT_ROOT
from .core import midi_tools
from .core.convert import (
    CONVERSIONS,
    SOURCE_FORMATS,
    TARGET_FORMATS,
    ConvertOptions,
    check_environment,
    convert_one,
    detect_format,
    fidelity_note,
    missing_requirements,
)
from .core.gm import gm_program_name
from .core.musicxml_io import iter_source_files, read_musicxml
from .core.pipeline import GenerateOptions, generate_suite
from .core.suite import OverwritePolicy, Suite, discover_suites, make_layout
from .core.sync_model import SyncDoc

log = get_logger("zpymusic.cli")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

#: 能力矩阵与格式判定都放在 :mod:`zpymusic.core.convert`（GUI 与 CLI 共用同一份实现）。
#: 这里保留同名符号，避免既有导入路径（``from zpymusic.cli import CONVERSIONS``）失效。
__all__ = [
    "CONVERSIONS",
    "SOURCE_FORMATS",
    "TARGET_FORMATS",
    "detect_format",
    "cmd_convert",
    "main",
]


# --------------------------------------------------------------------------- 子命令
def cmd_probe(args: argparse.Namespace) -> int:
    """环境自检（M0 交付物）。"""
    print(f"zpyMusic {__version__}")
    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Python    : {sys.version.split()[0]} ({sys.executable})")
    print()
    report = probe_all()
    for line in report.summary_lines():
        print("  " + line)
    print()
    if report.missing_required:
        print("结论：核心依赖缺失，无法生成套件：")
        for d in report.missing_required:
            print(f"  - {d.name}: {d.hint}")
        return EXIT_FAIL
    missing_opt = [d for d in report.warnings if not d.required]
    if missing_opt:
        print("结论：核心依赖齐备；以下可选能力不可用：")
        for d in missing_opt:
            print(f"  - {d.name}: {d.hint}")
    else:
        print("结论：全部依赖可用。")
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    """查看 MusicXML 源或套件的信息。"""
    target = Path(args.target)
    if target.is_dir():
        suites = discover_suites(target)
        if not suites:
            # 可能直接指向某个套件目录
            syncs = sorted(target.glob("*.sync.json"))
            if syncs:
                suites = [Suite(make_layout(target.parent, syncs[0].name[: -len(".sync.json")]))]
        if not suites:
            print(f"目录中未找到套件：{target}", file=sys.stderr)
            return EXIT_FAIL
        for s in suites:
            _print_suite(s)
        return EXIT_OK

    if target.suffix.lower() == ".sync.json":
        sync = SyncDoc.load(target)
        _print_sync(sync, target)
        return EXIT_OK

    if not target.is_file():
        print(f"路径不存在：{target}", file=sys.stderr)
        return EXIT_FAIL

    fmt = detect_format(target)
    if fmt == "musicxml":
        doc = read_musicxml(target)
        print(f"源文件   : {doc.source}")
        print(f"主名     : {doc.base_name}")
        print(f"标题     : {doc.title}")
        print(f"作曲     : {doc.composer or '（未标注）'}")
        print(f"格式     : {doc.root_tag} (MusicXML version={doc.version or '?'})")
        print(f"声部数   : {doc.part_count}")
        for p in doc.parts:
            flag = "" if p.program_was_explicit else "  ← 未指定音色"
            print(
                f"  - {p.id:8s} {p.name[:28]:30s} ch={p.midi_channel or '?':>2} "
                f"prog={p.program_display}{flag}"
            )
        for w in doc.warnings:
            print(f"警告     : {w}")
        return EXIT_OK
    if fmt == "midi":
        summary = midi_tools.summarize_midi(target)
        print(f"MIDI     : {target}")
        print(f"轨道数   : {summary.track_count}   ticks/beat={summary.ticks_per_beat}")
        for c in summary.channels:
            name = gm_program_name(c.program) if c.program is not None else "（未指定 → 钢琴）"
            perc = "  [打击乐]" if c.is_percussion else ""
            print(
                f"  - ch{c.channel_1based:>2}  prog={c.program if c.program is not None else '--':>3} "
                f"{name[:28]:30s} notes={c.note_count}{perc}"
            )
        for w in summary.warnings:
            print(f"警告     : {w}")
        return EXIT_OK

    print(f"暂不支持查看该格式：{fmt or target.suffix}", file=sys.stderr)
    return EXIT_USAGE


def _print_suite(s: Suite) -> None:
    problems = s.validate()
    print(f"套件     : {s.base}")
    print(f"  目录   : {s.dir}")
    if s.has_sync:
        try:
            doc = s.load_sync()
        except ZpyMusicError as e:
            print(f"  sync   : 读取失败 —— {e}")
        else:
            _print_sync(doc, s.layout.sync, indent="  ")
    else:
        print("  sync   : 缺失")
    print(f"  SVG    : {len(list(s.layout.svg_dir.glob('*.svg'))) if s.has_svg else 0} 个")
    print(f"  音频   : {', '.join(p.name for p in s.audio_files) or '（无）'}")
    print(f"  完整性 : {'通过' if not problems else '有问题'}")
    for p in problems:
        print(f"    ! {p}")


def _print_sync(sync: SyncDoc, path: Path, indent: str = "") -> None:
    print(f"{indent}sync     : {path.name}  schema={sync.schema}")
    print(f"{indent}  标题   : {sync.source.get('title', '')}")
    print(
        f"{indent}  规模   : {sync.note_count} 音符 / {len(sync.systems)} system / "
        f"{len(sync.measures)} 小节 / {sync.duration_ms/1000:.1f}s"
    )
    print(f"{indent}  引擎   : {sync.render.get('engine')} {sync.render.get('engine_version', '')}")
    print(f"{indent}  音色库 : {sync.audio.get('soundfont', '')}")
    bpm = sync.tempo.get("initial_bpm")
    print(f"{indent}  速度   : {bpm or '?'} BPM")
    for p in sync.parts:
        guess = "  ← 猜测值" if p.get("program_guessed") else ""
        missing = "" if p.get("explicit_program") else "  ← 源文件未指定"
        print(
            f"{indent}  - {str(p.get('id'))[:8]:8s} {str(p.get('name'))[:24]:26s} "
            f"ch={p.get('midi_channel'):>2} prog={p.get('midi_program'):>3} "
            f"{str(p.get('program_name'))[:24]}{guess}{missing}"
        )


def cmd_generate(args: argparse.Namespace) -> int:
    """生成套件（M1 的核心交付物）。"""
    config = AppConfig.load()
    if args.suites_dir:
        config.suites_dir = str(Path(args.suites_dir))
    if args.soundfont:
        config.audio.default_soundfont = str(Path(args.soundfont))
    if args.keep_wav:
        config.audio.keep_wav = True

    sources: list[Path] = []
    for raw in args.sources:
        p = Path(raw)
        if p.is_dir():
            sources.extend(iter_source_files(p))
        elif p.is_file():
            sources.append(p)
        else:
            print(f"跳过不存在的路径：{p}", file=sys.stderr)
    if not sources:
        print("没有可处理的源文件", file=sys.stderr)
        return EXIT_USAGE

    options = GenerateOptions(
        suites_root=Path(config.suites_dir),
        overwrite=args.overwrite,
        keep_wav=config.audio.keep_wav,
        render_svg=not args.no_svg,
        render_midi=not args.no_midi,
        render_audio=not args.no_audio,
        encode_mp3=not args.no_mp3,
        copy_musicxml=not args.no_copy,
        verify=not args.no_verify,
        soundfont=Path(args.soundfont) if args.soundfont else None,
    )

    def progress(stage: str, done: int, total: int) -> None:
        if args.quiet:
            return
        pct = done * 100 // max(1, total)
        end = "\n" if done >= total else "\r"
        print(f"  {stage}: {done}/{total} ({pct}%)", end=end, flush=True)

    def message(level: str, text: str) -> None:
        if args.quiet and level == "info":
            return
        prefix = {"info": "  ", "warning": "  ! ", "error": "  x "}.get(level, "  ")
        print(f"{prefix}{text}")

    results = []
    failures = 0
    for i, src in enumerate(sources, 1):
        print(f"\n[{i}/{len(sources)}] {src.name}")
        try:
            res = generate_suite(src, config, options, progress=progress, message=message)
        except KeyboardInterrupt:
            print("\n已中断", file=sys.stderr)
            return EXIT_FAIL
        results.append(res)
        if not res.ok:
            failures += 1

    if args.json:
        print(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*70}")
        ok = sum(1 for r in results if r.ok)
        skipped = sum(1 for r in results if r.status == "skipped")
        print(f"完成：成功 {ok}，跳过 {skipped}，失败 {failures}，共 {len(results)}")
        for r in results:
            mark = {"ok": "OK ", "skipped": "-- ", "failed": "XX "}.get(r.status, "?  ")
            print(f"  {mark}{r.summary()}")
            if r.suite_dir and r.ok:
                print(f"      → {r.suite_dir}")
    return EXIT_FAIL if failures else EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    """校验套件完整性（需求 §7.2 / §8.2）。"""
    root = Path(args.suite_dir)
    suites = discover_suites(root)
    if not suites and (root / "svg").is_dir():
        syncs = sorted(root.glob("*.sync.json"))
        if syncs:
            suites = [Suite(make_layout(root.parent, syncs[0].name[: -len(".sync.json")]))]
    if not suites:
        print(f"未找到套件：{root}", file=sys.stderr)
        return EXIT_FAIL
    bad = 0
    for s in suites:
        problems = s.validate(strict=args.strict)
        if problems:
            bad += 1
            print(f"XX {s.base}")
            for p in problems:
                print(f"     - {p}")
        else:
            print(f"OK {s.base}")
    print(f"\n共 {len(suites)} 个套件，{bad} 个有问题")
    return EXIT_FAIL if bad else EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    """列出已有套件。"""
    config = AppConfig.load()
    root = Path(args.suites_dir or config.suites_dir)
    suites = discover_suites(root)
    if not suites:
        print(f"套件根目录为空：{root}")
        return EXIT_OK
    print(f"套件根目录：{root}")
    for s in suites:
        dur = ""
        notes = ""
        if s.has_sync:
            try:
                doc = s.load_sync()
                dur = f"{doc.duration_ms/1000:7.1f}s"
                notes = f"{doc.note_count:6d} 音符"
            except ZpyMusicError:
                pass
        svg = len(list(s.layout.svg_dir.glob("*.svg"))) if s.has_svg else 0
        audio = "+".join(p.suffix.lstrip(".") for p in s.audio_files) or "-"
        print(f"  {s.base[:40]:42s} {notes} {dur}  svg={svg:4d}  audio={audio}")
    return EXIT_OK


def cmd_convert(args: argparse.Namespace) -> int:
    """格式转换（需求 §5.4）。与 GUI「格式转换」页共用 :mod:`zpymusic.core.convert`。"""
    src = Path(args.source)
    config = AppConfig.load()
    if args.soundfont:
        config.audio.default_soundfont = str(Path(args.soundfont))

    options = ConvertOptions(
        target=args.to.lower(),
        out_dir=Path(args.out_dir) if args.out_dir else None,
        name_template=args.name_template,
        overwrite=args.overwrite,
        soundfont=Path(args.soundfont) if args.soundfont else None,
        quantize=not args.no_quantize,
        keep_wav=bool(args.keep_wav),
        also_svg=bool(args.also_svg),
    )

    sfmt = detect_format(src)
    note = fidelity_note(sfmt, options.target)
    if note and not args.quiet:
        print(f"提示：{note}", file=sys.stderr)
    env = check_environment(config)
    missing = missing_requirements(sfmt, options.target, config, env)
    if missing and not args.quiet:
        for r in missing:
            print(f"缺少依赖：{r.name} —— {r.hint}", file=sys.stderr)

    def progress(stage: str, done: int, total: int) -> None:
        if args.quiet:
            return
        print(f"  {stage}: {done}/{total}", end="\r", flush=True)

    result = convert_one(src, options, config, progress=progress, env=env)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        mark = {"ok": "OK ", "skipped": "-- ", "failed": "XX ", "cancelled": ".. "}.get(
            result.status, "?  "
        )
        print(f"{mark}{result.summary()}")
        for p in result.outputs:
            print(f"      → {p}")
        for w in result.warnings:
            print(f"      ! {w}")
        if result.status == "failed":
            print(f"      原因：{result.message}", file=sys.stderr)
    return EXIT_OK if result.ok or result.status == "skipped" else EXIT_FAIL


def cmd_conversions(args: argparse.Namespace) -> int:
    """打印能力矩阵（需求 §5.4.1）。"""
    print(f"{'源':10s} {'目标':10s} {'优先级':6s} 实现方式")
    for (s, t), (prio, how) in sorted(CONVERSIONS.items(), key=lambda kv: (kv[1][0], kv[0])):
        print(f"{s:10s} {t:10s} {prio:6s} {how}")
    return EXIT_OK


# --------------------------------------------------------------------------- 参数解析
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zpymusic",
        description="zpyMusic —— MusicXML 曲谱转换与 SVG 同步播放工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"zpyMusic {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("probe", help="环境自检（依赖探测）")
    sp.set_defaults(func=cmd_probe)

    sp = sub.add_parser("inspect", help="查看 MusicXML 源 / 套件 / sync.json 信息")
    sp.add_argument("target", help="文件或套件目录")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("generate", help="生成套件（MusicXML → svg/midi/mp3/sync.json）")
    sp.add_argument("sources", nargs="+", help="源文件或目录")
    sp.add_argument("-o", "--suites-dir", help="套件根目录（默认取配置 staff/suites）")
    sp.add_argument("-s", "--soundfont", help="音色库 .sf2/.sf3 路径")
    sp.add_argument(
        "--overwrite", choices=list(OverwritePolicy.ALL), default=OverwritePolicy.SKIP,
        help="已存在时的策略（默认 skip）",
    )
    sp.add_argument("--keep-wav", action="store_true", help="保留中间 WAV")
    sp.add_argument("--no-svg", action="store_true", help="不渲染 SVG")
    sp.add_argument("--no-midi", action="store_true", help="不写 MIDI")
    sp.add_argument("--no-audio", action="store_true", help="不渲染 WAV/MP3")
    sp.add_argument("--no-mp3", action="store_true", help="只渲染 WAV，不编码 MP3")
    sp.add_argument("--no-copy", action="store_true", help="不复制源 MusicXML")
    sp.add_argument("--no-verify", action="store_true", help="跳过一致性自检")
    sp.add_argument("-q", "--quiet", action="store_true", help="减少输出")
    sp.add_argument("--json", action="store_true", help="结果以 JSON 输出")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("validate", help="校验套件完整性")
    sp.add_argument("suite_dir", help="套件根目录或单个套件目录")
    sp.add_argument("--strict", action="store_true", help="把缺少 MP3 也视为问题")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("list", help="列出已有套件")
    sp.add_argument("-o", "--suites-dir", help="套件根目录")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("convert", help="格式转换")
    sp.add_argument("source", help="源文件")
    sp.add_argument("-t", "--to", required=True, help="目标格式（svg/midi/mp3/wav/pdf/musicxml）")
    sp.add_argument("-o", "--out-dir", help="输出目录（默认与源文件同目录）")
    sp.add_argument("-s", "--soundfont", help="音色库路径")
    sp.add_argument("--also-svg", action="store_true", help="转换到 midi/mp3 时同时输出 SVG")
    sp.add_argument("--keep-wav", action="store_true", help="musicxml → mp3 时保留中间 WAV")
    sp.add_argument("--no-quantize", action="store_true", help="midi → musicxml 不做量化")
    sp.add_argument(
        "--name-template",
        default="{base}",
        help="命名模板，可用 {base} {format} {ext} {date}（默认 {base}）",
    )
    sp.add_argument(
        "--overwrite", choices=list(OverwritePolicy.ALL), default=OverwritePolicy.SKIP
    )
    sp.add_argument("-q", "--quiet", action="store_true")
    sp.add_argument("--json", action="store_true", help="结果以 JSON 输出")
    sp.set_defaults(func=cmd_convert)

    sp = sub.add_parser("conversions", help="打印格式转换能力矩阵")
    sp.set_defaults(func=cmd_conversions)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import logging  # noqa: PLC0415

    setup_logging(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return int(args.func(args))
    except ZpyMusicError as e:
        print(f"错误：{e}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
