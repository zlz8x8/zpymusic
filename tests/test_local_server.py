"""本地资源服务的路由回归（需求 §5.3 / D5）。

盯的是行 SVG 的 **URL 约定**：``SystemRef.file`` 是"相对套件目录"的路径
（套件里就是 ``svg/sys-0001.svg``），因此页面按 ``/sys/<相对路径>`` 请求它，
服务端也按同一条相对路径落到 ``<root>/<相对路径>``。

历史事故：页面只取 ``file`` 的**文件名**拼成 ``/svg/<文件名>``，而预览窗口把行 SVG
平铺写在缓存根目录（不是 ``<cache>/svg/``）——于是源文件预览整屏
"行加载失败：原因=HTTP 404"，同时日志里还写着"MusicXML 预览完成：… 12 行"。

这些用例**不需要 Qt / WebEngine**：只起一个只读 HTTP 服务，用 urllib 请求。
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from zpymusic.sync.local_server import LocalAssetServer

_SVG = '<svg width="100px" height="50px" xmlns="http://www.w3.org/2000/svg"></svg>'


@pytest.fixture()
def served(tmp_path: Path):
    """一个最小套件布局：``<root>/svg/sys-0001.svg`` + 根目录下的 ``audio.mp3``。"""
    (tmp_path / "svg").mkdir()
    (tmp_path / "svg" / "sys-0001.svg").write_text(_SVG, encoding="utf-8")
    (tmp_path / "audio.mp3").write_bytes(b"ID3")
    srv = LocalAssetServer(tmp_path)
    base = srv.start()
    try:
        yield base, tmp_path
    finally:
        srv.stop()


def _status(url: str) -> int:
    """请求一下，返回状态码（不抛 HTTPError）。"""
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def test_sys_route_resolves_relative_path(served) -> None:
    """``/sys/svg/sys-0001.svg`` → ``<root>/svg/sys-0001.svg``。"""
    base, root = served
    with urllib.request.urlopen(f"{base}/sys/svg/sys-0001.svg", timeout=10) as r:
        assert r.status == 200
        assert r.headers["Content-Type"] == "image/svg+xml"
        assert b"<svg" in r.read(2000)
    assert (root / "svg" / "sys-0001.svg").is_file()


def test_sys_route_accepts_root_level_file(served) -> None:
    """行 SVG 直接躺在根目录（旧版预览缓存的布局）时也要能取到。"""
    base, root = served
    (root / "sys-0002.svg").write_text(_SVG, encoding="utf-8")
    assert _status(f"{base}/sys/sys-0002.svg") == 200


def test_sys_route_rejects_escaping_path(served, tmp_path: Path) -> None:
    """``..`` 越界必须被拒（不能读到套件目录之外的文件）。"""
    base, root = served
    outside = root.parent / "不该被读到.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        assert _status(f"{base}/sys/../%E4%B8%8D%E8%AF%A5%E8%A2%AB%E8%AF%BB%E5%88%B0.txt") == 404
    finally:
        outside.unlink(missing_ok=True)


def test_sys_route_rejects_non_whitelisted_suffix(served) -> None:
    """只有白名单扩展名可取（``.py`` 之类一律 404）。"""
    base, root = served
    (root / "evil.py").write_text("print(1)", encoding="utf-8")
    assert _status(f"{base}/sys/evil.py") == 404


def test_legacy_svg_route_still_works(served) -> None:
    """``/svg/<名字>`` 是套件播放页一直使用的旧路由，不能因为新增 /sys/ 而失效。"""
    base, _root = served
    assert _status(f"{base}/svg/sys-0001.svg") == 200
    # 不存在的文件 → 404（中文名要按 URL 规则编码，否则 http.client 直接报错）
    missing = urllib.parse.quote("不存在.svg")
    assert _status(f"{base}/svg/{missing}") == 404
