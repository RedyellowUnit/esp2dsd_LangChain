import sys
from pathlib import Path
import configparser
import concurrent.futures
import traceback

from src.extract_strings_from_plugins import extract_save_csv
from src.translate_csv_llm import translate_csv_llm
from src.csv2dsd_converter import convert_csv_to_dsd
from src.twogame_translation import apply_2game_translation_to_csv, TwoGameDownloadError
from src.utility import find_mod_plugins_from_profile, get_runtime_base_path, resolve_input_dir, get_Config_Parser, save_current_timestamps, load_previous_timestamps
from src.select_profile_dialog import select_profile_dialog
from src.app_logger import setup_logging, get_logger

BASE_PATH: Path = get_runtime_base_path()
CSV_DIR: Path = BASE_PATH.joinpath("Translated_Csv")
DSD_DIR: Path = BASE_PATH.joinpath("Translated_DSD")
TWOGAME_DIR: Path = BASE_PATH.joinpath("Downloaded_Translations")
TIMESTAMP_FILE: Path = BASE_PATH.joinpath("plugin_timestamps.txt")

# Config
CONFIG: configparser.ConfigParser = get_Config_Parser()


def process_plugin(plugin: Path) -> None:
    """
    単一プラグイン処理（並列実行対象）
    文字列抽出→2game翻訳→LLM翻訳→DSD変換
    """
    log = get_logger()
    name = plugin.name

    try:
        log.info("[%s] === START === path=%s", name, plugin)
        log.info("[%s] STEP1 extract CSV begin", name)
        extract_result = extract_save_csv(plugin, CSV_DIR)
        if not extract_result:
            log.error("[%s] STEP1 extract failed", name)
            return
        log.info("[%s] STEP1 extract CSV done", name)

        csv_path = CSV_DIR.joinpath(f"{plugin.name}.csv").resolve()
        log.info("[%s] STEP2 2game apply begin csv=%s", name, csv_path)
        try:
            filled = apply_2game_translation_to_csv(plugin, csv_path, TWOGAME_DIR)
            log.info("[%s] STEP2 2game apply done filled=%s", name, filled)
        except TwoGameDownloadError as e:
            # 2game 失敗は当該プラグインの 2game 適用のみスキップし、LLM 以降は継続
            log.warning(
                "[%s] STEP2 2game failed, skip 2game and continue with LLM: %s",
                name,
                e,
            )

        log.info("[%s] STEP3 LLM translate begin", name)
        translate_result = translate_csv_llm(csv_path)
        if not translate_result:
            log.error("[%s] STEP3 LLM translate failed", name)
            return
        log.info("[%s] STEP3 LLM translate done", name)

        json_path = DSD_DIR.joinpath(plugin.name, f"{plugin.name}.json")
        log.info("[%s] STEP4 DSD convert begin -> %s", name, json_path)
        convert_result = convert_csv_to_dsd(csv_path, json_path)
        if not convert_result:
            log.error("[%s] STEP4 DSD convert failed", name)
            return

        log.info("[%s] === OK Finished ===", name)

    except Exception as e:
        log.exception("[%s] Exception: %s", name, e)
        traceback.print_exc()


def main():
    setup_logging(BASE_PATH)
    log = get_logger()

    input_base_dir = resolve_input_dir(sys.argv, get_runtime_base_path())
    log.info("Runtime base: %s", BASE_PATH)
    log.info("Input dir: %s", input_base_dir)
    log.info("argv: %s", sys.argv)

    profile_dir = input_base_dir.joinpath("profiles")
    selected_profile = select_profile_dialog(profile_dir)
    if selected_profile is None:
        log.info("Profile selection cancelled.")
        return
    log.info("Profile selected: %s", selected_profile)

    CSV_DIR.mkdir(exist_ok=True)
    DSD_DIR.mkdir(exist_ok=True)
    TWOGAME_DIR.mkdir(exist_ok=True)

    plugin_list = find_mod_plugins_from_profile(input_base_dir, selected_profile)
    log.info("Plugins found from profile: %s", len(plugin_list))

    # 更新されたプラグインをリスト
    previous_timestamps = load_previous_timestamps(TIMESTAMP_FILE)
    updated_plugins = []
    for plugin in plugin_list:
        try:
            current_mtime = plugin.stat().st_mtime
            previous_mtime = previous_timestamps.get(plugin.name)
            if previous_mtime is None or current_mtime != previous_mtime:
                updated_plugins.append(plugin)
        except Exception:
            continue

    log.info("Updated plugins: %s", len(updated_plugins))

    # 除外プラグインをリスト
    exclude_plugins = CONFIG["GENERAL"].get("EXCLUDE_PLUGINS")
    filtered_plugins = [
        plugin for plugin in updated_plugins
        if plugin.name not in exclude_plugins
    ]
    if not filtered_plugins:
        log.info("No plugins to process after exclusion.")
        return

    log.info(
        "Plugins to process: %s / max_parallel=%s",
        len(filtered_plugins),
        CONFIG.getint("GENERAL", "MAX_PARALLEL"),
    )
    for p in filtered_plugins:
        log.debug("  queue: %s", p.name)

    # 並列実行
    max_workers = min(CONFIG.getint("GENERAL", "MAX_PARALLEL"), len(filtered_plugins))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="plugin",
    ) as executor:
        futures = [
            executor.submit(process_plugin, plugin)
            for plugin in filtered_plugins]

        for future in concurrent.futures.as_completed(futures):
            future.result()

    save_current_timestamps(TIMESTAMP_FILE, plugin_list)
    log.info("All done. Timestamps saved.")

if __name__ == "__main__":
    main()
