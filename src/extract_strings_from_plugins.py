import configparser
import csv
from pathlib import Path
from src.utility import get_Config_Parser
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString

# Config
CONFIG: configparser.ConfigParser = get_Config_Parser()

def extract_translatable_strings(plugin_path: Path, target_type_map: dict[str,str]):
    #プラグインから翻訳対象の文字列のみ抽出、ConfigのTARGET_TYPEで絞り込み

    plugin = SSEPlugin.from_file(plugin_path)
    all_strings = plugin.extract_strings()
    translatable_strings = [s for s in all_strings if isinstance(s, PluginString) and s.type in target_type_map]
    print(f"[Info] Translatable strings filtered: {len(translatable_strings)}")

    return translatable_strings

def extract_save_csv(plugin_path: Path, output_dir: Path)->bool:
    print(f"[Info] Loading plugin: {plugin_path.name}")
    try:
        csv_path = output_dir.joinpath(f"{plugin_path.name}.csv")

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            # ヘッダー行
            writer.writerow(["editor_id", "form_id", "index", "type", "string"])

            strings = extract_translatable_strings(plugin_path, CONFIG["GENERAL"].get("TARGET_TYPE"))
            if not strings:
                print(f"[SKIP] {plugin_path.name}: No translatable strings")
                return True
            
            for s in strings:
                editor_id_string = getattr(s, "editor_id", "")
                if None == editor_id_string:
                    editor_id_string = "null" # DSD Rule
                
                index_tuple = getattr(s, "index", "")
                index = index_tuple[0] if isinstance(index_tuple, tuple) else index_tuple
                if None == index:
                    index = 0 # DSD Rule

                writer.writerow([
                    editor_id_string,
                    getattr(s, "form_id", ""),
                    index,
                    getattr(s, "type", ""),
                    getattr(s, "string", "")
                ])
    except Exception as e:
        print(f"[ERROR:{plugin_path.name}] {e}")
        return False

    return True

