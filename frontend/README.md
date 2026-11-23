# Ask my data: React frontend

```bash
npm install
npm run dev        # http://localhost:5173
``` npm run dev -- --host 0.0.0.0

1. Start the backend (`uvicorn app.main:app --reload`).
2. Open the app, expand **Backend connection**, enter `http://localhost:8000` and your `ADMIN_TOKEN`, then **Connect**.
3. **AI providers**: add your free APIs (pick a preset, paste the key, Test). Use ↑/↓ to set the order they are tried.
4. **My data**: write text, upload a file, or import a MongoDB collection.
5. **Ask**: answers show whether they came from your data, a saved answer, or AI.

If the browser blocks requests, make sure `http://localhost:5173` is listed in the backend's `CORS_ORIGINS`.
