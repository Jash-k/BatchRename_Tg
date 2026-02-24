# 📁 Telegram File Renamer — Render Deployment

Batch rename 267+ Telegram files **without downloading or uploading** — deployed on [Render.com](https://render.com) for free.

## 🚀 One-Click Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

1. Fork / push this repo to GitHub
2. Go to [render.com](https://render.com) → New → **Web Service**
3. Connect your GitHub repo
4. Render auto-detects `render.yaml` + `Dockerfile`
5. Click **Deploy** — live in ~3 minutes!

---

## 🏗️ Stack

| Layer | Tech |
|-------|------|
| Frontend | React 19 + Vite + Tailwind CSS 4 |
| Backend | FastAPI + Uvicorn (Python 3.11) |
| Telegram | Telethon (MTProto) |
| Deploy | Render.com (Docker) |
| Transport | WebSocket (live logs) + REST |

---

## 💡 How It Works (No Re-Upload)

Telegram identifies files by their **file_id** (content hash). Telethon's `send_file()` called with an existing `document` object + new filename attributes creates a new message referencing the same stored bytes — **zero bytes transferred**. The original message is then deleted.

---

## 🖥️ Run Locally

```bash
# Terminal 1: Backend
cd backend
pip install -r requirements.txt
python server.py
# → http://localhost:8000

# Terminal 2: Frontend (dev mode)
npm install
npm run dev
# → http://localhost:5173
```

Set `VITE_API_URL=http://localhost:8000` in a `.env` file for local dev.

---

## 🐳 Docker

```bash
docker build -t tg-renamer .
docker run -p 8000:8000 tg-renamer
# → http://localhost:8000
```

---

## 📂 Project Structure

```
├── Dockerfile              # Multi-stage: builds React → Python server
├── render.yaml             # Render deployment config
├── README.md
├── backend/
│   ├── server.py           # FastAPI + Telethon rename logic
│   └── requirements.txt    # Python deps
├── src/
│   ├── App.tsx             # React frontend
│   ├── main.tsx
│   └── index.css
├── index.html
├── package.json
└── vite.config.ts
```

---

## ⚠️ Notes

- You must be an **admin** of the channel to delete original messages
- Script adds **1.5s delay** between renames → 267 files ≈ 7 minutes
- Save the **session string** shown after first login to skip OTP next time
- Flood wait is handled automatically (30s pause)
