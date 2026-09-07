"""Seed database: users, bergs, voyages, feeds, thresholds, model registry."""
from __future__ import annotations

import json
from .database import Base, engine, SessionLocal
from .models import User, Berg, Voyage, Feed, Threshold, ModelVersion
from .auth import hash_password


USERS = [
    ("viewer", "viewer123", "viewer", "Dr. Meera (Viewer — Scientist/Student)"),
    ("operator", "operator123", "operator", "Duty Officer (NCPOR Operator)"),
    ("admin", "admin123", "admin", "MoES Mentor (Admin)"),
]

# [berg_id, name, lat, lon, size_class, area_km2]
BERGS = [
    ["A23a", "A23a — Weddell giant", -64.8, -41.5, "A23a-class", 3400.0],
    ["B22a", "B22a — Thwaites remnant", -68.9, -105.5, "tabular", 620.0],
    ["C33", "C33 — Ross Sea tabular", -72.4, 178.5, "tabular", 310.0],
    ["D28", "D28 — Amery calving", -67.2, 72.8, "large", 95.0],
    ["E19", "E19 — Prydz Bay growler field", -67.9, 75.6, "medium", 6.5],
    ["F44", "F44 — Lazarev approach", -69.6, 14.2, "large", 44.0],
    ["G07", "G07 — Bharati corridor watch", -68.3, 74.1, "medium", 8.2],
    ["H11", "H11 — Maitri corridor watch", -70.2, 12.6, "small", 0.8],
]

# Maitri 70.77S 11.73E | Bharati 69.41S 76.19E
VOYAGES = [
    {
        "code": "RES2026-BHARATI",
        "title": "Resupply 2026 – Bharati (Prydz Bay approach)",
        "corridor": [[-62.0, 68.0], [-64.5, 70.5], [-66.5, 72.5], [-68.3, 74.1],
                      [-69.0, 75.2], [-69.41, 76.19]],
        "ship_class": "ice-class",
    },
    {
        "code": "RES2026-MAITRI",
        "title": "Resupply 2026 – Maitri (Lazarev approach)",
        "corridor": [[-60.0, 8.0], [-63.5, 9.5], [-66.5, 10.8], [-69.0, 11.5],
                      [-70.2, 11.7], [-70.77, 11.73]],
        "ship_class": "ice-class",
    },
    {
        "code": "DEMO-NOGO",
        "title": "Demo — corridor through berg cone (expect NO-GO)",
        "corridor": [[-63.0, -44.0], [-64.0, -43.0], [-64.8, -41.5], [-65.6, -40.0]],
        "ship_class": "thin-hull",
    },
    {
        "code": "TRANSIT-INDIAN",
        "title": "Offshore transit — open water (expect GO)",
        "corridor": [[-48.0, 30.0], [-50.5, 38.0], [-52.5, 46.0], [-54.0, 54.0]],
        "ship_class": "ice-class",
    },
]

FEEDS = [
    ("sar", "Sentinel-1 SAR (Copernicus)", 6.0, "GRD IW, all-weather detector input"),
    ("sic", "AMSR2 / NSIDC ice concentration", 11.0, "daily SIC, lock-zone source"),
    ("wind", "ERA5 surface wind", 3.0, "hourly reanalysis + forecast"),
    ("current", "CMEMS ocean current", 9.0, "main driver of tabular bergs"),
    ("mosdac", "MOSDAC mirror (ISRO)", 30.0, "Indian gateway ingest"),
    ("nsidc", "NSIDC Sea Ice Index", 5.0, "extent + training labels"),
]

THRESHOLDS = [
    ("go_ice_max", 35.0, "Max route SIC (%) still eligible for GO"),
    ("hull_buffer_km", 5.0, "Safety buffer around hull for cone tests"),
    ("sar_max_age_h", 24.0, "SAR older than this caps advice at SLOW"),
    ("min_confidence", 0.55, "Model confidence floor; below => NO-GO"),
    ("cone_quantile", 90.0, "Conformal cone coverage target (%)"),
]

MODELS = [
    ("v1.0.0", "physics-only", 31.4, 0.83, False, False),
    ("v1.1.0", "physics+lstm-blackbox", 27.9, 0.86, False, False),
    ("v1.2.0", "physics+residual (operational)", 18.6, 0.91, True, True),
]


def seed():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(User).count() == 0:
            for u, p, r, d in USERS:
                db.add(User(username=u, password_hash=hash_password(p), role=r, display_name=d))
        if db.query(Berg).count() == 0:
            for b in BERGS:
                db.add(Berg(berg_id=b[0], name=b[1], lat=b[2], lon=b[3],
                            size_class=b[4], area_km2=b[5]))
        if db.query(Voyage).count() == 0:
            for v in VOYAGES:
                db.add(Voyage(code=v["code"], title=v["title"],
                              corridor_json=json.dumps(v["corridor"]),
                              ship_class=v["ship_class"], created_by="seed"))
        if db.query(Feed).count() == 0:
            for f in FEEDS:
                db.add(Feed(feed_id=f[0], name=f[1], age_hours=f[2], latency_note=f[3]))
        if db.query(Threshold).count() == 0:
            for t in THRESHOLDS:
                db.add(Threshold(key=t[0], value=t[1], note=t[2]))
        if db.query(ModelVersion).count() == 0:
            for m in MODELS:
                db.add(ModelVersion(version=m[0], kind=m[1], mae_72_km=m[2],
                                    coverage_90=m[3], frozen=m[4], active=m[5]))
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("seeded", __import__("os").path.abspath("iceguard.db"))
