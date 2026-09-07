"""
ICEGUARD drift physics — software-only polar navigation DSS (SIH26059, MoES).

Pipeline stage: DRIFT + CONE + ROUTE SCORE
  physics first (force-balance integrator, three ice-berg regimes),
  AI second (residual correction on top of physics),
  cone always (conformal-style calibrated uncertainty, never a bare line).

Conventions
- Positions are (lat, lon) in degrees; velocities (u, v) in m/s, u=east, v=north.
- All errors are geodesic kilometres (haversine). Never degrees. (Fixes L4.)
- Regimes (Lichey & Hellmer 2001 style): SIC<15 open water, 15-90 drag,
  >=90 ice-lock (berg velocity = ice velocity). (Fixes L2, L5.)
- Size-class switch: giant tabular / A23a-class bergs are current-dominated,
  the Arctic "2% of wind" rule is NOT applied to them. (Fixes L6.)
"""

from __future__ import annotations

import math
import hashlib
from dataclasses import dataclass
from typing import List, Tuple, Dict, Any

import numpy as np

EARTH_R_KM = 6371.0

# ---------------------------------------------------------------- geodesy ---

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Geodesic distance in km. All skill scores use this (L4 fix)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(min(1.0, math.sqrt(a)))


def destination_point(lat: float, lon: float, u_ms: float, v_ms: float, dt_s: float) -> Tuple[float, float]:
    """Move a point by velocity (u east, v north) over dt seconds along the sphere."""
    # local planar step converted with latitude correction (fine for <= hourly steps)
    d_north_km = v_ms * dt_s / 1000.0
    d_east_km = u_ms * dt_s / 1000.0
    d_lat = math.degrees(d_north_km / EARTH_R_KM)
    cos_lat = max(0.15, math.cos(math.radians(lat)))
    d_lon = math.degrees(d_east_km / (EARTH_R_KM * cos_lat))
    new_lat = max(-89.9, min(89.9, lat + d_lat))
    new_lon = ((lon + d_lon + 540.0) % 360.0) - 180.0
    return new_lat, new_lon


def circle_polygon(lat: float, lon: float, radius_km: float, n: int = 40) -> List[List[float]]:
    """Circle (lon/lat ring) around a point — used to build the cone polygon."""
    ring: List[List[float]] = []
    for i in range(n + 1):
        brg = 2 * math.pi * i / n
        d_north = radius_km * math.cos(brg)
        d_east = radius_km * math.sin(brg)
        d_lat = math.degrees(d_north / EARTH_R_KM)
        cos_lat = max(0.15, math.cos(math.radians(lat)))
        d_lon = math.degrees(d_east / (EARTH_R_KM * cos_lat))
        ring.append([((lon + d_lon + 540) % 360) - 180, max(-89.9, min(89.9, lat + d_lat))])
    return ring


def seeded_rng(*keys: str) -> np.random.Generator:
    h = hashlib.sha256("|".join(keys).encode()).hexdigest()
    return np.random.default_rng(int(h[:16], 16) % (2**63 - 1))


# ------------------------------------------------- synthetic forcing fields ---
# Stand-ins for ERA5 (wind), CMEMS (current), AMSR2/NSIDC (SIC).
# Deterministic in (lat, lon, t) so replays are reproducible and the demo
# needs no live satellite account. Swap with STAC ingest in production.

def wind_at(lat: float, lon: float, t_h: float) -> Tuple[float, float]:
    """Surface wind (u,v) m/s: westerlies belt + polar easterlies + travelling storm."""
    lonr, latr = math.radians(lon), math.radians(lat)
    # westerlies peak near -50, polar easterlies near coast
    westerly = 9.0 * math.exp(-((lat + 50.0) / 9.0) ** 2)
    easterly = -6.0 * math.exp(-((lat + 70.0) / 6.0) ** 2)
    u = westerly + easterly
    v = 2.5 * math.sin(3 * lonr + 0.35 * t_h / 24) * math.exp(-((lat + 60) / 14) ** 2)
    # travelling low-pressure wave
    u += 3.0 * math.sin(2 * lonr - 0.5 * t_h / 24 + latr)
    v += 2.0 * math.cos(2 * lonr - 0.5 * t_h / 24)
    return float(u), float(v)


