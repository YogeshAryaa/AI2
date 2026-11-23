"""Load the ready-made knowledge base (backend/seed_data/*.json) into the running backend.

Usage (backend must be running):
    cd backend
    .venv\\Scripts\\python seed_data.py              # uses http://localhost:8000 and ADMIN_TOKEN from .env
    .venv\\Scripts\\python seed_data.py --url https://ai.yourcompany.com

Safe to run again: every item has a fixed id, so edited answers are updated, not duplicated.

Add your own answers: copy any JSON file in seed_data/, keep the same shape:
    {"collection": "kb_<name>", "tags": [...], "items": [{"id": "...", "q": "...", "also": [...], "a": "..."}]}
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def admin_token() -> str:
    env = HERE / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        if line.strip().startswith("ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("ADMIN_TOKEN not found in backend/.env")


def to_record(item: dict, tags: list[str]) -> dict:
    text = f"Q: {item['q']}\n"
    if item.get("also"):
        text += "Also asked as: " + " | ".join(item["also"]) + "\n"
    text += f"A: {item['a']}"
    return {"id": item["id"], "title": item["q"], "text": text, "tags": tags + item.get("tags", [])}


def post(url: str, token: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-API-Key": token})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--namespace", default=None, help="default namespace if omitted")
    args = ap.parse_args()
    token, base = admin_token(), args.url.rstrip("/")
    total = 0
    for f in sorted((HERE / "seed_data").glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        records = [to_record(i, data.get("tags", [])) for i in data["items"]]
        body = {"collection": data["collection"], "records": records, "wait": True}
        if args.namespace:
            body["namespace"] = args.namespace
        try:
            res = post(f"{base}/records", token, body)
        except urllib.error.HTTPError as e:
            sys.exit(f"{f.name}: HTTP {e.code} {e.read().decode(errors='ignore')[:300]}")
        except urllib.error.URLError as e:
            sys.exit(f"Cannot reach {base} ({e.reason}). Is the backend running?")
        total += len(records)
        print(f"{f.name}: {len(records)} answers loaded -> {res}")
    print(f"Done. {total} answers are now in your knowledge base.")


if __name__ == "__main__":
    main()
