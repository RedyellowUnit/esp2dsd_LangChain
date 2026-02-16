import sys
from pathlib import Path
import configparser
import concurrent.futures

from src.extract_strings_from_plugins import extract_save_csv
from src.translate_csv_llm import translate_csv_llm
from src.csv2dsd_converter import convert_csv_to_dsd
from src.utility import find_mod_plugins_from_profile, get_runtime_base_path, resolve_input_dir, get_Config_Parser, save_current_timestamps
from src.select_profile_dialog import select_profile_dialog

BASE_PATH: Path = get_runtime_base_path()
CSV_DIR: Path = BASE_PATH.joinpath("Translated_Csv")
DSD_DIR: Path = BASE_PATH.joinpath("Translated_DSD")
TIMESTAMP_FILE: Path = BASE_PATH.joinpath("plugin_timestamps.txt")

# Config
CONFIG: configparser.ConfigParser = get_Config_Parser()

def process_plugin(plugin: Path) -> None:
    """
    単一プラグイン処理（並列実行対象）
    文字列抽出→翻訳→DSD変換
    """
    try:
        extract_result = extract_save_csv(plugin, CSV_DIR)
        if not extract_result:
            print(f"[Error] Failed to extract string from plugin: {plugin}")
            return

        csv_path = CSV_DIR.joinpath(f"{plugin.name}.csv").resolve()

        translate_result = True #translate_csv_llm(csv_path)
        if not translate_result:
            print(f"[Error] Failed to translate plugin: {csv_path}")
            return

        json_path = DSD_DIR.joinpath(plugin.name, f"{plugin.name}.json")
        convert_result = True #convert_csv_to_dsd(csv_path, json_path)
        if not convert_result:
            print(f"[Error] Failed to convert csv to json: {json_path}")
            return

        print(f"[OK] Finished plugin: {plugin.name}")

    except Exception as e:
        print(f"[Exception] Plugin: {plugin.name} / {e}")


def main():
    input_base_dir = resolve_input_dir(sys.argv, get_runtime_base_path())
    profile_dir = input_base_dir.joinpath("profiles")
    selected_profile = select_profile_dialog(profile_dir)
    if selected_profile is None:
        print("[INFO] Profile selection cancelled.")
        return
    print(f"[INFO] Profile {selected_profile} selected.")

    CSV_DIR.mkdir(exist_ok=True)
    DSD_DIR.mkdir(exist_ok=True)

    plugin_list = find_mod_plugins_from_profile(input_base_dir, selected_profile)
    exclude_plugins = CONFIG["GENERAL"].get("EXCLUDE_PLUGINS")
    filtered_plugins = [
        plugin for plugin in plugin_list
        if plugin.name not in exclude_plugins
    ]
    if not filtered_plugins:
        print("[Info] No plugins to process after exclusion.")
        return

    # 並列実行
    max_workers = min(CONFIG.getint("GENERAL", "MAX_PARALLEL"), len(filtered_plugins))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_plugin, plugin) 
            for plugin in filtered_plugins]

        # 完了待ち（例外もここで回収される）
        for future in concurrent.futures.as_completed(futures):
            future.result()

    # プラグイン更新日時のリストのスナップショットをとる
    save_current_timestamps(TIMESTAMP_FILE, filtered_plugins)

if __name__ == "__main__":
    main()