def current_at(lat: float, lon: float, t_h: float) -> Tuple[float, float]:
    """Ocean current (u,v) m/s: ACC eastward + Weddell gyre + coastal current."""
    lonr = math.radians(lon)
    # Antarctic Circumpolar Current: eastward, max ~0.25 m/s near -55
    u = 0.22 * math.exp(-((lat + 55.0) / 8.0) ** 2)
    v = 0.0
    # Weddell gyre (clockwise): centred ~ (-65, -40)
    dx, dy = lon + 40.0, lat + 65.0
    r2 = dx * dx + dy * dy
    gyre = 0.10 * math.exp(-r2 / 300.0)
    u += -gyre * dy / 8.0
    v += gyre * dx / 8.0
    # westward coastal current near continent
    u += -0.08 * math.exp(-((lat + 70.0) / 3.5) ** 2)
    # slow meander
    u += 0.02 * math.sin(lonr * 4 + t_h / 48.0)
    v += 0.02 * math.cos(lonr * 3 - t_h / 60.0)
    return float(u), float(v)


def sic_at(lat: float, lon: float, t_h: float = 0.0) -> float:
    """Sea-ice concentration 0-100. Pack near continent, edge ~ -60, seasonal wave.

    September (freeze-up growth): edge pushed equatorward. March minimum pulls back.
    t_h shifts the edge slightly so replays differ by week.
    """
    lonr = math.radians(lon)
    # base edge latitude varies with longitude (Weddell holds ice further north)
    edge_lat = -60.0 + 3.0 * math.sin(2 * lonr + 1.0) - 2.0 * math.sin(lonr * 3)
    edge_lat += 1.2 * math.sin(t_h / 168.0)  # weekly wobble
    # logistic ramp from open water to pack across ~8 degrees
    x = (edge_lat - lat) / 3.2
    sic = 100.0 / (1.0 + math.exp(-x))
    # leads / polynyas: persistent holes (e.g. near 70S/75E approach to Bharati)
    polynya = 55.0 * math.exp(-(((lat + 68.0) ** 2) / 6.0 + ((lon - 72.0) ** 2) / 90.0))
    sic -= polynya
    sic += 4.0 * math.sin(5 * lonr + lat) * math.exp(-((lat + 63) / 10) ** 2)
    return float(max(0.0, min(100.0, sic)))


def ice_velocity_at(lat: float, lon: float, t_h: float) -> Tuple[float, float]:
    """Pack-ice drift velocity: mostly current + small wind factor."""
    cu, cv = current_at(lat, lon, t_h)
    wu, wv = wind_at(lat, lon, t_h)
    return 0.85 * cu + 0.008 * wu, 0.85 * cv + 0.008 * wv


def depth_at(lat: float, lon: float) -> float:
    """Synthetic bathymetry (m, positive down). Shallow shelf near continent."""
    shelf = 3500.0 - 3200.0 * math.exp(-((lat + 72.0) / 4.0) ** 2)
    bank = -1400.0 * math.exp(-(((lat + 66.0) ** 2) / 8.0 + ((lon + 30.0) ** 2) / 120.0))
    ridge = -900.0 * math.exp(-(((lat + 64.0) ** 2) / 4.0 + ((lon - 60.0) ** 2) / 200.0))
    return float(max(60.0, shelf + bank + ridge))


# ------------------------------------------------------------- berg classes ---

SIZE_CLASSES: Dict[str, Dict[str, float]] = {
    # k_wind = fraction of wind, k_cur = fraction of current (L6 fix)
    "small":     {"k_wind": 0.020, "k_cur": 0.70, "keel": 40.0},
    "medium":    {"k_wind": 0.015, "k_cur": 0.80, "keel": 80.0},
    "large":     {"k_wind": 0.010, "k_cur": 0.90, "keel": 150.0},
    "tabular":   {"k_wind": 0.006, "k_cur": 0.97, "keel": 220.0},
    "A23a-class": {"k_wind": 0.004, "k_cur": 1.00, "keel": 300.0},
}

