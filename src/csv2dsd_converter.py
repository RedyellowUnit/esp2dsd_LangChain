import csv
import json
from pathlib import Path
from src.app_logger import get_logger

def normalize_int(value, default=0):
    if value is None:
        return default

    # 空文字や空白だけの場合
    if isinstance(value, str) and value.strip() == "":
        return default

    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def normalize_str(value):
    if value is None:
        return ""
    return str(value).strip()

def normalize_string_editor_id(value):
    if value is None or value == "":
        return None
    return str(value).strip()

def convert_csv_to_dsd(csv_path: Path, json_path: Path):
    """
    CSVを読み込み、DSD形式に変換して保存（数字・文字列両対応）
    
    CSV列: editor_id, form_id, index, type, string, Translated
    JSON列: editor_id, form_id, index, type, original, string, status
    """
    log = get_logger()
    data = []

    if not csv_path.exists():
        log.error("DSD convert failed, CSV missing: %s", csv_path)
        return False

    log.info("DSD convert begin: %s -> %s", csv_path.name, json_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # CSV行が全て空の場合はスキップ
            if not any(row.values()):
                continue

            item = {
                "editor_id": normalize_string_editor_id(row.get("editor_id")),
                "form_id": normalize_str(row.get("form_id")),
                "index": normalize_int(row.get("index")),
                "type": normalize_str(row.get("type")),
                "original": normalize_str(row.get("string")),
                "string": normalize_str(row.get("Translated")),
                "status": "TranslationComplete"
            }
            data.append(item)
    # Csvデータ行がすべて空の場合は何もしない
    if not data:
        log.info("DSD convert skip (no data rows): %s", csv_path.name)
        return True

    # JSON 出力（UTF-8, インデント付き）
    json_path.parent.mkdir(exist_ok=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    log.info("DSD convert done: %s entries=%s", json_path, len(data))
    return True
