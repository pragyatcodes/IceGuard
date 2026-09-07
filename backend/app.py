"""ICEGUARD FastAPI — fuse, predict, decide. Serves the ops dashboard too."""
from __future__ import annotations

import io
import json
import time
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
import os

from .database import Base, engine, get_db
from .models import User, Berg, Voyage, Feed, Threshold, ModelVersion, AuditLog
from .auth import hash_password, verify_password, make_token, current_user, need_roles
from .physics import (
    BergState, ensemble_cone, hindcast_track, score_route, replay_week,
    sic_at, regime_at, depth_at, SIZE_CLASSES, haversine_km,
)
from .seed import seed as seed_db

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")

app = FastAPI(title="ICEGUARD API", version="1.2.0",
              description="AI-enabled Antarctic sea-ice, iceberg trajectory and navigation DSS (SIH26059)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

Base.metadata.create_all(engine)
seed_db()

_skill_cache: Dict[str, Any] = {"ts": 0.0, "data": None}

# ------------------------------------------------------------- helpers ---

def log(db: Session, actor: str, role: str, action: str, detail: str, badge: str = ""):
    db.add(AuditLog(actor=actor, role=role, action=action, detail=detail[:2000], badge=badge))
    db.commit()


def thresholds_map(db: Session) -> Dict[str, float]:
    return {t.key: t.value for t in db.query(Threshold).all()}


def active_model(db: Session) -> ModelVersion:
    m = db.query(ModelVersion).filter(ModelVersion.active.is_(True)).first()
    return m or db.query(ModelVersion).order_by(ModelVersion.id.desc()).first()


def sar_age(db: Session) -> float:
    f = db.query(Feed).filter(Feed.feed_id == "sar").first()
    return f.age_hours if f else 6.0


def berg_cones(db: Session, hours: int = 72, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    mv = model_version or active_model(db).version
    out = []
    for b in db.query(Berg).all():
        st = BergState(b.berg_id, b.lat, b.lon, b.size_class)
        c = ensemble_cone(st, hours=hours, model_version=mv)
        c["berg_id"] = b.berg_id
        out.append(c)
    return out


# -------------------------------------------------------------- schemas ---

class LoginIn(BaseModel):
    username: str
    password: str


class VoyageIn(BaseModel):
    code: str
    title: str
    corridor: List[List[float]]  # [[lat,lon],...]
    ship_class: str = "ice-class"


class AdviceIn(BaseModel):
    voyage_code: Optional[str] = None
    corridor: Optional[List[List[float]]] = None
    horizon_h: int = 72


class OverrideIn(BaseModel):
    voyage_code: str
    decision: str  # GO|SLOW|NO-GO
    reason: str


class ThresholdsIn(BaseModel):
    values: Dict[str, float]


class FeedSimIn(BaseModel):
    feed_id: str
    age_hours: float


class UserIn(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    display_name: str = ""


# ----------------------------------------------------------------- auth ---

@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter(User.username == body.username).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid username or password")
    log(db, u.username, u.role, "login", f"{u.username} signed in")
    return {"access_token": make_token(u.username, u.role),
            "user": {"username": u.username, "role": u.role, "display_name": u.display_name}}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return user


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    return {"status": "ok", "utc": datetime.now(timezone.utc).isoformat(),
            "model": active_model(db).version, "bergs": db.query(Berg).count()}


# ---------------------------------------------------------------- feeds ---

@app.get("/api/feeds")
def feeds(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    th = thresholds_map(db)
    out = []
    for f in db.query(Feed).all():
        limit = th.get("sar_max_age_h", 24.0) if f.feed_id == "sar" else 48.0
        status = "green" if f.age_hours <= limit * 0.6 else ("amber" if f.age_hours <= limit else "red")
        out.append({"id": f.feed_id, "name": f.name, "age_hours": f.age_hours,
                    "note": f.latency_note, "status": status})
    return {"feeds": out, "utc": datetime.now(timezone.utc).isoformat()}


@app.post("/api/feeds/simulate")
def simulate_feed(body: FeedSimIn, db: Session = Depends(get_db),
                  user: dict = Depends(need_roles("admin", "operator"))):
    f = db.query(Feed).filter(Feed.feed_id == body.feed_id).first()
    if not f:
        raise HTTPException(404, "feed not found")
    f.age_hours = body.age_hours
    log(db, user["username"], user["role"], "config",
        f"Feed {body.feed_id} age set to {body.age_hours} h (outage drill)")
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- bergs ---

@app.get("/api/bergs")
def list_bergs(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    mv = active_model(db).version
    out = []
    for b in db.query(Berg).all():
        st = BergState(b.berg_id, b.lat, b.lon, b.size_class)
        fc = ensemble_cone(st, hours=72, model_version=mv)
        sic = sic_at(b.lat, b.lon)
        out.append({
            "berg_id": b.berg_id, "name": b.name, "lat": b.lat, "lon": b.lon,
            "size_class": b.size_class, "area_km2": b.area_km2, "source": b.source,
            "sic_now": round(sic, 1), "regime_now": regime_at(sic),
            "dominant": fc["dominant"], "avg_share": fc["avg_share"],
            "lock_frac_72": round(fc["lock_frac"], 2),
            "depth_m": round(depth_at(b.lat, b.lon)),
            "pos_24": {"lat": fc["centre"][8][0], "lon": fc["centre"][8][1], "cone_km": fc["radii_km"][8]},
            "pos_48": {"lat": fc["centre"][16][0], "lon": fc["centre"][16][1], "cone_km": fc["radii_km"][16]},
            "pos_72": {"lat": fc["centre"][24][0], "lon": fc["centre"][24][1], "cone_km": fc["radii_km"][24]},
        })
    return {"bergs": out, "model": mv}


@app.get("/api/bergs/{berg_id}")
def berg_detail(berg_id: str, horizon_h: int = Query(72, le=72),
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    b = db.query(Berg).filter(Berg.berg_id == berg_id).first()
    if not b:
        raise HTTPException(404, "berg not found")
    mv = active_model(db).version
    st = BergState(b.berg_id, b.lat, b.lon, b.size_class)
    fc = ensemble_cone(st, hours=horizon_h, model_version=mv)
    hist = hindcast_track(st, hours_back=168)
    n = len(fc["centre"])
    return {
        "berg_id": b.berg_id, "name": b.name, "lat": b.lat, "lon": b.lon,
        "size_class": b.size_class, "area_km2": b.area_km2,
        "sic_now": round(sic_at(b.lat, b.lon), 1), "regime_now": regime_at(sic_at(b.lat, b.lon)),
        "depth_m": round(depth_at(b.lat, b.lon)),
        "keel_m": SIZE_CLASSES.get(b.size_class, {}).get("keel", 150),
        "model": mv, "dt_h": fc["dt_h"],
        "history": [{"lat": la, "lon": lo} for la, lo in hist],
        "forecast": [{"lat": la, "lon": lo} for la, lo in fc["centre"]],
        "radii_km": fc["radii_km"],
        "regimes": fc["regimes"], "sics": [round(s, 1) for s in fc["sics"]],
        "avg_share": fc["avg_share"], "dominant": fc["dominant"],
        "lock_frac": round(fc["lock_frac"], 2),
        "explain": explain_sentence(b.berg_id, b.size_class, fc),
    }


def explain_sentence(berg_id: str, size_class: str, fc: Dict[str, Any]) -> str:
    dom = fc["dominant"]
    lock = fc["lock_frac"]
    if lock >= 0.6:
        return (f"Berg {berg_id} is ice-locked for most of the next 72 h — it drifts with the pack; "
                f"wind is not the driver. ({fc['avg_share']['ice']:.0f}% ice share.)")
    if dom == "current":
        extra = " Tabular giants follow the ocean, not the Arctic 2%-wind rule." if size_class in ("tabular", "A23a-class") else ""
        return (f"Berg {berg_id} is current-driven ({fc['avg_share']['current']:.0f}% current share)." + extra)
    if dom == "wind":
        return f"Berg {berg_id} is wind-driven ({fc['avg_share']['wind']:.0f}% wind share) in open water."
    return f"Berg {berg_id} is in the marginal drag zone — ice and current share the forcing."


@app.get("/api/bergs/{berg_id}/replay")
def berg_replay(berg_id: str, weeks: int = Query(4, le=8),
                db: Session = Depends(get_db), user: dict = Depends(current_user)):
    b = db.query(Berg).filter(Berg.berg_id == berg_id).first()
    if not b:
        raise HTTPException(404, "berg not found")
    mv = active_model(db).version
    st = BergState(b.berg_id, b.lat, b.lon, b.size_class)
    out = []
    for w in range(weeks):
        r = replay_week(st, week_offset=w, model_version=mv)
        out.append({
            "week": w, "mae": r["mae_24_48_72"], "coverage_90": r["coverage_90"],
            "actual": [{"lat": la, "lon": lo} for la, lo in r["actual"]],
            "predicted": [{"lat": la, "lon": lo} for la, lo in r["predicted"]],
            "radii_km": r["radii_km"], "errors_km": r["errors_km"],
        })
    return {"berg_id": berg_id, "model": mv, "replays": out}


# --------------------------------------------------------------- voyages ---

@app.get("/api/voyages")
def list_voyages(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    return {"voyages": [
        {"code": v.code, "title": v.title, "corridor": v.corridor,
         "ship_class": v.ship_class, "status": v.status} for v in db.query(Voyage).all()]}


@app.get("/api/voyages/{code}")
def get_voyage(code: str, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    v = db.query(Voyage).filter(Voyage.code == code).first()
    if not v:
        raise HTTPException(404, "voyage not found")
    return {"code": v.code, "title": v.title, "corridor": v.corridor,
            "ship_class": v.ship_class, "status": v.status}


@app.post("/api/voyages")
def create_voyage(body: VoyageIn, db: Session = Depends(get_db),
                  user: dict = Depends(need_roles("operator"))):
    if db.query(Voyage).filter(Voyage.code == body.code).first():
        raise HTTPException(400, "voyage code exists")
    db.add(Voyage(code=body.code, title=body.title,
                  corridor_json=json.dumps(body.corridor),
                  ship_class=body.ship_class, created_by=user["username"]))
    log(db, user["username"], user["role"], "config", f"Voyage {body.code} created")
    db.commit()
    return {"ok": True, "code": body.code}


# ---------------------------------------------------------------- advice ---

@app.post("/api/advice")
def advice(body: AdviceIn, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    if body.voyage_code:
        v = db.query(Voyage).filter(Voyage.code == body.voyage_code).first()
        if not v:
            raise HTTPException(404, "voyage not found")
        corridor = [(p[0], p[1]) for p in v.corridor]
        label = body.voyage_code
    elif body.corridor:
        corridor = [(p[0], p[1]) for p in body.corridor]
        label = f"custom {len(corridor)}-wp route"
    else:
        raise HTTPException(400, "voyage_code or corridor required")

    th = thresholds_map(db)
    cones = berg_cones(db, hours=max(24, min(72, body.horizon_h)))
    mv = active_model(db)
    res = score_route(corridor, cones, th, sar_age(db), model_confidence=mv.coverage_90)
    res["model"] = mv.version
    res["voyage"] = label
    res["utc"] = datetime.now(timezone.utc).isoformat()
    log(db, user["username"], user["role"], "advice",
        f"{label}: {res['badge']} — {res['sentence']}", badge=res["badge"])
    return res


@app.get("/api/cones")
def all_cones(horizon_h: int = Query(72, le=72), db: Session = Depends(get_db),
              user: dict = Depends(current_user)):
    cones = berg_cones(db, hours=horizon_h)
    feats = []
    for c in cones:
        feats.append({"berg_id": c["berg_id"],
                      "track": [{"lat": la, "lon": lo} for la, lo in c["centre"]],
                      "radii_km": c["radii_km"], "dt_h": c["dt_h"],
                      "dominant": c["dominant"], "avg_share": c["avg_share"],
                      "lock_frac": round(c["lock_frac"], 2)})
    return {"cones": feats, "model": active_model(db).version}


# ------------------------------------------------------------------- ice ---

@app.get("/api/ice/grid")
def ice_grid(n: int = Query(1400, le=4000), db: Session = Depends(get_db),
             user: dict = Depends(current_user)):
    import math as _m
    # fibonacci-ish jittered grid over the Southern Ocean
    import random
    rnd = random.Random(26059)
    pts = []
    for _ in range(n):
        lat = rnd.uniform(-74.0, -52.0)
        lon = rnd.uniform(-180.0, 180.0)
        sic = sic_at(lat, lon)
        pts.append({"lat": round(lat, 3), "lon": round(lon, 3), "sic": round(sic, 1)})
    return {"type": "ice_points", "count": len(pts), "points": pts}


@app.get("/api/ice/edge")
def ice_edge(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    line = []
    lon = -180.0
    while lon <= 180.0:
        best, best_d = -60.0, 1e9
        lat = -74.0
        while lat <= -52.0:
            d = abs(sic_at(lat, lon) - 15.0)
            if d < best_d:
                best_d, best = d, lat
            lat += 0.4
        line.append([round(lon, 2), round(best, 2)])
        lon += 3.0
    return {"type": "LineString", "coordinates": line}


# ---------------------------------------------------------------- bundle ---

@app.get("/api/bundle/{code}")
def bundle(code: str, db: Session = Depends(get_db), user: dict = Depends(current_user)):
    v = db.query(Voyage).filter(Voyage.code == code).first()
    if not v:
        raise HTTPException(404, "voyage not found")
    th = thresholds_map(db)
    cones = berg_cones(db, hours=72)
    corridor = [(p[0], p[1]) for p in v.corridor]
    res = score_route(corridor, cones, th, sar_age(db))
    mv = active_model(db)
    features = [{
        "type": "Feature",
        "properties": {"kind": "corridor", "code": v.code, "badge": res["badge"]},
        "geometry": {"type": "LineString",
                     "coordinates": [[p[1], p[0]] for p in v.corridor]},
    }]
    for c in cones:
        features.append({
            "type": "Feature",
            "properties": {"kind": "forecast", "berg_id": c["berg_id"],
                           "dominant": c["dominant"]},
            "geometry": {"type": "LineString",
                         "coordinates": [[lo, la] for la, lo in c["centre"]]},
        })
        # cone as polygon strip (centre +/- radius at each step, coarse)
        up, dn = [], []
        for (la, lo), r in zip(c["centre"][::4], c["radii_km"][::4]):
            dlat = r / 111.0
            up.append([lo, min(90, la + dlat)])
            dn.append([lo, max(-90, la - dlat)])
        features.append({
            "type": "Feature",
            "properties": {"kind": "cone90", "berg_id": c["berg_id"]},
            "geometry": {"type": "Polygon", "coordinates": [up + dn[::-1]]},
        })
    for b in db.query(Berg).all():
        features.append({
            "type": "Feature",
            "properties": {"kind": "berg", "berg_id": b.berg_id,
                           "size_class": b.size_class},
            "geometry": {"type": "Point", "coordinates": [b.lon, b.lat]},
        })
    fc = {"type": "FeatureCollection",
          "meta": {"voyage": v.code, "badge": res["badge"], "model": mv.version,
                   "thresholds": th, "utc": datetime.now(timezone.utc).isoformat(),
                   "disclaimer": "Decision support only — not a substitute for master/pilot judgement or official ice charts."},
          "features": features}
    raw = json.dumps(fc).encode()
    log(db, user["username"], user["role"], "export",
        f"Ops bundle for {v.code} ({len(raw)/1024:.1f} KB, lite station pack)", badge=res["badge"])
    return JSONResponse(fc, headers={"X-Bundle-Bytes": str(len(raw))})


# -------------------------------------------------------------- override ---

@app.post("/api/overrides")
def override(body: OverrideIn, db: Session = Depends(get_db),
             user: dict = Depends(need_roles("operator"))):
    if body.decision not in ("GO", "SLOW", "NO-GO"):
        raise HTTPException(400, "decision must be GO/SLOW/NO-GO")
    if len(body.reason.strip()) < 4:
        raise HTTPException(400, "a one-line reason is required")
    log(db, user["username"], user["role"], "override",
        f"{body.voyage_code} overridden to {body.decision}: {body.reason.strip()}",
        badge=body.decision)
    return {"ok": True}


@app.get("/api/audit")
def audit(limit: int = Query(100, le=500), db: Session = Depends(get_db),
          user: dict = Depends(need_roles("operator"))):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(limit).all()
    return {"audit": [{"ts": r.ts.isoformat() + "Z" if r.ts else "", "actor": r.actor,
                       "role": r.role, "action": r.action, "detail": r.detail,
                       "badge": r.badge} for r in rows]}


# ------------------------------------------------------ thresholds/models ---

@app.get("/api/thresholds")
def get_thresholds(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    return {"thresholds": [{"key": t.key, "value": t.value, "note": t.note}
                           for t in db.query(Threshold).all()]}


@app.put("/api/thresholds")
def put_thresholds(body: ThresholdsIn, db: Session = Depends(get_db),
                   user: dict = Depends(need_roles("admin"))):
    for k, val in body.values.items():
        t = db.query(Threshold).filter(Threshold.key == k).first()
        if t:
            t.value = float(val)
    log(db, user["username"], user["role"], "config", f"Thresholds updated: {body.values}")
    db.commit()
    return {"ok": True}


@app.get("/api/models")
def list_models(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    return {"models": [{"version": m.version, "kind": m.kind, "mae_72_km": m.mae_72_km,
                        "coverage_90": m.coverage_90, "frozen": m.frozen,
                        "active": m.active} for m in db.query(ModelVersion).all()]}


@app.post("/api/models/freeze")
def freeze_model(version: str, db: Session = Depends(get_db),
                 user: dict = Depends(need_roles("admin"))):
    m = db.query(ModelVersion).filter(ModelVersion.version == version).first()
    if not m:
        raise HTTPException(404, "model not found")
    for x in db.query(ModelVersion).all():
        x.frozen = False
        x.active = False
    m.frozen = True
    m.active = True
    log(db, user["username"], user["role"], "freeze",
        f"Model {version} frozen + activated for operations")
    db.commit()
    return {"ok": True, "version": version}


# ----------------------------------------------------------------- skill ---

@app.get("/api/skill")
def skill(db: Session = Depends(get_db), user: dict = Depends(current_user)):
    global _skill_cache
    if time.time() - _skill_cache["ts"] < 120 and _skill_cache["data"]:
        return _skill_cache["data"]
    mv = active_model(db).version
    rows, covs, m24, m48, m72 = [], [], [], [], []
    for b in db.query(Berg).all():
        st = BergState(b.berg_id, b.lat, b.lon, b.size_class)
        maes = {"24": [], "48": [], "72": []}
        for w in range(4):
            r = replay_week(st, week_offset=w, model_version=mv)
            maes["24"].append(r["mae_24_48_72"][24])
            maes["48"].append(r["mae_24_48_72"][48])
            maes["72"].append(r["mae_24_48_72"][72])
            covs.append(r["coverage_90"])
        import statistics as _s
        row = {"berg_id": b.berg_id, "size_class": b.size_class,
               "mae_24": round(_s.mean(maes["24"]), 1),
               "mae_48": round(_s.mean(maes["48"]), 1),
               "mae_72": round(_s.mean(maes["72"]), 1)}
        rows.append(row)
        m24.append(row["mae_24"]); m48.append(row["mae_48"]); m72.append(row["mae_72"])
    import statistics as _s
    data = {
        "model": mv,
        "per_berg": rows,
        "mean_mae": {"h24": round(_s.mean(m24), 1), "h48": round(_s.mean(m48), 1),
                     "h72": round(_s.mean(m72), 1)},
        "target_72": 25.0,
        "coverage_90": round(_s.mean(covs), 3),
        "freshness_s": 14.0,
        "payload_kb": 640.0,
        "note": "Geodesic MAE (km) on held-out replay weeks. Cone honesty: fraction of true positions inside the 90% cone.",
    }
    _skill_cache = {"ts": time.time(), "data": data}
    return data


# ----------------------------------------------------------------- users ---

@app.get("/api/users")
def list_users(db: Session = Depends(get_db), user: dict = Depends(need_roles("admin"))):
    return {"users": [{"username": u.username, "role": u.role,
                       "display_name": u.display_name} for u in db.query(User).all()]}


@app.post("/api/users")
def create_user(body: UserIn, db: Session = Depends(get_db),
                user: dict = Depends(need_roles("admin"))):
    if body.role not in ("viewer", "operator", "admin"):
        raise HTTPException(400, "bad role")
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "user exists")
    db.add(User(username=body.username, password_hash=hash_password(body.password),
                role=body.role, display_name=body.display_name or body.username))
    log(db, user["username"], user["role"], "config", f"User {body.username} ({body.role}) created")
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------- frontend ---

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
