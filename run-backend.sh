#!/usr/bin/env bash
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv && source .venv/bin/activate
  pip install torch --index-url https://download.pytorch.org/whl/cpu
else
  source .venv/bin/activate
fi
pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created backend/.env. Edit ADMIN_TOKEN and APP_SECRET_KEY, then run again."; exit 1; }
uvicorn app.main:app --reload --port 8000
