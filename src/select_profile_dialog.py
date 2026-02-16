import tkinter as tk
from tkinter import ttk
from pathlib import Path

def get_available_profiles(profiles_dir: Path) -> list[str]:
    if not profiles_dir.exists():
        raise FileNotFoundError(f"profiles directory not found: {profiles_dir}")

    return [
        p.name for p in profiles_dir.iterdir()
        if p.is_dir()
    ]


def select_profile_dialog(profiles_dir: Path) -> str | None:
    selected_profile = {"value": None}
    profile_list = get_available_profiles(profiles_dir)

    def on_ok():
        selected_profile["value"] = combo.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    root = tk.Tk()
    root.title("Select Profile")
    root.geometry("300x120")
    root.resizable(False, False)

    ttk.Label(root, text="Select profile:").pack(pady=10)

    combo = ttk.Combobox(root, values=profile_list, state="readonly")
    combo.pack()
    combo.current(0)

    button_frame = ttk.Frame(root)
    button_frame.pack(pady=10)

    ttk.Button(button_frame, text="OK", command=on_ok).pack(side="left", padx=5)
    ttk.Button(button_frame, text="Cancel", command=on_cancel).pack(side="left", padx=5)

    # ウィンドウ右上 × ボタン対策
    root.protocol("WM_DELETE_WINDOW", on_cancel)

    root.mainloop()

    return selected_profile["value"]