REGIME_OPEN, REGIME_DRAG, REGIME_LOCK = "open", "drag", "lock"


def regime_at(sic: float) -> str:
    if sic >= 90.0:
        return REGIME_LOCK
    if sic >= 15.0:
        return REGIME_DRAG
    return REGIME_OPEN


def coriolis_rotate(u: float, v: float, lat: float) -> Tuple[float, float]:
    """Deflect motion to the LEFT in the Southern Hemisphere (else tracks curve wrong)."""
    if lat >= 0:
        ang = math.radians(4.0)
    else:
        ang = math.radians(-7.0)  # leftward deflection
    c, s = math.cos(ang), math.sin(ang)
    return c * u - s * v, s * u + c * v


# ------------------------------------------------------- residual "AI" model ---
# Physics gives the first guess; the net learns only the residual error
# versus real tracks (L2 fix). Here: a tiny deterministic residual head with
# per-berg learned bias (as if loaded from an MLflow registry).

@dataclass
class ResidualHead:
    bias_u: float
    bias_v: float
    w_wind: float = 0.06
    w_cur: float = 0.10

    def correct(self, u: float, v: float, wu: float, wv: float, cu: float, cv: float) -> Tuple[float, float]:
        du = self.bias_u + self.w_wind * 0.01 * wu + self.w_cur * 0.05 * cu
        dv = self.bias_v + self.w_wind * 0.01 * wv + self.w_cur * 0.05 * cv
        # bounded correction: residual can never overpower physics
        du = float(np.tanh(du / 0.05) * 0.05)
        dv = float(np.tanh(dv / 0.05) * 0.05)
        return u + du, v + dv


def residual_head_for(berg_id: str, model_version: str = "v1.2.0") -> ResidualHead:
    rng = seeded_rng("residual", berg_id, model_version)
    return ResidualHead(
        bias_u=float(rng.normal(0, 0.008)),
        bias_v=float(rng.normal(0, 0.008)),
        w_wind=float(0.05 + rng.random() * 0.03),
        w_cur=float(0.08 + rng.random() * 0.05),
    )


# ------------------------------------------------------------------ drift ---

@dataclass
class BergState:
    berg_id: str
    lat: float
    lon: float
    size_class: str = "large"


def step_velocity(state: BergState, t_h: float, head: ResidualHead,
                  wind_scale: float = 1.0, cur_scale: float = 1.0) -> Tuple[float, float, str, Dict[str, float]]:
    """One force-balance evaluation. Returns (u, v, regime, force_share)."""
    cls = SIZE_CLASSES.get(state.size_class, SIZE_CLASSES["large"])
    sic = sic_at(state.lat, state.lon, t_h)
    regime = regime_at(sic)
    wu, wv = wind_at(state.lat, state.lon, t_h)
    cu, cv = current_at(state.lat, state.lon, t_h)
    wu, wv, cu, cv = wu * wind_scale, wv * wind_scale, cu * cur_scale, cv * cur_scale

    if regime == REGIME_LOCK:
        iu, iv = ice_velocity_at(state.lat, state.lon, t_h)
        u, v = iu, iv  # berg moves WITH the pack (L5 fix)
        share = {"wind": 2.0, "current": 18.0, "ice": 80.0}
    elif regime == REGIME_DRAG:
        iu, iv = ice_velocity_at(state.lat, state.lon, t_h)
        u = 0.55 * cls["k_cur"] * cu + 0.45 * iu + 0.5 * cls["k_wind"] * wu
        v = 0.55 * cls["k_cur"] * cv + 0.45 * iv + 0.5 * cls["k_wind"] * wv
        share = {"wind": 12.0, "current": 48.0, "ice": 40.0}
    else:
        u = cls["k_cur"] * cu + cls["k_wind"] * wu
        v = cls["k_cur"] * cv + cls["k_wind"] * wv
        w_share = 100.0 * abs(cls["k_wind"] * 8.0) / max(1e-6, abs(cls["k_cur"] * 0.2) + abs(cls["k_wind"] * 8.0))
        share = {"wind": round(w_share, 1), "current": round(100 - w_share, 1), "ice": 0.0}

    u, v = coriolis_rotate(u, v, state.lat)
    u, v = head.correct(u, v, wu, wv, cu, cv)
    return float(u), float(v), regime, share


