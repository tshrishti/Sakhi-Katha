@echo off
REM Starts Sakhi Khata on http://localhost:8000 . Keys are read from .env
cd /d "%~dp0backend"
python -m pip install -q -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000
