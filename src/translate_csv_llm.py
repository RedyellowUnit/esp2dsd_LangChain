import configparser
from pathlib import Path
import pandas as pd
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typing import List
from src.utility import get_Config_Parser,normalize_text_list, build_id_map, count_tokens, chunk_by_token_limit, build_prompt_map
from src.app_logger import get_logger

# CommandPrompt:
#   > setx OPENAI_API_KEY "sk-xxxxxxxx"

# Config
CONFIG: configparser.ConfigParser = get_Config_Parser()

# Structured Output
class TranslationItem(BaseModel):
    id: int
    text: str

class TranslationResult(BaseModel):
    translations: List[TranslationItem]

# Cache
class TranslationCache:
    def __init__(self):
        self._cache = {}

    def get(self, record_type: str, text: str):
        return self._cache.get((record_type, text))

    def set(self, record_type: str, text: str, translated: str):
        self._cache[(record_type, text)] = translated

    def size(self):
        return len(self._cache)

# GPT-API初期化
LLM_MODEL = CONFIG["LLM"].get("LLM_MODEL")
LLM = ChatOpenAI(model=LLM_MODEL, temperature=0)
STRUCTURED_LLM = LLM.with_structured_output(TranslationResult)

# 速度改善処理用の設定
MAX_INPUT_TOKENS:int = CONFIG.getint("LLM", "MAX_INPUT_TOKENS")

# API失敗のリトライ回数
MAX_RETRY:int = CONFIG.getint("LLM", "MAX_RETRY")

# Typeごとの指示を追加
PROMPT_MAP = build_prompt_map(CONFIG)

def translate_with_retry(pipeline, input_items, record_type, batch_no, token_batch_no):
    log = get_logger()
    remaining = input_items
    final_results = {}

    for retry in range(MAX_RETRY + 1):
        if not remaining:
            break

        try:
            log.debug(
                "LLM invoke begin Type=%s Batch=%s TokenBatch=%s Retry=%s items=%s",
                record_type,
                batch_no,
                token_batch_no,
                retry,
                len(remaining),
            )
            result: TranslationResult = pipeline.invoke({"text": remaining})
            log.debug(
                "LLM invoke done Type=%s Batch=%s TokenBatch=%s Retry=%s returned=%s",
                record_type,
                batch_no,
                token_batch_no,
                retry,
                len(result.translations),
            )
        except Exception as e:
            log.error(
                "Type=%s Batch=%s TokenBatch=%s Retry=%s API error: %s",
                record_type,
                batch_no,
                token_batch_no,
                retry,
                e,
            )
            continue

        returned_ids = set()
        for item in result.translations:
            final_results[item.id] = item.text
            returned_ids.add(item.id)

        remaining = [
            item for item in remaining
            if item["id"] not in returned_ids
        ]

        if remaining:
            log.warning(
                "Type=%s Batch=%s TokenBatch=%s Retry=%s Missing IDs=%s",
                record_type,
                batch_no,
                token_batch_no,
                retry,
                len(remaining),
            )

    return final_results, remaining



def call_llm_api_batch(text_list, record_type, batch_no, translation_cache):
    safe_text_list = normalize_text_list(text_list)

    # ==== 全件空チェック ====
    non_empty_items = [
        (i, t) for i, t in enumerate(safe_text_list)
        if t.strip() != ""
    ]

    # 全件空 → API呼び出ししない
    if not non_empty_items:
        return [""] * len(safe_text_list)

    # ==== キャッシュ確認 ====
    translated_map = {}
    uncached_items = []

    for i, t in non_empty_items:
        cached = translation_cache.get(record_type, t)
        if cached is not None:
            translated_map[i] = cached
        else:
            uncached_items.append((i, t))

    # 全件キャッシュヒット→API呼び出しせず、Cacheの翻訳を使う
    if not uncached_items and len(translated_map) == len(non_empty_items):
        results = []
        for i, text in enumerate(safe_text_list):
            if text.strip() == "":
                results.append("")
            else:
                results.append(translated_map.get(i))
        return results

    # ==== ID付き入力生成（未キャッシュのみ） ====
    input_items = [
        {"id": i, "text": t}
        for i, t in uncached_items
    ]
    original_id_text = {i: t for i, t in uncached_items}

    prompt = ChatPromptTemplate.from_messages([
        ("system", CONFIG["LLM"].get("SYSTEM_PROMPT")),
        ("user", PROMPT_MAP.get(record_type, PROMPT_MAP["others"])),
    ])

    pipeline = prompt | STRUCTURED_LLM

    # ==== TokenBatch分割（ID単位） ====
    for token_batch_no, item_batch in enumerate(
        chunk_by_token_limit(input_items, MAX_INPUT_TOKENS, LLM_MODEL),
        start=1
    ):

        result_map, failed_items = translate_with_retry(
            pipeline,
            item_batch,
            record_type,
            batch_no,
            token_batch_no,
        )

        # 結果格納
        for id, text in result_map.items():
            translated_map[id] = text
            # キャッシュ保存
            translation_cache.set(record_type, original_id_text[id], text)

        # 失敗分は元文字列を出力
        for item in failed_items:
            original_text = item["text"]
            translated_map[item["id"]] = f"[翻訳失敗: {original_text}]"

    # ==== 元の順序で復元 ====
    results = []
    for i, text in enumerate(safe_text_list):
        if text.strip() == "":
            results.append("")
        else:
            results.append(
                translated_map.get(i, text)
            )

    return results