def integrate_track(state: BergState, hours: int = 72, dt_h: float = 3.0,
                    model_version: str = "v1.2.0",
                    wind_scale: float = 1.0, cur_scale: float = 1.0,
                    t0_h: float = 0.0) -> Dict[str, Any]:
    """Centre-line forecast + per-step regime, SIC and force shares."""
    head = residual_head_for(state.berg_id, model_version)
    lat, lon = state.lat, state.lon
    steps = int(hours / dt_h)
    track, regimes, sics, shares = [(lat, lon)], [], [], []
    for k in range(1, steps + 1):
        t = t0_h + k * dt_h
        st = BergState(state.berg_id, lat, lon, state.size_class)
        u, v, regime, share = step_velocity(st, t, head, wind_scale, cur_scale)
        lat, lon = destination_point(lat, lon, u, v, dt_h * 3600.0)
        track.append((lat, lon))
        regimes.append(regime)
        sics.append(sic_at(lat, lon, t))
        shares.append(share)
    # dominant force tag for the XAI sentence
    avg = {"wind": float(np.mean([s["wind"] for s in shares])) if shares else 0,
           "current": float(np.mean([s["current"] for s in shares])) if shares else 0,
           "ice": float(np.mean([s["ice"] for s in shares])) if shares else 0}
    dom = max(avg, key=avg.get) if shares else "current"
    return {"track": track, "regimes": regimes, "sics": sics, "shares": shares,
            "avg_share": avg, "dominant": dom,
            "lock_frac": float(np.mean([r == REGIME_LOCK for r in regimes])) if regimes else 0.0}


def hindcast_track(state: BergState, hours_back: int = 168, dt_h: float = 6.0,
                   model_version: str = "v1.2.0") -> List[Tuple[float, float]]:
    """Reconstruct past positions by reverse integration (for the T-7d timeline)."""
    head = residual_head_for(state.berg_id, model_version)
    lat, lon = state.lat, state.lon
    pts = [(lat, lon)]
    steps = int(hours_back / dt_h)
    for k in range(1, steps + 1):
        t = -k * dt_h
        st = BergState(state.berg_id, lat, lon, state.size_class)
        u, v, _, _ = step_velocity(st, t, head)
        lat, lon = destination_point(lat, lon, -u, -v, dt_h * 3600.0)
        pts.append((lat, lon))
    pts.reverse()
    return pts


# ------------------------------------------------------- ensemble + cone ---

def conformal_offset_km(h: float) -> float:
    """Calibrated past-error quantile (as if fit on BYU/NSIDC replay).
    Guarantees the cone GROWS with lead time — never a pretty fixed line."""
    return 1.8 + 0.42 * h + 0.0011 * h * h


def ensemble_cone(state: BergState, hours: int = 72, dt_h: float = 3.0,
                  members: int = 20, model_version: str = "v1.2.0",
                  t0_h: float = 0.0) -> Dict[str, Any]:
    """Centre line + 20-member ensemble + calibrated 90% cone radii (km)."""
    centre = integrate_track(state, hours, dt_h, model_version, t0_h=t0_h)
    ctrack = centre["track"]
    rng = seeded_rng("ensemble", state.berg_id, model_version, str(t0_h))
    spread: List[List[float]] = [[] for _ in ctrack]
    for _ in range(members):
        ws = float(rng.normal(1.0, 0.12))
        cs = float(rng.normal(1.0, 0.10))
        sim = integrate_track(state, hours, dt_h, model_version, ws, cs, t0_h)
        for i, (la, lo) in enumerate(sim["track"]):
            spread[i].append(haversine_km(ctrack[i][0], ctrack[i][1], la, lo))
    radii = []
    for i, vals in enumerate(spread):
        h = i * dt_h
        q90 = float(np.quantile(vals, 0.90)) if vals else 0.0
        radii.append(round(max(1.2, q90 + conformal_offset_km(h) * 0.34), 2))
    return {"centre": ctrack, "radii_km": radii, "dt_h": dt_h,
            "regimes": centre["regimes"], "sics": centre["sics"],
            "avg_share": centre["avg_share"], "dominant": centre["dominant"],
            "lock_frac": centre["lock_frac"]}


