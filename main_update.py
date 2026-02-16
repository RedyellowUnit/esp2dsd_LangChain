import sys
from pathlib import Path
import configparser
import concurrent.futures

from src.utility import resolve_input_dir, find_mod_plugins_from_profile, get_runtime_base_path, get_Config_Parser, save_current_timestamps, load_previous_timestamps
from src.select_profile_dialog import select_profile_dialog
from main import process_plugin

BASE_PATH: Path = get_runtime_base_path()
CSV_DIR: Path = BASE_PATH.joinpath("Translated_Csv")
DSD_DIR: Path = BASE_PATH.joinpath("Translated_DSD")
TIMESTAMP_FILE: Path = BASE_PATH.joinpath("plugin_timestamps.txt")

# Config
CONFIG: configparser.ConfigParser = get_Config_Parser()

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
    
    # 除外プラグインをリスト
    exclude_plugins = CONFIG["GENERAL"].get("EXCLUDE_PLUGINS")
    filtered_plugins = [
        plugin for plugin in updated_plugins
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
    save_current_timestamps(TIMESTAMP_FILE, plugin_list)

if __name__ == "__main__":
    main()