def _process_one_batch(df, record_type, batch_items, batch_no, translation_cache: TranslationCache):
    log = get_logger()
    idx_chunk = [i for i, _ in batch_items]
    text_chunk = [t for _, t in batch_items]

    log.info(
        "Type=%s Batch=%s index=%s～%s count=%s API translate begin",
        record_type,
        batch_no,
        idx_chunk[0],
        idx_chunk[-1],
        len(text_chunk),
    )

    translated_chunk = call_llm_api_batch(
        text_chunk,
        record_type,
        batch_no,
        translation_cache
    )

    for i, translated_text in zip(idx_chunk, translated_chunk):
        df.at[i, "Translated"] = translated_text

    log.info("Type=%s Batch=%s API translate done", record_type, batch_no)

def translate_csv_llm(csv_path: Path)->bool:
    """
    CSVを読み込み、LLM(API)に翻訳を依頼する。
    CSV列に Translated を追加し、翻訳テキストを保存する。
    既に Translated がある行は保持し、空欄のみ翻訳する。
    """
    log = get_logger()
    if not csv_path.exists():
        log.error("CSV not found for LLM: %s", csv_path)
        return False

    log.info("LLM translate begin: %s", csv_path.name)
    df = pd.read_csv(csv_path)
    if "Translated" not in df.columns:
        df["Translated"] = None
        log.debug("Translated column created: %s", csv_path.name)
    else:
        already = df["Translated"].apply(
            lambda v: v is not None
            and not (isinstance(v, float) and pd.isna(v))
            and str(v).strip() != ""
            and str(v).strip().lower() != "nan"
        ).sum()
        log.info(
            "LLM translate: %s rows=%s already_translated=%s",
            csv_path.name,
            len(df),
            int(already),
        )

    # Cache plugin単位（スレッドローカル）
    translation_cache = TranslationCache()

    for record_type, group_df in df.groupby("type"):
        pending = 0
        for idx in group_df.index.tolist():
            existing = df.at[idx, "Translated"]
            if existing is None or (isinstance(existing, float) and pd.isna(existing)) or str(existing).strip() == "":
                pending += 1
        log.info(
            "%s Type=%s rows=%s pending_llm=%s",
            csv_path.name,
            record_type,
            len(group_df),
            pending,
        )
        if pending == 0:
            continue

        # index と text をペアで保持
        items = list(zip(group_df.index.tolist(), group_df["string"].tolist()))

        batch_no = 0
        current_batch = []
        current_tokens = 0

        for idx, text in items:
            # 既に Translated がある行はスキップ（2game 適用済みなど）
            existing = df.at[idx, "Translated"]
            if existing is not None and not (isinstance(existing, float) and pd.isna(existing)):
                if str(existing).strip() != "":
                    continue

            text_tokens = count_tokens(text, LLM_MODEL)

            # 単文が上限超え → 単独バッチ
            if text_tokens > MAX_INPUT_TOKENS:
                if current_batch:
                    batch_no += 1
                    _process_one_batch(
                        df, record_type, current_batch, batch_no, translation_cache
                    )
                    current_batch = []
                    current_tokens = 0

                batch_no += 1
                _process_one_batch(
                    df, record_type, [(idx, text)], batch_no, translation_cache
                )
                continue

            if current_tokens + text_tokens > MAX_INPUT_TOKENS:
                batch_no += 1
                _process_one_batch(
                    df, record_type, current_batch, batch_no, translation_cache
                )
                current_batch = [(idx, text)]
                current_tokens = text_tokens
            else:
                current_batch.append((idx, text))
                current_tokens += text_tokens

        if current_batch:
            batch_no += 1
            _process_one_batch(
                df, record_type, current_batch, batch_no, translation_cache
            )

    log.info("LLM CSV write begin: %s", csv_path.name)
    df.to_csv(csv_path, index=False)
    log.info("LLM translate done: %s", csv_path.name)

    return True # 翻訳の成功失敗は、Csvを検索して判断すること。自動判定はできない。