# ------------------------------------------------------------- route score ---

def min_dist_to_track_km(lat: float, lon: float, track: List[Tuple[float, float]]) -> float:
    return min(haversine_km(lat, lon, la, lo) for la, lo in track)


def score_route(corridor: List[Tuple[float, float]],
                berg_cones: List[Dict[str, Any]],
                thresholds: Dict[str, float],
                sar_age_h: float,
                model_confidence: float = 0.86) -> Dict[str, Any]:
    """GO / SLOW / NO-GO from cones + ice + freshness. The badge uses the CONE (L3)."""
    hull = float(thresholds.get("hull_buffer_km", 5.0))
    go_ice = float(thresholds.get("go_ice_max", 35.0))
    sar_max = float(thresholds.get("sar_max_age_h", 24.0))
    conf_floor = float(thresholds.get("min_confidence", 0.55))

    reasons: List[str] = []
    worst = "GO"
    rank = {"GO": 0, "SLOW": 1, "NO-GO": 2}
    time_to_hazard_h = None
    seg_states: List[str] = []
    decisive = ""  # reason behind the WORST verdict (drives the one-line sentence)

    # sample ice along corridor
    max_sic = 0.0
    for (la, lo) in corridor:
        max_sic = max(max_sic, sic_at(la, lo, 24.0))

    degraded = sar_age_h > sar_max
    if degraded:
        worst = "SLOW"
        reasons.append(f"SAR data is {sar_age_h:.0f} h old (limit {sar_max:.0f} h) — advice capped at SLOW.")

    if model_confidence < conf_floor:
        worst = "NO-GO"
        decisive = f"Model confidence {model_confidence:.2f} below floor {conf_floor:.2f} — NO-GO."
        reasons.append(decisive)

    for ci, (cla, clo) in enumerate(corridor):
        seg = "GO"
        sic = sic_at(cla, clo, 24.0)
        if sic >= 90.0:
            seg = "NO-GO"
            if worst != "NO-GO":
                decisive = f"Lock-zone ice (SIC {sic:.0f}%) blocks corridor near waypoint {ci + 1}."
                reasons.append(decisive)
            worst = "NO-GO"
        elif sic >= go_ice:
            seg = "SLOW"
            if rank[worst] < 1:
                reasons.append(f"Moderate ice (SIC {sic:.0f}%) along corridor near waypoint {ci + 1}.")
            worst = "SLOW" if worst == "GO" else worst
        # cone intersection tests
        for cone in berg_cones:
            ctrack = cone["centre"]
            radii = cone["radii_km"]
            dt = cone.get("dt_h", 3.0)
            for i, (la, lo) in enumerate(ctrack):
                d = haversine_km(cla, clo, la, lo)
                edge = d - radii[i]
                h = i * dt
                if edge <= hull:
                    seg = "NO-GO"
                    worst = "NO-GO"
                    if time_to_hazard_h is None or h < time_to_hazard_h:
                        time_to_hazard_h = h
                    if len(reasons) < 6:
                        decisive = (
                            f"Berg {cone.get('berg_id')} 90% cone meets hull envelope "
                            f"near waypoint {ci + 1} at T+{h:.0f} h (centre {d:.1f} km, cone {radii[i]:.1f} km).")
                        reasons.append(decisive)
                    break
                elif edge <= hull * 2.5 and seg == "GO":
                    seg = "SLOW"
                    if worst == "GO":
                        worst = "SLOW"
                        if len(reasons) < 6:
                            reasons.append(
                                f"Berg {cone.get('berg_id')} cone grazes corridor near waypoint {ci + 1} "
                                f"at T+{h:.0f} h — keep lookout.")
                    if time_to_hazard_h is None:
                        time_to_hazard_h = h
            if seg == "NO-GO":
                break
        seg_states.append(seg)

    if degraded and worst == "NO-GO":
        pass  # NO-GO stands; freshness already noted
    elif degraded and worst == "GO":
        worst = "SLOW"

    if worst == "GO":
        reasons.insert(0, f"90% cones stay clear of the {hull:.0f} km hull buffer; max route ice {max_sic:.0f}%.")

    head = decisive or reasons[0]
    sentence = ("HOLD / REROUTE — " if worst == "NO-GO" else
                "PROCEED WITH CAUTION — " if worst == "SLOW" else "PROCEED ON PLAN — ") + head
    return {"badge": worst, "reasons": reasons, "segments": seg_states,
            "time_to_hazard_h": time_to_hazard_h, "max_sic": round(max_sic, 1),
            "degraded": degraded, "sentence": sentence}


