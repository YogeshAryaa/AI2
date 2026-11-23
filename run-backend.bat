@echo off
cd /d "%~dp0backend"
if not exist .venv (
  python -m venv .venv
  call .venv\Scripts\activate
  pip install torch --index-url https://download.pytorch.org/whl/cpu
) else ( call .venv\Scripts\activate )
pip install -q -r requirements.txt
if not exist .env ( copy .env.example .env & echo Created backend\.env. Edit ADMIN_TOKEN and APP_SECRET_KEY, then run again. & exit /b 1 )
uvicorn app.main:app --reload --port 8000
