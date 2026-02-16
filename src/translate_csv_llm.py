import configparser
from pathlib import Path
import pandas as pd
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel
from typing import List
from src.utility import get_Config_Parser,normalize_text_list, build_id_map, count_tokens, chunk_by_token_limit, build_prompt_map

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
    remaining = input_items
    final_results = {}

    for retry in range(MAX_RETRY + 1):
        if not remaining:
            break

        try:
            result: TranslationResult = pipeline.invoke({"text": remaining})
        except Exception as e:
            print(
                f"[ERROR] Type={record_type} Batch={batch_no} "
                f"TokenBatch={token_batch_no} Retry={retry} API error: {e}"
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
            print(
                f"[WARN] Type={record_type} Batch={batch_no} "
                f"TokenBatch={token_batch_no} Retry={retry} "
                f"Missing IDs={len(remaining)}"
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
    idx_chunk = [i for i, _ in batch_items]
    text_chunk = [t for _, t in batch_items]

    print(
        f"[INFO] "
        f"Type={record_type} Batch={batch_no} "
        f"対象index={idx_chunk[0]}～{idx_chunk[-1]} "
        f"件数={len(text_chunk)} API翻訳開始"
    )

    translated_chunk = call_llm_api_batch(
        text_chunk,
        record_type,
        batch_no,
        translation_cache
    )

    for i, translated_text in zip(idx_chunk, translated_chunk):
        df.at[i, "Translated"] = translated_text
        #print(f"[Debug] {df.at[i, "string"]}  {translated_text}")

def translate_csv_llm(csv_path: Path)->bool:
    """
    CSVを読み込み、LLM(API)に翻訳を依頼する。
    CSV列に Translated を追加し、翻訳テキストを保存する。
    """
    if not csv_path.exists():
        return False

    df = pd.read_csv(csv_path)
    df["Translated"] = None

    # Cache plugin単位（スレッドローカル）
    translation_cache = TranslationCache()

    for record_type, group_df in df.groupby("type"):
        print(f"[INFO] {csv_path.name} Type処理開始: {record_type} 件数={len(group_df)}")

        # index と text をペアで保持
        items = list(zip(group_df.index.tolist(), group_df["string"].tolist()))

        batch_no = 0
        current_batch = []
        current_tokens = 0

        for idx, text in items:
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

        #print(f"[INFO] Type毎の処理完了: {record_type}")

    df.to_csv(csv_path, index=False)

    return True # 翻訳の成功失敗は、Csvを検索して判断すること。自動判定はできない。