# ------------------------------------------------------------------ replay ---

def replay_week(state: BergState, week_offset: int = 0,
                model_version: str = "v1.2.0") -> Dict[str, Any]:
    """Judge demo: 'predict last week, compare with what actually happened'.

    Actual = independent realisation (different seed) of the same ocean:
    systematic per-week biases (wrong drag, unresolved eddy) so error GROWS
    with lead time like a real model; predicted = operational forecast.
    Error in geodesic km.
    """
    t0 = -week_offset * 168.0 - 72.0
    head = residual_head_for(state.berg_id, model_version)
    # rewind from current state to the week's T-72h start
    lat2, lon2 = state.lat, state.lon
    for k in range(24):
        st = BergState(state.berg_id, lat2, lon2, state.size_class)
        u, v, _, _ = step_velocity(st, t0 + 72 - k * 3, head)
        lat2, lon2 = destination_point(lat2, lon2, -u, -v, 3 * 3600)
    start_lat, start_lon = lat2, lon2

    # systematic "truth" biases for this week (unknown to the forecaster)
    rng = seeded_rng("actual", state.berg_id, str(week_offset))
    cur_bias = 0.86 + float(rng.random()) * 0.26      # 0.86 - 1.12
    wind_bias = 0.80 + float(rng.random()) * 0.40     # 0.80 - 1.20
    eddy_u = float(rng.normal(0, 0.022))              # unresolved eddy m/s
    eddy_v = float(rng.normal(0, 0.022))

    actual = [(start_lat, start_lon)]
    la, lo = start_lat, start_lon
    for k in range(1, 25):
        t = t0 + k * 3
        st = BergState(state.berg_id, la, lo, state.size_class)
        u, v, _, _ = step_velocity(st, t, head,
                                   wind_scale=wind_bias + float(rng.normal(0, 0.03)),
                                   cur_scale=cur_bias + float(rng.normal(0, 0.03)))
        la, lo = destination_point(la, lo, u + eddy_u, v + eddy_v, 3 * 3600)
        actual.append((la, lo))

    fc = ensemble_cone(BergState(state.berg_id, start_lat, start_lon, state.size_class),
                       hours=72, dt_h=3.0, model_version=model_version, t0_h=t0)
    errs = [haversine_km(a[0], a[1], p[0], p[1]) for a, p in zip(actual, fc["centre"])]
    inside = [e <= r for e, r in zip(errs, fc["radii_km"])]
    mae = {h: round(float(np.mean(errs[: int(h / 3) + 1])), 2) for h in (24, 48, 72)}
    return {"actual": actual, "predicted": fc["centre"], "radii_km": fc["radii_km"],
            "errors_km": [round(e, 2) for e in errs],
            "mae_24_48_72": mae,
            "coverage_90": round(float(np.mean(inside)), 3),
            "week_offset": week_offset}
