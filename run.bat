@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在啟動 聽歌尋曲小工具 (Song Finder)...
echo 啟動後請在瀏覽器開啟: http://127.0.0.1:8765
start "" "http://127.0.0.1:8765"
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8765
pause
