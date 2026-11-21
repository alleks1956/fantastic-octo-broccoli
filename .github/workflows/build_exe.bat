@echo off
rem Run this file in the same folder as batch_photo_editor.py (Windows, Python 3.9+ installed)
python -m pip install --upgrade pip
python -m pip install pillow pyinstaller
python -m PyInstaller --onefile --windowed --name BatchPhotoEditor batch_photo_editor.py
echo.
echo Done: dist\BatchPhotoEditor.exe
pause
