"""
2game.info から xTranslator XML 翻訳を取得し、CSV に適用する。
"""
from __future__ import annotations

import configparser
import os
import re
import shutil
import sys
import threading
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Iterable
from urllib.error import URLError, HTTPError
from urllib.parse import urljoin, urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPCookieProcessor

import pandas as pd

from src.app_logger import get_logger

TWOGAME_BASE = "https://skyrimspecialedition.2game.info"
DETAIL_URL_TEMPLATE = TWOGAME_BASE + "/detail.php?id={mod_id}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_cache_locks_guard = threading.Lock()
_cache_locks: dict[str, threading.Lock] = {}
_py7zz_binary_ready = False


def _lock_for_mod(mod_id: str) -> threading.Lock:
    with _cache_locks_guard:
        if mod_id not in _cache_locks:
            _cache_locks[mod_id] = threading.Lock()
        return _cache_locks[mod_id]


def ensure_py7zz_binary() -> None:
    """
    PyInstaller 実行時に py7zz が 7zz を見つけられるよう PY7ZZ_BINARY を設定する。
    onefile では __file__ 基準の bin/ が見えないため必須。
    """
    global _py7zz_binary_ready
    if _py7zz_binary_ready:
        return

    log = get_logger()
    existing = os.environ.get("PY7ZZ_BINARY")
    if existing and Path(existing).exists():
        log.debug("PY7ZZ_BINARY already set: %s", existing)
        _py7zz_binary_ready = True
        return

    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                meipass / "py7zz" / "bin" / "7zz.exe",
                meipass / "bin" / "7zz.exe",
                meipass / "7zz.exe",
                exe_dir / "py7zz" / "bin" / "7zz.exe",
                exe_dir / "bin" / "7zz.exe",
                exe_dir / "7zz.exe",
            ]
        )
    else:
        try:
            import py7zz

            pkg_bin = Path(py7zz.__file__).resolve().parent / "bin" / "7zz.exe"
            candidates.append(pkg_bin)
        except Exception:
            pass

    for candidate in candidates:
        if candidate.exists():
            os.environ["PY7ZZ_BINARY"] = str(candidate)
            # 7z.dll 探索のため bin ディレクトリを PATH 先頭へ
            bin_dir = str(candidate.parent)
            path_now = os.environ.get("PATH", "")
            if bin_dir not in path_now.split(os.pathsep):
                os.environ["PATH"] = bin_dir + os.pathsep + path_now
            log.info("PY7ZZ_BINARY set: %s", candidate)
            _py7zz_binary_ready = True
            return

    log.warning(
        "7zz binary not found for py7zz. candidates tried=%s",
        [str(c) for c in candidates],
    )
    _py7zz_binary_ready = True  # 再探索しても無駄なので一度きり


class TwoGameDownloadError(Exception):
    """ダウンロード失敗・サイト不通など、翻訳処理全体を中断すべきエラー。"""


@dataclass
class XmlTranslationEntry:
    editor_id: str
    record_type: str
    index: int
    source: str
    dest: str


def read_mod_id_from_meta(plugin_path: Path) -> str | None:
    """プラグイン所在 Mod フォルダの meta.ini から modid を取得する。"""
    meta_path = plugin_path.parent.joinpath("meta.ini")
    if not meta_path.exists():
        return None

    parser = configparser.ConfigParser()
    try:
        parser.read(meta_path, encoding="utf-8")
    except Exception:
        # MO2 meta.ini は稀に非標準だが、通常は UTF-8/CP932
        try:
            parser.read(meta_path, encoding="cp932")
        except Exception:
            return None

    if not parser.has_section("General"):
        return None

    mod_id = parser["General"].get("modid", "").strip()
    if not mod_id or mod_id == "0":
        return None
    return mod_id


def expected_xml_filename(plugin_path: Path) -> str:
    """Something.esp → Something_english_japanese.xml"""
    return f"{plugin_path.stem}_english_japanese.xml"


