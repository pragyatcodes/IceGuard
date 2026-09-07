# 🌍 Get a public link for ICEGUARD (free)

The sandbox preview link only works **inside Arena** (it needs a traffic token).
For a link you can share with anyone (judges, teammates), deploy once — free:

## Option A — Render.com (easiest, ~5 min, free)

1. Zip/upload this `iceguard/` folder to a **GitHub repo** (or use the `iceguard.zip`).
2. Go to **render.com → New → Web Service →** connect the repo.
3. Settings:
   - **Environment:** Docker (Dockerfile is already included)
   - **Port:** 8000 (auto-detected from Dockerfile)
   - Free plan is enough.
4. Deploy → you get `https://iceguard-xxxx.onrender.com` — **shareable with anyone**, no token needed.
5. Logins: `viewer/viewer123`, `operator/operator123`, `admin/admin123`.

> Note: Render free tier sleeps after inactivity — first load takes ~30 s to wake up.

## Option B — Railway.app (free trial, no sleep)

1. **railway.app → New Project → Deploy from Repo** (or empty + upload).
2. It auto-detects the Dockerfile → Deploy.
3. **Settings → Networking → Generate Domain** → public `https://....up.railway.app`.

## Option C — Run on your own laptop (demo offline*)

```bash
# needs Python 3.10+
pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

\*Map tiles + CDN libraries need internet; the science/API runs fully offline.

## Option D — Show it right now, inside Arena

Open the **LIVE PREVIEW** panel for **“ICEGUARD Website”** in this chat —
Arena attaches the traffic token automatically, so it just works there.
