# esp2dsd_LangChain

Skyrim SE のプラグイン（`.esp` / `.esm` / `.esl`）から文字列を抽出し、LLM（OpenAI + LangChain）で日本語翻訳したうえで、[Dynamic String Distributor (DSD)](https://www.nexusmods.com/skyrimspecialedition/mods/77957) 形式の JSON を出力するツールです。

Mod Organizer 2（MO2）の `mods` / `profiles` 構成を前提に、有効 Mod 上のプラグインを一括処理します。

## 処理フロー

```
MO2 ルート（mods / profiles）
        │
        ▼
 プロファイル選択（GUI）
        │
        ▼
 modlist.txt から有効 Mod のプラグインを収集
 （同名プラグインは上位 Mod 優先＝先勝ち）
        │
        ▼
 更新検知（plugin_timestamps.txt）＋ EXCLUDE_PLUGINS で除外
        │
        ▼
 並列処理（MAX_PARALLEL）
   ├─ 1. 文字列抽出 → Translated_Csv/{plugin}.csv
   ├─ 2. 2game 既存翻訳を適用（空欄のみ）
   ├─ 3. 未翻訳分を LLM 翻訳 → CSV の Translated 列
   └─ 4. DSD 変換 → Translated_DSD/{plugin}/{plugin}.json
        │
        ▼
 タイムスタンプを保存（次回の差分判定用）
```

## 必要環境

| 項目 | 内容 |
|------|------|
| Python | 3.13 |
| OS | Windows（`initialize.bat` / `build.bat` / Tkinter GUI 前提） |
| API | OpenAI API Key（環境変数 `OPENAI_API_KEY`） |
| ネットワーク | [2game.info](https://skyrimspecialedition.2game.info/) へのアクセス（既存日本語翻訳の取得） |
| 入力構成 | MO2 互換ディレクトリ（`mods/` と `profiles/<name>/modlist.txt`、各 Mod に `meta.ini`） |

## セットアップ

```bat
initialize.bat
```

仮想環境を有効化したうえで依存パッケージをインストールします。

```bat
call .\venv\Scripts\activate.bat
pip install -r requirements.txt
```

OpenAI API Key を設定します。

```bat
setx OPENAI_API_KEY "sk-xxxxxxxx"
```

実行時はカレントディレクトリに `translate_plugin2dsd_config.ini` を置いてください（リポジトリ直下の同名ファイルがテンプレートです）。

## 使い方

### Python から実行

```bat
python main.py
```

引数なしの場合、カレントディレクトリを MO2 ルートとして扱います。

```bat
python main.py "D:\MO2\instances\SkyrimSE"
```

MO2 ルートフォルダ（またはその中のファイル）をドラッグ＆ドロップしても同様です。

起動後、プロファイル選択ダイアログが表示されます。`profiles/` 配下のフォルダ名から選択します。

### 実行ファイル（PyInstaller）

```bat
build.bat
```

`translate_plugin2dsd.spec` を用いて exe をビルドします。exe 実行時は、exe と同じフォルダを書き込みベース（CSV / DSD / 設定 / タイムスタンプ）として使います。

RAR 展開用に `7zz.exe` / `7z.dll` を exe へ同梱します。万一 RAR で失敗する場合は、`build.bat` が `dist\` にコピーする `7zz.exe` と `7z.dll` を exe と同じフォルダへ置いてください。

## 入出力

### 入力ディレクトリ構成

```
<MO2ルート>/
  mods/
    <Mod名>/
      *.esp / *.esm / *.esl
  profiles/
    <プロファイル名>/
      modlist.txt
```

`modlist.txt` の `+` で始まる行のみ有効 Mod として扱います（MO2 左ペイン相当）。

### 出力

| パス | 内容 |
|------|------|
| `Translated_Csv/{plugin名}.csv` | 抽出文字列＋翻訳結果 |
| `Translated_DSD/{plugin名}/{plugin名}.json` | DSD 用 JSON |
| `Downloaded_Translations/{modid}/` | 2game から取得した翻訳アーカイブのキャッシュ |
| `plugin_timestamps.txt` | 処理済みプラグインの mtime 一覧 |
| `esp2dsd.log` | 実行ログ（段階・スレッド名・エラー詳細） |

実行ベースパス:

- `python main.py` … カレントディレクトリ
- exe … exe のあるフォルダ

### CSV 列

| 列 | 説明 |
|----|------|
| `editor_id` | Editor ID（無い場合は `"null"`） |
| `form_id` | Form ID |
| `index` | インデックス（無い場合は `0`） |
| `type` | レコード種別（例: `INFO NAM1`, `BOOK DESC`） |
| `string` | 原文 |
| `Translated` | 翻訳文（LLM 処理後） |

### DSD JSON 要素

```json
{
  "editor_id": "SomeEditorID",
  "form_id": "01234567",
  "index": 0,
  "type": "INFO NAM1",
  "original": "English text",
  "string": "日本語訳",
  "status": "TranslationComplete"
}
```

## 2game 既存翻訳の適用

LLM の前に、[skyrimspecialedition.2game.info](https://skyrimspecialedition.2game.info/) 上の日本語化ファイルを検索・適用します（常時有効・設定スイッチなし）。

### 取得

1. プラグイン所在 Mod の `meta.ini` から `modid` を読む  
   （例: Nexus `.../mods/94505` → 2game `detail.php?id=94505`）
2. 詳細ページの `jp_download.php` リンクを**すべて**取得（表示上側＝新しい）
3. zip / 7z / rar をダウンロードし `Downloaded_Translations/{modid}/` に展開
4. 同じ `modid` フォルダが既にあれば再ダウンロードしない（キャッシュ優先）

`meta.ini` / `modid` が無い、または `jp_download.php` が無い場合はスキップして LLM のみです。

ダウンロード失敗・サイト不通・非対応アーカイブの場合は、当該プラグインの 2game 適用を**スキップ**し、LLM 翻訳以降の処理を継続します。

2game への HTTP リクエストは、設定 `[TWOGAME] REQUEST_INTERVAL_SEC`（デフォルト 1.0 秒）の間隔でレート制限されます（プラグイン並列時もプロセス全体で共有）。

### 対象 XML

xTranslator の `SSTXMLRessources` 形式（`.xml`）のみ。

ファイル名ルール:

```text
Something.esp  →  Something_english_japanese.xml
```

アーカイブ内に一致 XML が無い場合、そのアーカイブからは適用しません。全アーカイブで見つからなければ LLM のみです。

複数アーカイブがある場合はすべて読み、**新しい側を優先**してマージします。

### CSV へのマッチ

`Translated` が空の行だけ埋めます。

1. `editor_id` + `type` + `index`  
   （XML の `EDID` + `REC`（`QUST:NNAM` → `QUST NNAM`）+ `REC/@id`。id 無しは `0`）
2. だめなら原文 `string` と XML `Source` の完全一致

翻訳が空文字のエントリは未翻訳扱い（LLM へ）。

## 差分処理と除外

- 前回保存した `plugin_timestamps.txt` と比較して、mtime が変わった（または未登録の）プラグインだけを処理します。
- `EXCLUDE_PLUGINS` に列挙したプラグイン名は処理対象から外します。
- 対象が 0 件の場合は何もせず終了します。
- 処理完了後、**収集した全プラグイン**のタイムスタンプを保存します（除外・スキップ分も含む）。

## 設定ファイル

`translate_plugin2dsd_config.ini`

### `[GENERAL]`

| キー | 説明 |
|------|------|
| `MAX_PARALLEL` | プラグイン単位の並列数（スレッドプール） |
| `TARGET_TYPE` | 翻訳対象のレコード種別。不要な行はコメントアウトして絞り込み |
| `EXCLUDE_PLUGINS` | 翻訳しないプラグイン名の一覧 |

`TARGET_TYPE` / `EXCLUDE_PLUGINS` は疑似 dict 形式で記述します（右辺の値はダミーで、キーの有無のみが意味を持ちます）。

### `[TWOGAME]`

| キー | 説明 |
|------|------|
| `REQUEST_INTERVAL_SEC` | 2game への HTTP リクエスト最小間隔（秒）。並列処理でも全体共有。`0` で無効。デフォルト `1.0` |

### `[LLM]`

| キー | 説明 |
|------|------|
| `LLM_MODEL` | モデル名（例: `gpt-4o-mini`） |
| `MAX_INPUT_TOKENS` | 1 バッチあたりの入力トークン上限 |
| `MAX_RETRY` | ID 欠落・API 失敗時の再試行回数 |
| `SYSTEM_PROMPT` | システムプロンプト（日本語ゲーム文言向けの共通指示） |
| `PROMPT_TEMPLATE` | ユーザープロンプトの共通テンプレート（JSON 入出力規約） |
| `PROMPT_<TYPE>` | レコード種別ごとの追加指示（例: `PROMPT_INFO_NAM1` → type `INFO NAM1`） |
| `PROMPT_OTHERS` | 専用プロンプトが無い type 向けのフォールバック |

種別ごとのプロンプトは `PROMPT_` 接頭辞を除き `_` を空白に置換した名前（`DIAL FULL` など）でマッチします。

## 翻訳処理の仕様（LLM）

- 2game で埋まらなかった（`Translated` が空の）行だけを対象にします。
- CSV を `type` ごとにグループ化し、トークン上限に収まるようバッチ分割して LLM に送ります。
- LangChain の Structured Output（Pydantic）で  
  `{ "translations": [ { "id", "text" }, ... ] }` 形式を強制します。
- プラグイン単位で同一 `(type, 原文)` の翻訳結果をキャッシュし、重複呼び出しを避けます。
- 空文字のみのバッチは API を呼びません。
- 再試行後も欠落した ID は `[翻訳失敗: 原文]` として書き出します。
- `<key=value>` 形式のタグは原文保持、`=` を含まない `<...>` は内容のみ翻訳、などのルールはプロンプト側で指示しています。

## モジュール構成

| パス | 役割 |
|------|------|
| `main.py` | エントリポイント。プロファイル選択・差分判定・並列オーケストレーション |
| `src/select_profile_dialog.py` | プロファイル選択ダイアログ（Tkinter） |
| `src/extract_strings_from_plugins.py` | `sse-plugin-interface` による文字列抽出 → CSV |
| `src/twogame_translation.py` | 2game から xTranslator XML を取得し CSV に適用 |
| `src/translate_csv_llm.py` | LangChain / OpenAI による CSV 翻訳（未翻訳行のみ） |
| `src/csv2dsd_converter.py` | CSV → DSD JSON 変換 |
| `src/app_logger.py` | コンソール＋`esp2dsd.log` への診断ログ |
| `src/utility.py` | パス解決、modlist 解析、トークン分割、設定読込、タイムスタンプ I/O |
| `config/` | 用途別のプロンプト／対象種別プリセット例（参考用） |

## 依存パッケージ

`requirements.txt` より:

- `langchain` / `langchain-core` / `langchain-openai`
- `pandas` / `pydantic` / `tiktoken` / `python-dotenv`
- `sse-plugin-interface`（プラグイン文字列抽出）
- `py7zr`（7z 展開）/ `py7zz`（rar 展開、7zz 同梱）
- `pyinstaller`（配布用ビルド）

## ライセンス

MIT License（Copyright (c) 2026 RedyellowUnit）
