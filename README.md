# 🧊 ICEGUARD — AI-Enabled Antarctic Sea-Ice, Iceberg Trajectory & Navigation DSS

**SIH 2026 · SIH26059 · MoES** — software-only polar navigation decision support.
Not a pretty map: a **GO / SLOW / NO-GO** advice layer a scientist can audit.

> Physics first, SAR first, cone first, human first.

## Quick start

```bash
pip install -r requirements.txt
uvicorn backend.app:app --host 0.0.0.0 --port 8000
# open http://localhost:8000
```

Demo logins: `viewer/viewer123` · `operator/operator123` · `admin/admin123`

Or with Docker: `docker build -t iceguard . && docker run -p 8000:8000 iceguard`

## Stack (per project analysis)

| Layer | Choice |
|---|---|
| API | **FastAPI** (Python 3), JWT roles: viewer / operator / admin |
| UI | **React** + **MapLibre GL** ops console (Carto open tiles, no key) |
| Store | SQLite (PostGIS-ready lat/lon schema) for bergs, voyages, audit, config |
| Science | Force-balance drift integrator, 3 ice–berg regimes, residual head, 20-member ensemble + conformal 90% cone, geodesic MAE |
| MLOps | Model registry + freeze/activate, replay skill table, audit log |

## What the console does

- **Top bar**: live UTC clock, SAR age, per-feed health dots (click for detail + outage drill)
- **Left rail**: 8 tracked bergs (search, regime badges), 4 voyages, layer toggles
- **Centre map**: ice-concentration heat, ≥90% lock zones, ice edge, berg markers, cyan forecast tracks, translucent 90% cones, ship chevron, corridor coloured by segment verdict, pinned GO/SLOW/NO-GO badge
- **Right panel**: Berg (regime, 24/48/72 h + cone radii, force breakdown, XAI sentence, replay) · Route (verdict + human reasons + override) · Skill (geodesic MAE vs 25 km target, cone honesty, registry) · Admin (thresholds, model freeze, users, audit)
- **Timeline**: scrub T−7 d → T+72 h, play voyages & drift
- **LITE toggle**: station low-bandwidth mode; **⬇ bundle**: GeoJSON ops pack for the ship

## Honest limits (from the analysis)

- 24–72 h skill only; beyond is experimental. Optical is backup-only (cloud/polar night).
- Advice capped at SLOW when SAR is stale; grounding/breakup only approximated.
- Decision support only — never an autopilot; every advice & override is logged.
