import sys
from pathlib import Path
import tiktoken
import pandas as pd

PLUGIN_EXTENSIONS = {".esp", ".esm", ".esl"}

def get_base_path() -> Path:
    """
    アプリケーションの基準ディレクトリを返す

    - python main.py 実行時:
        main.py があるプロジェクトルート
    - PyInstaller --onefile 実行時:
        _MEIPASS（同梱ファイルの展開先）
    """
    # PyInstaller 実行時
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)

    # 通常実行時（main.py の場所を基準）
    return Path(sys.modules["__main__"].__file__).resolve().parent

def get_runtime_base_path() -> Path:
    """
    書き込み用の安全なベースパス。
    exe 配布時は exe のあるフォルダを返す。
    Python 実行時はカレントディレクトリを返す。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    else:
        return Path.cwd()

def resolve_input_dir(argv: list[str], default_dir: Path) -> Path:
    """
    ドラッグ＆ドロップ、または引数指定、またはデフォルトフォルダを解決
    """
    if len(argv) < 2:
        return default_dir

    dropped_path = Path(argv[1]).resolve()

    if dropped_path.is_dir():
        return dropped_path

    if dropped_path.is_file():
        return dropped_path.parent

    return default_dir

def find_mod_plugins(root_dir: Path) -> list[Path]:
    """
    2 階層目までのプラグイン探索
    """
    plugins: list[Path] = []

    # 1階層目を走査
    for path in root_dir.iterdir():

        # ① 1階層目に直接プラグインがある場合
        if path.is_file() and path.suffix.lower() in PLUGIN_EXTENSIONS:
            plugins.append(path)
            continue

        # ② 1階層目がディレクトリ（MO2の通常構成）
        if path.is_dir():
            for sub in path.iterdir():
                if sub.is_file() and sub.suffix.lower() in PLUGIN_EXTENSIONS:
                    plugins.append(sub)

    print(f"[Info] Plugins found: {len(plugins)}")
    return plugins

# Utility
def normalize_text_list(text_list):
    normalized = []
    for t in text_list:
        if t is None:
            normalized.append("")
        elif isinstance(t, float) and pd.isna(t):
            normalized.append("")
        else:
            normalized.append(str(t))
    return normalized

# Utility
def build_id_map(text_list):
    return [
        {"id": i, "text": t}
        for i, t in enumerate(text_list)
    ]

# Utility
def restore_by_id(input_items, llm_items):
    """
    input_items:build_id_mapで作成したMap
    llm_items:Structured Outputでのプロンプトの応答
    →IDベース復元+不足ID検出する
    """
    result_map = {item.id: item.text for item in llm_items}

    restored = []
    missing_ids = []

    for item in input_items:
        if item["id"] in result_map:
            restored.append(result_map[item["id"]])
        else:
            restored.append(None)
            missing_ids.append(item["id"])

    return restored, missing_ids


# Utility
def count_tokens(text: str, llm_model: str) -> int:
    if text is None:
        text = ""
    elif isinstance(text, float) and pd.isna(text):
        text = ""
    else:
        text = str(text)
    try:
        encoder = tiktoken.encoding_for_model(llm_model)
    except Exception:
        encoder = tiktoken.get_encoding("cl100k_base")

    return len(encoder.encode(text))

# Utility
def chunk_by_token_limit(items, max_tokens, llm_model):
    """
    items を、合計トークン数が max_tokens を超えない単位で分割する。

    対応形式:
    - str
    - {"id": int, "text": str}

    戻り値:
    - items と同じ型のリストを yield
    """

    batch = []
    current_tokens = 0

    def get_text(item):
        if isinstance(item, dict):
            return item.get("text", "")
        return item

    for item in items:
        text = get_text(item)
        text_tokens = count_tokens(text, llm_model)

        # 単文が上限超え → 単独バッチ
        if text_tokens > max_tokens:
            if batch:
                yield batch
                batch = []
                current_tokens = 0

            yield [item]
            continue

        # バッチ上限超過
        if current_tokens + text_tokens > max_tokens:
            yield batch
            batch = [item]
            current_tokens = text_tokens
        else:
            batch.append(item)
            current_tokens += text_tokens

    if batch:
        yield batch


from typing import Dict
import configparser

# Config
def get_Config_Parser() -> configparser.ConfigParser:
    # Config
    config_file = get_runtime_base_path().joinpath("config").joinpath("setting.ini")
    CONFIG = configparser.ConfigParser()
    CONFIG.read(config_file, encoding="utf-8")

    return CONFIG

def build_prompt_map(config: configparser.ConfigParser) -> Dict[str, str]:
    """
    Configを読み込み、翻訳用プロンプトをレコードタイプごとのMapで生成する
    """
    llm_cfg = config["LLM"]
    base_template = llm_cfg["PROMPT_TEMPLATE"].lstrip()
    input_suffix = "【入力テキスト】:{text}"

    prompt_map: Dict[str, str] = {}

    for key, value in llm_cfg.items():
        # PROMPT_ で始まるが TEMPLATE は除外
        if not key.startswith("prompt_"):
            continue
        if key == "prompt_template":
            continue

        # 表示用キーに変換（PROMPT_DIAL_FULL → DIAL FULL）
        prompt_name = key.removeprefix("prompt_").replace("_", " ")
        prompt_map[prompt_name] = (
            base_template
            + value.lstrip()
            + input_suffix
        )

    return prompt_map

def save_current_timestamps(file_path: Path, plugins: list[Path]) -> None:
    """
    プラグインの更新日時を一覧で保存する
    """
    with open(file_path, "w", encoding="utf-8") as f:
        for plugin in plugins:
            try:
                mtime = plugin.stat().st_mtime
                f.write(f"{plugin.name}\t{mtime}\n")
            except Exception:
                continue

def load_previous_timestamps(file_path: Path) -> dict[str, float]:
    """
    プラグインの更新日時を一覧で取得する
    """
    if not file_path.exists():
        return {}

    result = {}
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                name, ts = line.split("\t", 1)
                result[name] = float(ts)
            except ValueError:
                continue
    return result