def normalize_record_type(rec: str) -> str:
    """QUST:NNAM / QUST NNAM → 'QUST NNAM'"""
    text = (rec or "").strip()
    if not text:
        return ""
    if ":" in text:
        left, right = text.split(":", 1)
        return f"{left.strip()} {right.strip()}"
    return re.sub(r"\s+", " ", text)


def normalize_editor_id(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() == "null":
        return "null"
    return text


def _http_get(opener, url: str, referer: str | None = None) -> tuple[bytes, str]:
    log = get_logger()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "ja,en;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    req = Request(url, headers=headers)
    log.debug("HTTP GET begin: %s", url)
    try:
        with opener.open(req, timeout=60) as resp:
            data = resp.read()
            final_url = resp.geturl()
            log.debug(
                "HTTP GET done: %s -> %s bytes=%s",
                url,
                final_url,
                len(data),
            )
            return data, final_url
    except (URLError, HTTPError, TimeoutError, OSError) as e:
        log.error("HTTP GET failed: %s / %s", url, e)
        raise TwoGameDownloadError(f"HTTP request failed: {url} / {e}") from e


def fetch_jp_download_links(mod_id: str) -> list[str]:
    """
    詳細ページから jp_download.php リンクをページ表示順（上＝新しい）で返す。
    絶対 URL のリスト。
    """
    log = get_logger()
    detail_url = DETAIL_URL_TEMPLATE.format(mod_id=mod_id)
    log.info("2game detail fetch begin: modid=%s url=%s", mod_id, detail_url)
    cookie_jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    html_bytes, _ = _http_get(opener, detail_url)
    try:
        html = html_bytes.decode("utf-8")
    except UnicodeDecodeError:
        html = html_bytes.decode("cp932", errors="replace")

    # ページ上の出現順を維持。&amp; を正規化。
    raw_links = re.findall(
        r"(?:https?:)?//skyrimspecialedition\.2game\.info/jp_download\.php\?[^\"'\s<>]+"
        r"|/jp_download\.php\?[^\"'\s<>]+"
        r"|jp_download\.php\?[^\"'\s<>]+",
        html,
        flags=re.IGNORECASE,
    )

    ordered: list[str] = []
    seen: set[str] = set()
    for raw in raw_links:
        link = raw.replace("&amp;", "&").strip()
        if link.startswith("//"):
            absolute = "https:" + link
        elif link.startswith("http://") or link.startswith("https://"):
            absolute = link
        else:
            absolute = urljoin(TWOGAME_BASE + "/", link.lstrip("/"))
        if "jp_download.php" not in absolute:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        ordered.append(absolute)

    log.info("2game detail fetch done: modid=%s links=%s", mod_id, len(ordered))
    for i, link in enumerate(ordered):
        log.debug("  link[%s]=%s", i, link)
    return ordered


def _file_id_from_url(url: str) -> str:
    qs = parse_qs(urlparse(url).query)
    file_ids = qs.get("file_id") or qs.get("id")
    if file_ids:
        # file_id 優先。query に id=modid もあるので file_id を使う
        if "file_id" in qs:
            return qs["file_id"][0]
    # フォールバック
    m = re.search(r"file_id=(\d+)", url)
    if m:
        return m.group(1)
    return re.sub(r"[^\w.-]+", "_", urlparse(url).query)[:64] or "unknown"


def _detect_archive_format(data: bytes) -> str:
    """マジックバイトからアーカイブ形式を判定する。"""
    if len(data) >= 2 and data[:2] == b"PK":
        return "zip"
    if len(data) >= 6 and data[:6] == b"7z\xbc\xaf\x27\x1c":
        return "7z"
    # RAR 1.5–4 / RAR 5
    if len(data) >= 7 and data[:4] == b"Rar!" and data[4:6] == b"\x1a\x07":
        return "rar"
    return "unknown"


def _extract_archive(archive_path: Path, target_dir: Path, fmt: str) -> None:
    """zip / 7z / rar を target_dir に展開する。"""
    log = get_logger()
    log.info("extract begin: format=%s path=%s", fmt, archive_path)

    try:
        if fmt == "zip":
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(target_dir)
        elif fmt == "7z":
            import py7zr

            with py7zr.SevenZipFile(archive_path, mode="r") as zf:
                zf.extractall(path=target_dir)
        elif fmt == "rar":
            # py7zz は 7zz バイナリ同梱で RAR 展開可能
            # PyInstaller では同梱パス解決のため事前設定が必要
            ensure_py7zz_binary()
            import py7zz

            py7zz.extract_archive(str(archive_path), str(target_dir))
        else:
            raise TwoGameDownloadError(f"Unsupported archive format: {fmt}")
    except TwoGameDownloadError:
        raise
    except Exception as e:
        raise TwoGameDownloadError(
            f"Failed to extract {fmt} archive: {archive_path} / {e}"
        ) from e

    log.info("extract done: format=%s path=%s", fmt, archive_path)


def download_and_extract_archives(mod_id: str, cache_dir: Path, links: list[str]) -> None:
    """
    jp_download をすべて取得し cache_dir に展開する。
    サブフォルダ名: {順位:03d}_{file_id} （000 = 最新）
    対応形式: zip / 7z / rar
    """
    log = get_logger()
    if not links:
        return

    detail_url = DETAIL_URL_TEMPLATE.format(mod_id=mod_id)
    cookie_jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    # 詳細ページ訪問で Cookie を得る
    _http_get(opener, detail_url)

    cache_dir.mkdir(parents=True, exist_ok=True)

    for order, url in enumerate(links):
        file_id = _file_id_from_url(url)
        target_dir = cache_dir.joinpath(f"{order:03d}_{file_id}")
        target_dir.mkdir(parents=True, exist_ok=True)
        log.info(
            "2game download %s/%s begin: modid=%s file_id=%s",
            order + 1,
            len(links),
            mod_id,
            file_id,
        )

        data, final_url = _http_get(opener, url, referer=detail_url)
        if not data:
            raise TwoGameDownloadError(f"Empty download: {url}")

        # HTML が返った場合は失敗扱い
        if data[:15].lstrip().lower().startswith(b"<!doctype") or data[:6].lstrip().lower().startswith(b"<html"):
            raise TwoGameDownloadError(
                f"Download returned HTML instead of archive: {url} -> {final_url}"
            )

        fmt = _detect_archive_format(data)
        if fmt == "unknown":
            raise TwoGameDownloadError(
                f"Unsupported archive format: {url} magic={data[:8]!r}"
            )

        archive_path = target_dir.joinpath(f"{file_id}.{fmt}")
        archive_path.write_bytes(data)
        log.debug(
            "2game archive saved: %s format=%s bytes=%s",
            archive_path,
            fmt,
            len(data),
        )

        _extract_archive(archive_path, target_dir, fmt)
        log.info(
            "2game download %s/%s extracted: modid=%s file_id=%s format=%s",
            order + 1,
            len(links),
            mod_id,
            file_id,
            fmt,
        )


def ensure_translation_cache(mod_id: str, cache_root: Path) -> Path:
    """
    Downloaded_Translations/{modid}/ を用意する。
    既存かつ中身があれば再ダウンロードしない。
    リンクが無い場合はディレクトリを作らず返す。
    """
    log = get_logger()
    cache_dir = cache_root.joinpath(mod_id)
    lock = _lock_for_mod(mod_id)
    log.info("2game cache lock wait: modid=%s", mod_id)
    with lock:
        log.info("2game cache lock acquired: modid=%s", mod_id)
        if cache_dir.exists() and any(cache_dir.iterdir()):
            log.info("2game cache hit: %s", cache_dir)
            return cache_dir

        links = fetch_jp_download_links(mod_id)
        if not links:
            log.info("No jp_download links for modid=%s, skip 2game", mod_id)
            return cache_dir

        log.info(
            "2game downloading translations for modid=%s (%s file(s))",
            mod_id,
            len(links),
        )
        try:
            download_and_extract_archives(mod_id, cache_dir, links)
        except TwoGameDownloadError:
            if cache_dir.exists():
                shutil.rmtree(cache_dir, ignore_errors=True)
            raise
        log.info("2game cache ready: %s", cache_dir)
        return cache_dir


def find_matching_xml_files(cache_dir: Path, xml_name: str) -> list[Path]:
    """
    キャッシュ内の一致 XML を、アーカイブ順位（新しい→古い）で返す。
    フォルダ名 {order:03d}_* の昇順 = 新しい順。
    """
    if not cache_dir.exists():
        return []

    # order 付きサブフォルダ優先。無ければ全体から検索。
    ordered_dirs = sorted(
        [p for p in cache_dir.iterdir() if p.is_dir() and re.match(r"^\d{3}_", p.name)],
        key=lambda p: p.name,
    )

    results: list[Path] = []
    if ordered_dirs:
        for d in ordered_dirs:
            for xml_path in d.rglob(xml_name):
                if xml_path.is_file():
                    results.append(xml_path)
                    break  # 1アーカイブにつき1件
    else:
        results = [p for p in cache_dir.rglob(xml_name) if p.is_file()]

    return results


def parse_xtranslator_xml(xml_path: Path) -> list[XmlTranslationEntry]:
    """SSTXMLRessources 形式の xTranslator XML をパースする。"""
    log = get_logger()
    log.debug("XML parse begin: %s", xml_path)
    try:
        tree = ET.parse(xml_path)
    except ET.ParseError as e:
        log.warning("Failed to parse XML: %s / %s", xml_path, e)
        return []

    root = tree.getroot()
    if root.tag != "SSTXMLRessources":
        log.warning("Unexpected XML root '%s': %s", root.tag, xml_path)
        return []

    entries: list[XmlTranslationEntry] = []
    content = root.find("Content")
    if content is None:
        log.warning("XML has no Content: %s", xml_path)
        return []

    for string_el in content.findall("String"):
        edid_el = string_el.find("EDID")
        rec_el = string_el.find("REC")
        source_el = string_el.find("Source")
        dest_el = string_el.find("Dest")

        editor_id = normalize_editor_id(edid_el.text if edid_el is not None else "")
        record_raw = (rec_el.text if rec_el is not None else "") or ""
        record_type = normalize_record_type(record_raw)

        index = 0
        if rec_el is not None:
            id_attr = rec_el.attrib.get("id")
            if id_attr is not None and str(id_attr).strip() != "":
                try:
                    index = int(str(id_attr).strip())
                except ValueError:
                    index = 0

        source = source_el.text if source_el is not None and source_el.text is not None else ""
        dest = dest_el.text if dest_el is not None and dest_el.text is not None else ""

        entries.append(
            XmlTranslationEntry(
                editor_id=editor_id,
                record_type=record_type,
                index=index,
                source=source,
                dest=dest if dest is not None else "",
            )
        )
    log.info("XML parse done: %s entries=%s", xml_path.name, len(entries))
    return entries


def merge_entries_newest_wins(xml_files_newest_first: Iterable[Path]) -> list[XmlTranslationEntry]:
    """
    複数 XML をマージ。ページ上の新しいアーカイブを優先（衝突時は上書き）。
    入力は新しい→古い順。内部では古い→新しいで上書きする。
    """
    by_key: dict[tuple[str, str, int], XmlTranslationEntry] = {}
    # キー無し衝突用に source も別管理するが、最終リストは by_key + source-only を統合
    # ここでは全エントリを古い→新しいで積み、同一 (edid,type,index) は上書き。
    # source フォールバック辞書も同様に上書き。
    files = list(xml_files_newest_first)
    for xml_path in reversed(files):
        for entry in parse_xtranslator_xml(xml_path):
            key = (entry.editor_id, entry.record_type, entry.index)
            by_key[key] = entry

    return list(by_key.values())


def _is_translated_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and pd.isna(value):
        return False
    text = str(value).strip()
    return text != "" and text.lower() != "nan"


def apply_entries_to_csv(csv_path: Path, entries: list[XmlTranslationEntry]) -> int:
    """
    CSV の空 Translated 行に XML 翻訳を適用する。
    マッチ: 1) editor_id+type+index  2) 原文 string 完全一致
    戻り値: 埋めた件数
    """
    log = get_logger()
    if not csv_path.exists():
        log.warning("CSV not found for 2game apply: %s", csv_path)
        return 0

    log.info("2game CSV apply begin: %s entries=%s", csv_path.name, len(entries))
    df = pd.read_csv(csv_path)
    if "Translated" not in df.columns:
        df["Translated"] = None

    by_key: dict[tuple[str, str, int], str] = {}
    by_source: dict[str, str] = {}
    for entry in entries:
        dest = entry.dest if entry.dest is not None else ""
        if str(dest).strip() == "":
            continue
        by_key[(entry.editor_id, entry.record_type, entry.index)] = dest
        by_source[entry.source] = dest

    filled = 0
    total = len(df)
    for i, (idx, row) in enumerate(df.iterrows()):
        if i > 0 and i % 5000 == 0:
            log.info(
                "2game CSV apply progress: %s/%s filled=%s (%s)",
                i,
                total,
                filled,
                csv_path.name,
            )

        if _is_translated_filled(row.get("Translated")):
            continue

        editor_id = normalize_editor_id(row.get("editor_id"))
        record_type = normalize_record_type(str(row.get("type") or ""))
        try:
            index = int(row.get("index") if row.get("index") is not None else 0)
        except (TypeError, ValueError):
            index = 0

        translated = by_key.get((editor_id, record_type, index))
        if translated is None:
            source = row.get("string")
            if source is None or (isinstance(source, float) and pd.isna(source)):
                source = ""
            else:
                source = str(source)
            translated = by_source.get(source)

        if translated is None or str(translated).strip() == "":
            continue

        df.at[idx, "Translated"] = translated
        filled += 1

    log.info("2game CSV write begin: %s filled=%s", csv_path.name, filled)
    df.to_csv(csv_path, index=False)
    log.info("2game CSV apply done: %s filled=%s", csv_path.name, filled)
    return filled


def apply_2game_translation_to_csv(
    plugin_path: Path,
    csv_path: Path,
    cache_root: Path,
) -> int:
    """
    2game 翻訳を CSV に適用する。
    - modid 無し / リンク無し / 一致 XML 無し → 0件（LLM へフォールバック）
    - DL 失敗 → TwoGameDownloadError（呼び出し側で全体中断）
    """
    log = get_logger()
    name = plugin_path.name
    mod_id = read_mod_id_from_meta(plugin_path)
    if not mod_id:
        log.info("[%s] No modid in meta.ini, skip 2game", name)
        return 0

    log.info("[%s] 2game modid=%s", name, mod_id)
    xml_name = expected_xml_filename(plugin_path)
    cache_dir = ensure_translation_cache(mod_id, cache_root)

    if not cache_dir.exists() or not any(cache_dir.iterdir()):
        log.info("[%s] 2game cache empty, skip apply", name)
        return 0

    xml_files = find_matching_xml_files(cache_dir, xml_name)
    if not xml_files:
        log.info(
            "[%s] No matching XML '%s', skip 2game apply",
            name,
            xml_name,
        )
        return 0

    log.info("[%s] matching XML files=%s", name, [str(p) for p in xml_files])
    entries = merge_entries_newest_wins(xml_files)
    filled = apply_entries_to_csv(csv_path, entries)
    log.info(
        "[%s] 2game applied xml=%s filled=%s",
        name,
        len(xml_files),
        filled,
    )
    return filled
