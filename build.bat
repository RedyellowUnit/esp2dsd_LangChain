@echo off
setlocal

pip install -r requirements.txt
if errorlevel 1 exit /b 1

pyinstaller translate_plugin2dsd.spec --clean
if errorlevel 1 exit /b 1

REM PyInstaller 同梱漏れ対策: 7zz を dist 横にもコピー（手動フォールバック用）
python -c "import py7zz, shutil, pathlib; src=pathlib.Path(py7zz.__file__).parent/'bin'; dst=pathlib.Path('dist'); dst.mkdir(exist_ok=True);
[shutil.copy2(src/n, dst/n) for n in ('7zz.exe','7z.dll') if (src/n).exists()]; print('copied 7zz next to dist')"

echo.
echo Build done: dist\translate_plugin2dsd.exe
echo If RAR extract still fails, keep 7zz.exe and 7z.dll beside the exe.
endlocal
