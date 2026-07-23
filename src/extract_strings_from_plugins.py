import configparser
import csv
from pathlib import Path
from src.utility import get_Config_Parser
from src.app_logger import get_logger
from sse_plugin_interface.plugin import SSEPlugin
from sse_plugin_interface.plugin_string import PluginString

# Config
CONFIG: configparser.ConfigParser = get_Config_Parser()

def extract_translatable_strings(plugin_path: Path, target_type_map: dict[str,str]):
    #プラグインから翻訳対象の文字列のみ抽出、ConfigのTARGET_TYPEで絞り込み
    log = get_logger()
    log.debug("[%s] SSEPlugin.from_file begin", plugin_path.name)
    plugin = SSEPlugin.from_file(plugin_path)
    log.debug("[%s] extract_strings begin", plugin_path.name)
    all_strings = plugin.extract_strings()
    log.debug("[%s] extract_strings done total=%s", plugin_path.name, len(all_strings))
    translatable_strings = [s for s in all_strings if isinstance(s, PluginString) and s.type in target_type_map]
    log.info("[%s] Translatable strings filtered: %s", plugin_path.name, len(translatable_strings))

    return translatable_strings

def extract_save_csv(plugin_path: Path, output_dir: Path)->bool:
    log = get_logger()
    log.info("[%s] Loading plugin for CSV extract", plugin_path.name)
    try:
        csv_path = output_dir.joinpath(f"{plugin_path.name}.csv")

        with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            # ヘッダー行
            writer.writerow(["editor_id", "form_id", "index", "type", "string"])

            strings = extract_translatable_strings(plugin_path, CONFIG["GENERAL"].get("TARGET_TYPE"))
            if not strings:
                log.info("[%s] No translatable strings (CSV header only)", plugin_path.name)
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
        log.info("[%s] CSV written: %s rows=%s", plugin_path.name, csv_path, len(strings))
    except Exception as e:
        log.exception("[%s] extract CSV error: %s", plugin_path.name, e)
        return False

    return True
