@echo off
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name ClickForge --icon assets\clickforge.ico --add-data "assets;assets" --collect-data customtkinter clickforge.py
echo Done: dist\ClickForge.exe
