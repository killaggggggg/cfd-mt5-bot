@echo off
cd /d "%~dp0"
echo Starting CFD MT5 Operation Center at http://127.0.0.1:8787 ...
start "" http://127.0.0.1:8787
python scripts\start_ops.py
pause
