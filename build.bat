@echo off
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name ClickForge --collect-data customtkinter clickforge.py
echo Done: dist\ClickForge.exe
