@echo off
pip install -r requirements.txt
pyinstaller translate_plugin2dsd.spec --clean
pyinstaller update_translate_plugin2dsd.spec --clean