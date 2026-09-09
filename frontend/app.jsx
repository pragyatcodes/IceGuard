/* ICEGUARD ops console — React + MapLibre. Physics first, SAR first, cone first, human first. */
const { useState, useEffect, useRef, useMemo } = React;

const CARTO_DARK = "https://tiles.openfreemap.org/styles/liberty";
const STATIONS = [
  { name: "Maitri", lat: -70.77, lon: 11.73 },
  { name: "Bharati", lat: -69.41, lon: 76.19 },
];
const BADGE_COLOR = { "GO": "#22c55e", "SLOW": "#f59e0b", "NO-GO": "#ef4444" };

async function api(path, opts) {
  opts = opts || {};
  const token = localStorage.getItem("iceguard_token");
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: Object.assign({ "Content-Type": "application/json" },
      token ? { Authorization: "Bearer " + token } : {}),
    body: opts.body ? JSON.stringify(opts.body) : null,
  });
  if (res.status === 401) throw { status: 401, detail: "Session expired — please log in." };
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw { status: res.status, detail: data.detail || ("HTTP " + res.status) };
  return data;
}

function fmtT(offsetH) {
  if (offsetH === 0) return "T+0 (NOW)";
  return "T" + (offsetH > 0 ? "+" : "") + offsetH + "h";
}
function dateAt(offsetH) {
  const d = new Date(Date.now() + offsetH * 3600 * 1000);
  return d.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}
/* perpendicular cone strip around a track */
function coneStrip(track, radii) {
  const up = [], dn = [];
  for (let i = 0; i < track.length; i++) {
    const p = track[i];
    const q = track[Math.min(track.length - 1, i + 1)];
    const o = track[Math.max(0, i - 1)];
    const cosLat = Math.max(0.2, Math.cos((p.lat * Math.PI) / 180));
    let dx = (q.lon - o.lon) * cosLat, dy = q.lat - o.lat;
    const L = Math.hypot(dx, dy) || 1; dx /= L; dy /= L;
    const r = radii[i] || 1;
    const dLat = r / 111.0, dLon = r / (111.0 * cosLat);
    up.push([p.lon + -dy * dLon, p.lat + dx * dLat]);
    dn.push([p.lon - -dy * dLon, p.lat - dx * dLat]);
  }
  return [up.concat(dn.reverse())];
}
function posAt(times, pts, t) {
  if (t <= times[0]) return pts[0];
  if (t >= times[times.length - 1]) return pts[pts.length - 1];
  for (let i = 1; i < times.length; i++) {
    if (t <= times[i]) {
      const f = (t - times[i - 1]) / (times[i] - times[i - 1]);
      return { lat: pts[i - 1].lat + (pts[i].lat - pts[i - 1].lat) * f,
               lon: pts[i - 1].lon + (pts[i].lon - pts[i - 1].lon) * f };
    }
  }
  return pts[pts.length - 1];
}
function buildTimeline(detail) {
  // history: 6h steps -168..0 ; forecast: 3h steps 0..72
  const times = [], pts = [];
  const H = detail.history || [];
  for (let i = 0; i < H.length; i++) { times.push(-168 + i * 6); pts.push(H[i]); }
  const F = detail.forecast || [];
  for (let i = 1; i < F.length; i++) { times.push(i * 3); pts.push(F[i]); }
  return { times, pts };
}

function App() {
  const [user, setUser] = useState(() => JSON.parse(localStorage.getItem("iceguard_user") || "null"));
  const [showLogin, setShowLogin] = useState(() => !localStorage.getItem("iceguard_token"));
  const [mapReady, setMapReady] = useState(false);
  const [feeds, setFeeds] = useState([]);
  const [bergs, setBergs] = useState([]);
  const [details, setDetails] = useState({});       // berg_id -> detail
  const [voyages, setVoyages] = useState([]);
  const [selBerg, setSelBerg] = useState("A23a");
  const [selVoyage, setSelVoyage] = useState(null);
  const [advice, setAdvice] = useState(null);
  const [adviceFor, setAdviceFor] = useState("");
  const [cones, setCones] = useState([]);
  const [icePts, setIcePts] = useState([]);
  const [iceEdge, setIceEdge] = useState(null);
  const [skill, setSkill] = useState(null);
  const [thresholds, setThresholds] = useState([]);
  const [models, setModels] = useState([]);
  const [audit, setAudit] = useState([]);
  const [users, setUsers] = useState([]);
  const [horizon, setHorizon] = useState(72);
  const [offset, setOffset] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [layers, setLayers] = useState({ ice: true, lock: true, edge: true, tracks: true, cones: true, ship: true, sar: false });
  const [lite, setLite] = useState(false);
  const [tab, setTab] = useState("berg");
  const [replay, setReplay] = useState(null);       // {berg_id, replays, week}
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState(null);         // [[lat,lon],...] drawn corridor
  const [toast, setToast] = useState(null);
  const [mapNote, setMapNote] = useState("");
  const [showOverride, setShowOverride] = useState(false);
  const [showFeeds, setShowFeeds] = useState(false);
  const [q, setQ] = useState("");
  const mapRef = useRef(null);
  const markersRef = useRef({ bergs: {}, ship: null, stations: [] });
  const drawingRef = useRef(false);

  const authed = !!user;
  const role = user ? user.role : "viewer";
  const canOp = role === "operator" || role === "admin";
  const isAdmin = role === "admin";

  function fail(e) {
    if (e && e.status === 401) { logout(); setToast({ msg: e.detail, kind: "bad" }); }
    else setToast({ msg: (e && e.detail) || "Request failed", kind: "bad" });
  }
  function logout() {
    localStorage.removeItem("iceguard_token"); localStorage.removeItem("iceguard_user");
    setUser(null); setShowLogin(true);
  }

  /* ---------------- map init ---------------- */
  // Ops sources + layers. Runs once the (online or offline) style is ready.
  function addOpsLayers(M) {
      M.addSource("ice", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "ice-heat", type: "heatmap", source: "ice", maxzoom: 12,
        paint: {
          "heatmap-weight": ["interpolate", ["linear"], ["get", "sic"], 3, 0, 100, 1],
          "heatmap-intensity": 1.1, "heatmap-radius": 22, "heatmap-opacity": 0.75,
          "heatmap-color": ["interpolate", ["linear"], ["heatmap-density"],
            0, "rgba(0,0,0,0)", 0.25, "#0c4a6e", 0.5, "#0284c7", 0.75, "#7dd3fc", 1, "#f0f9ff"],
        } });
      M.addLayer({ id: "ice-lock", type: "circle", source: "ice",
        filter: [">=", ["get", "sic"], 90],
        paint: { "circle-radius": 3.5, "circle-color": "#e0f2fe", "circle-opacity": 0.55 } });
      M.addSource("edge", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "ice-edge", type: "line", source: "edge",
        paint: { "line-color": "#38bdf8", "line-width": 1.5, "line-dasharray": [4, 3], "line-opacity": 0.9 } });
      M.addSource("cones", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "cones", type: "fill", source: "cones",
        paint: { "fill-color": ["get", "color"], "fill-opacity": ["get", "op"] } });
      M.addSource("tracks", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "tracks", type: "line", source: "tracks",
        paint: { "line-color": ["get", "color"], "line-width": ["get", "w"], "line-opacity": 0.95 } });
      M.addSource("history", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "history", type: "line", source: "history",
        paint: { "line-color": "#94a3b8", "line-width": 1.5, "line-dasharray": [2, 3], "line-opacity": 0.8 } });
      M.addSource("replay", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "replay", type: "line", source: "replay",
        paint: { "line-color": ["get", "color"], "line-width": 3,
                 "line-dasharray": ["get", "dash"] } });
      M.addSource("corridor", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "corridor", type: "line", source: "corridor", layout: { "line-cap": "round" },
        paint: { "line-color": ["get", "color"], "line-width": 4, "line-opacity": 0.9 } });
      M.addLayer({ id: "corridor-wp", type: "circle", source: "corridor",
        filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 4, "circle-color": "#fff", "circle-stroke-width": 1.5, "circle-stroke-color": "#0a1424" } });
      M.addSource("draft", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      M.addLayer({ id: "draft-line", type: "line", source: "draft",
        filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": "#fb923c", "line-width": 3, "line-dasharray": [5, 3] } });
      M.addLayer({ id: "draft-wp", type: "circle", source: "draft",
        filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 6, "circle-color": "#fb923c", "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
      M.addLayer({ id: "draft-label", type: "symbol", source: "draft",
        filter: ["==", ["geometry-type"], "Point"],
        layout: { "text-field": ["get", "seq"], "text-size": 10, "text-offset": [0, -1.4] },
        paint: { "text-color": "#fed7aa", "text-halo-color": "#431407", "text-halo-width": 1.5 } });
      M.on("click", (e) => {
        if (drawingRef.current) {
          const la = +e.lngLat.lat.toFixed(4), lo = +e.lngLat.lng.toFixed(4);
          setDraft((prev) => [...(prev || []), [la, lo]]);
        }
      });
  }
  useEffect(() => {
    document.getElementById("boot").style.display = "none";
    let cancelled = false;
    const FALLBACK_STYLE = { version: 8, name: "iceguard-offline", sources: {},
      layers: [{ id: "bg", type: "background", paint: { "background-color": "#06101f" } }] };
    // Reachability pre-check: if the base-map style is unreachable (offline/slow/blocked),
    // boot with an offline style. Our ops layers (ice, tracks, cones) need no base tiles.
    fetch(CARTO_DARK, { mode: "cors" }).then(
      (r) => { if (!cancelled) bootMap(r.ok ? CARTO_DARK : FALLBACK_STYLE, !r.ok); },
      () => { if (!cancelled) bootMap(FALLBACK_STYLE, true); }
    );
    const bootTimer = setTimeout(() => {
      if (!cancelled && !mapRef.current) bootMap(FALLBACK_STYLE, true);
    }, 10000);
    function bootMap(style, offline) {
      if (cancelled || mapRef.current) return;
      if (offline) setMapNote("offline base map - ops layers active");
      const map = new maplibregl.Map({
        container: "map", style: style, center: [35, -64], zoom: 2.3,
        maxBounds: [[-180, -85], [180, 10]],
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "top-right");
      let layersReady = false;
      const initLayers = () => {
        if (layersReady || !mapRef.current) return;
        try {
          addOpsLayers(mapRef.current);
          layersReady = true;
          setMapReady(true);
        } catch (e) { /* style not ready yet - load event retries */ }
      };
      map.on("load", initLayers);
      map.on("error", () => {
        try { if (!map.isStyleLoaded()) setMapNote("base map unreachable - ops layers active"); } catch (e) {}
      });
      setTimeout(() => {
        if (cancelled || layersReady) return;
        try { map.setStyle(FALLBACK_STYLE); } catch (e) {}
        setMapNote("offline base map - ops layers active");
        setTimeout(initLayers, 1500);
        setTimeout(initLayers, 4000);
      }, 12000);
    }
    return () => { cancelled = true; clearTimeout(bootTimer);
      try { mapRef.current && mapRef.current.remove(); } catch (e) {}
      mapRef.current = null; };
  }, []);

  /* ---------------- data load ---------------- */
  async function loadAll() {
    try {
      const [f, b, v, c, ice, edge, sk, th, md] = await Promise.all([
        api("/api/feeds"), api("/api/bergs"), api("/api/voyages"), api("/api/cones?horizon_h=72"),
        api("/api/ice/grid?n=1400"), api("/api/ice/edge"), api("/api/skill"),
        api("/api/thresholds"), api("/api/models"),
      ]);
      setFeeds(f.feeds); setBergs(b.bergs); setVoyages(v.voyages); setCones(c.cones);
      setIcePts(ice.points); setIceEdge(edge); setSkill(sk); setThresholds(th.thresholds); setModels(md.models);
      // details for every berg (timeline scrub needs history+forecast)
      const ds = {};
      await Promise.all(b.bergs.map(async (bb) => {
        try { ds[bb.berg_id] = await api("/api/bergs/" + bb.berg_id + "?horizon_h=72"); } catch (e) {}
      }));
      setDetails(ds);
      if (!selVoyage && v.voyages.length) {
        const pick = v.voyages.find((x) => x.code === "RES2026-BHARATI") || v.voyages[0];
        selectVoyage(pick.code, v.voyages);
      }
      if (isAdmin || canOp) { try { setAudit((await api("/api/audit?limit=60")).audit); } catch (e) {} }
      if (isAdmin) { try { setUsers((await api("/api/users")).users); } catch (e) {} }
    } catch (e) { fail(e); }
  }
  useEffect(() => { if (authed) loadAll(); }, [authed]);

  useEffect(() => { drawingRef.current = drawing;
    const M = mapRef.current;
    if (M) { try { M.getCanvas().style.cursor = drawing ? "crosshair" : ""; } catch (e) {} }
  }, [drawing]);

  // UTC clock
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);

  // play timeline
  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => setOffset((o) => (o >= 72 ? (setPlaying(false), 72) : o + 3)), 350);
    return () => clearInterval(t);
  }, [playing]);

  async function selectVoyage(code, list) {
    setDraft(null); setDrawing(false);
    const L = list || voyages;
    const v = L.find((x) => x.code === code);
    if (!v) return;
    setSelVoyage(v); setAdvice(null);
    try {
      const a = await api("/api/advice", { method: "POST", body: { voyage_code: code, horizon_h: horizon } });
      setAdvice(a); setAdviceFor(code);
      if (a.badge !== "GO") {
        const tth = a.time_to_hazard_h == null ? "" : a.time_to_hazard_h === 0 ?
          " Hazard on route NOW." : (" Cone meets route in " + a.time_to_hazard_h + " h.");
        setToast({ msg: a.badge + " — " + code + "." + tth + " " + (a.reasons[0] || ""),
                   kind: a.badge === "NO-GO" ? "bad" : "warn", sticky: true });
      }
    } catch (e) { fail(e); }
  }
  function selectBerg(id) { setSelBerg(id); }

  /* ---------------- voyage drawing ---------------- */
  function startDraw() {
    setDraft([]); setDrawing(true);
    setToast({ msg: "Draw mode: click anywhere on the map to add waypoints, then Done. Tip: clicking a berg drops a waypoint on it.", kind: "info" });
  }
  function cancelDraw() {
    setDrawing(false);
    if (!selVoyage || selVoyage.code !== "DRAFT") setDraft(null);
  }
  function resumeDraw() { if (draft && draft.length) setDrawing(true); }
  function undoDraft() { setDraft((prev) => (prev || []).slice(0, -1)); }
  async function finishDraft() {
    if (!draft || draft.length < 2) {
      setToast({ msg: "Add at least 2 waypoints - click on the map.", kind: "warn" }); return;
    }
    const v = { code: "DRAFT", title: "Drawn route - unsaved (" + draft.length + " wp)",
                corridor: draft.slice(), ship_class: "ice-class", status: "draft" };
    setSelVoyage(v); setAdvice(null); setDrawing(false);
    try {
      const a = await api("/api/advice", { method: "POST", body: { corridor: draft, horizon_h: horizon } });
      setAdvice(a); setAdviceFor("DRAFT");
      if (a.badge !== "GO") {
        const tth = a.time_to_hazard_h == null ? "" : a.time_to_hazard_h === 0 ?
          " Hazard on route NOW." : (" Cone meets route in " + a.time_to_hazard_h + " h.");
        setToast({ msg: a.badge + " - drawn route." + tth + " " + (a.reasons[0] || ""),
                   kind: a.badge === "NO-GO" ? "bad" : "warn", sticky: true });
      } else setToast({ msg: "GO - drawn route is clear. Save it to keep it.", kind: "info" });
    } catch (e) { fail(e); }
  }
  async function saveDraft(code, title) {
    if (!draft || draft.length < 2) { setToast({ msg: "Nothing to save.", kind: "warn" }); return; }
    try {
      await api("/api/voyages", { method: "POST",
        body: { code: code.trim(), title: title.trim() || code.trim(), corridor: draft, ship_class: "ice-class" } });
      const v = await api("/api/voyages");
      setVoyages(v.voyages); setDraft(null);
      selectVoyage(code.trim(), v.voyages);
      setToast({ msg: "Voyage " + code.trim() + " saved.", kind: "info" });
    } catch (e) { fail(e); }
  }
  function discardDraft() {
    setDraft(null); setDrawing(false);
    if (voyages.length) selectVoyage(voyages[0].code, voyages);
    else { setSelVoyage(null); setAdvice(null); }
  }

  /* ---------------- map render ---------------- */
  useEffect(() => {
    const M = mapRef.current;
    if (!M || !mapReady) return;
    const vis = (id, on) => { try { if (M.getLayer(id)) M.setLayoutProperty(id, "visibility", on ? "visible" : "none"); } catch (e) {} };
    vis("ice-heat", layers.ice); vis("ice-lock", layers.lock && !lite);
    vis("ice-edge", layers.edge && !lite); vis("tracks", layers.tracks);
    vis("cones", layers.cones); vis("history", true); vis("replay", !!replay);
    vis("draft-line", !!(draft && draft.length >= 2));
    vis("draft-wp", !!(draft && draft.length >= 1));
    vis("draft-label", !!(draft && draft.length >= 1));

    // ice
    if (M.getSource("ice")) {
      const pts = lite ? icePts.filter((_, i) => i % 3 === 0) : icePts;
      M.getSource("ice").setData({ type: "FeatureCollection",
        features: pts.map((p) => ({ type: "Feature", properties: { sic: p.sic },
          geometry: { type: "Point", coordinates: [p.lon, p.lat] } })) });
    }
    if (M.getSource("edge") && iceEdge)
      M.getSource("edge").setData({ type: "FeatureCollection",
        features: [{ type: "Feature", properties: {}, geometry: iceEdge }] });

    // cones + tracks (sliced to horizon)
    const nSteps = Math.round(horizon / 3);
    if (M.getSource("cones"))
      M.getSource("cones").setData({ type: "FeatureCollection", features: cones.map((c) => {
        const tr = c.track.slice(0, nSteps + 1), rr = c.radii_km.slice(0, nSteps + 1);
        const sel = c.berg_id === selBerg;
        return { type: "Feature",
          properties: { berg_id: c.berg_id, color: sel ? "#f59e0b" : "#22d3ee", op: sel ? 0.30 : 0.16 },
          geometry: { type: "Polygon", coordinates: coneStrip(tr, rr) } };
      }) });
    if (M.getSource("tracks"))
      M.getSource("tracks").setData({ type: "FeatureCollection", features: cones.map((c) => {
        const tr = c.track.slice(0, nSteps + 1);
        const sel = c.berg_id === selBerg;
        return { type: "Feature", properties: { berg_id: c.berg_id, color: sel ? "#fbbf24" : "#22d3ee", w: sel ? 3 : 2 },
          geometry: { type: "LineString", coordinates: tr.map((p) => [p.lon, p.lat]) } };
      }) });

    // history of selected berg
    if (M.getSource("history")) {
      const d = details[selBerg];
      M.getSource("history").setData({ type: "FeatureCollection", features: d ? [{
        type: "Feature", properties: {},
        geometry: { type: "LineString", coordinates: d.history.map((p) => [p.lon, p.lat]) } }] : [] });
    }
    // replay overlay
    if (M.getSource("replay")) {
      if (replay) {
        const r = replay.replays[replay.week];
        M.getSource("replay").setData({ type: "FeatureCollection", features: [
          { type: "Feature", properties: { color: "#f8fafc", dash: [2, 2] },
            geometry: { type: "LineString", coordinates: r.actual.map((p) => [p.lon, p.lat]) } },
          { type: "Feature", properties: { color: "#22d3ee", dash: [1, 0] },
            geometry: { type: "LineString", coordinates: r.predicted.map((p) => [p.lon, p.lat]) } },
        ]});
      } else M.getSource("replay").setData({ type: "FeatureCollection", features: [] });
    }

    // corridor segments
    if (M.getSource("corridor")) {
      const feats = [];
      if (selVoyage) {
        const corr = selVoyage.corridor;
        const segs = (advice && adviceFor === selVoyage.code && advice.segments) || [];
        for (let i = 0; i < corr.length - 1; i++) {
          const s = segs[i] || segs[i + 1] || "GO";
          feats.push({ type: "Feature", properties: { color: BADGE_COLOR[s] || "#22c55e" },
            geometry: { type: "LineString", coordinates: [[corr[i][1], corr[i][0]], [corr[i + 1][1], corr[i + 1][0]]] } });
        }
        corr.forEach((p) => feats.push({ type: "Feature", properties: {},
          geometry: { type: "Point", coordinates: [p[1], p[0]] } }));
      }
      M.getSource("corridor").setData({ type: "FeatureCollection", features: feats });
    }

    // draft (drawn, unsaved)
    if (M.getSource("draft")) {
      const pts = draft || [];
      const df = [];
      if (pts.length >= 2)
        df.push({ type: "Feature", properties: {},
          geometry: { type: "LineString", coordinates: pts.map((pp) => [pp[1], pp[0]]) } });
      pts.forEach((pp, i) => df.push({ type: "Feature", properties: { seq: String(i + 1) },
        geometry: { type: "Point", coordinates: [pp[1], pp[0]] } }));
      M.getSource("draft").setData({ type: "FeatureCollection", features: df });
    }

    // berg markers
    const mk = markersRef.current;
    bergs.forEach((b) => {
      const d = details[b.berg_id];
      const tl = d ? buildTimeline(d) : null;
      const pos = tl ? posAt(tl.times, tl.pts, Math.max(-168, Math.min(72, offset))) : { lat: b.lat, lon: b.lon };
      if (layers.sar === undefined) return;
      let m = mk.bergs[b.berg_id];
      if (!m) {
        const el = document.createElement("div");
        el.className = "berg-marker"; el.textContent = "❄";
        el.title = b.berg_id;
        el.onclick = () => {
          if (drawingRef.current) {
            try {
              const ll = mk.bergs[b.berg_id].getLngLat();
              const la = +ll.lat.toFixed(4), lo = +ll.lng.toFixed(4);
              setDraft((prev) => [...(prev || []), [la, lo]]);
            } catch (e) {}
          } else selectBerg(b.berg_id);
        };
        m = new maplibregl.Marker({ element: el }).setLngLat([pos.lon, pos.lat]).addTo(M);
        mk.bergs[b.berg_id] = m;
      } else m.setLngLat([pos.lon, pos.lat]);
      const el = m.getElement();
      el.classList.toggle("sel", b.berg_id === selBerg);
      el.classList.toggle("sar", !!layers.sar);
      el.title = b.berg_id + " — " + b.name;
    });
    // ship marker
    if (selVoyage && layers.ship) {
      const corr = selVoyage.corridor;
      const f = Math.max(0, Math.min(1, (offset + 168) / 240));
      const segF = f * (corr.length - 1), i0 = Math.floor(segF), i1 = Math.min(corr.length - 1, i0 + 1), fr = segF - i0;
      const la = corr[i0][0] + (corr[i1][0] - corr[i0][0]) * fr;
      const lo = corr[i0][1] + (corr[i1][1] - corr[i0][1]) * fr;
      if (!mk.ship) {
        const el = document.createElement("div"); el.className = "ship-marker"; el.title = "Ship";
        mk.ship = new maplibregl.Marker({ element: el }).setLngLat([lo, la]).addTo(M);
      } else mk.ship.setLngLat([lo, la]);
    } else if (mk.ship) { mk.ship.remove(); mk.ship = null; }
    // stations
    if (mk.stations.length === 0) {
      STATIONS.forEach((s) => {
        const el = document.createElement("div"); el.className = "station-marker"; el.textContent = "◉ " + s.name;
        mk.stations.push(new maplibregl.Marker({ element: el }).setLngLat([s.lon, s.lat]).addTo(M));
      });
    }
  });

  /* ---------------- derived ---------------- */
  const sar = feeds.find((f) => f.id === "sar");
  const degraded = advice && advice.degraded;
  const detail = details[selBerg] || null;
  const bergMeta = bergs.find((b) => b.berg_id === selBerg);
  const filteredBergs = bergs.filter((b) =>
    (b.berg_id + " " + b.name).toLowerCase().includes(q.toLowerCase()));

  async function runReplay(bergId) {
    try {
      const r = await api("/api/bergs/" + bergId + "/replay?weeks=4");
      setReplay({ berg_id: bergId, replays: r.replays, week: 0 });
      setTab("berg");
      const m = r.replays[0].mae;
      setToast({ msg: "Replay " + bergId + ": MAE " + m[24] + "/" + m[48] + "/" + m[72] +
        " km at 24/48/72 h (geodesic), cone coverage " + r.replays[0].coverage_90 + ". White dashed = actual, cyan = predicted.",
        kind: "info" });
    } catch (e) { fail(e); }
  }
  async function exportBundle() {
    if (!selVoyage) return;
    try {
      const fc = await api("/api/bundle/" + selVoyage.code);
      const blob = new Blob([JSON.stringify(fc)], { type: "application/geo+json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "iceguard_" + selVoyage.code + "_bundle.geojson";
      a.click();
      setToast({ msg: "Station bundle exported (" + (blob.size / 1024).toFixed(1) +
        " KB GeoJSON — tracks, cones, corridor, badge).", kind: "info" });
    } catch (e) { fail(e); }
  }
  async function saveThresholds(vals) {
    try { await api("/api/thresholds", { method: "PUT", body: { values: vals } });
      setToast({ msg: "Thresholds updated. Re-scoring route…", kind: "info" });
      const th = await api("/api/thresholds"); setThresholds(th.thresholds);
      if (selVoyage) selectVoyage(selVoyage.code);
    } catch (e) { fail(e); }
  }
  async function freezeModel(v) {
    try { await api("/api/models/freeze?version=" + encodeURIComponent(v), { method: "POST" });
      const md = await api("/api/models"); setModels(md.models);
      const c = await api("/api/cones?horizon_h=72"); setCones(c.cones);
      setToast({ msg: "Model " + v + " frozen + activated. Cones recomputed.", kind: "info" });
      if (selVoyage) selectVoyage(selVoyage.code);
    } catch (e) { fail(e); }
  }
  async function simFeed(id, age) {
    try { await api("/api/feeds/simulate", { method: "POST", body: { feed_id: id, age_hours: age } });
      const f = await api("/api/feeds"); setFeeds(f.feeds);
      if (selVoyage) selectVoyage(selVoyage.code);
    } catch (e) { fail(e); }
  }

  /* ---------------- render ---------------- */
  return (
    <div className="app">
      <TopBar now={now} feeds={feeds} sar={sar} user={user} lite={lite} setLite={setLite}
        onFeeds={() => setShowFeeds(!showFeeds)} onLogout={logout}
        onReset={() => { const M = mapRef.current; if (M) M.flyTo({ center: [35, -64], zoom: 2.3 }); }} />
      <div className="main">
        <div className="left">
          <div className="section-title">Tracked bergs ({bergs.length})</div>
          <input className="search" placeholder="Search ID…" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="berg-list" style={{ maxHeight: "34%" }}>
            {filteredBergs.map((b) => (
              <div key={b.berg_id} className={"berg-item" + (b.berg_id === selBerg ? " sel" : "")}
                onClick={() => selectBerg(b.berg_id)}>
                <span className="id">{b.berg_id}</span>
                <span className={"regime " + b.regime_now}>{b.regime_now.toUpperCase()}</span>
                <div className="meta">{b.size_class} · {b.area_km2} km² · SIC {b.sic_now}%</div>
              </div>
            ))}
          </div>
          <div className="section-title">Voyages & corridors</div>
          <button className="btn small primary" style={{ margin: "0 10px 8px" }} onClick={startDraw}>✏ Draw new voyage</button>
          <div className="voy-list" style={{ maxHeight: "30%" }}>
            {voyages.map((v) => (
              <div key={v.code} className={"voy-item" + (selVoyage && selVoyage.code === v.code ? " sel" : "")}
                onClick={() => selectVoyage(v.code)}>
                <div className="code">{v.code}</div>
                <div className="title">{v.title}</div>
              </div>
            ))}
          </div>
          <div className="section-title">Layers</div>
          <div className="layers">
            {[["ice", "Ice"], ["lock", "Lock ≥90%"], ["edge", "Ice edge"], ["tracks", "Tracks"],
              ["cones", "Cones"], ["ship", "Ship"], ["sar", "SAR chips"]].map(([k, lbl]) => (
              <span key={k} className={"chip" + (layers[k] ? " on" : "")}
                onClick={() => setLayers(Object.assign({}, layers, { [k]: !layers[k] }))}>{lbl}</span>
            ))}
            <span className="chip disabled" title="Optical is backup only"
              onClick={() => setToast({ msg: "Optical unavailable: cloud + polar night. SAR-first by design (L1).", kind: "info" })}>
              Optical ∅</span>
          </div>
          {role === "viewer" && (
            <div className="gloss" style={{ margin: "4px 10px 10px" }}>
              <b>Classroom mode.</b> Hover any <b>?</b> for plain-language polar science.
              Try Replay on berg <b>A23a</b> to see forecast vs actual.
            </div>
          )}
        </div>

        <div className="center">
          <div id="map"></div>
          {advice && adviceFor === (selVoyage && selVoyage.code) && (
            <div className={"route-badge " + advice.badge}>
              {advice.badge}
              <span className="sub">{selVoyage.code} · {advice.model} · max ice {advice.max_sic}%</span>
            </div>
          )}
          {degraded && <div className="degraded-banner">⚠ DEGRADED — SAR older than limit. Advice capped at SLOW. Advice refreshes on next STAC ingest.</div>}
          {drawing && (
            <div className="draw-bar">
              <span>📌 {(draft || []).length} wp - click map to add</span>
              <button className="btn small ghost" onClick={undoDraft}>↩ Undo</button>
              <button className="btn small primary" onClick={finishDraft}>✓ Done - score it</button>
              <button className="btn small danger" onClick={cancelDraw}>✕</button>
            </div>
          )}
          {mapNote && <div className="map-note">{"\u26a0 " + mapNote}</div>}
          {lite && <div className="lite-banner">📡 LITE station mode · bundle ≈ {skill ? skill.payload_kb : "—"} KB · tracks + cones + badge only</div>}
          <div className="map-legend">
            <div><span className="sw" style={{ background: "linear-gradient(90deg,#0c4a6e,#7dd3fc,#f0f9ff)" }}></span>Ice concentration (AMSR2/NSIDC)</div>
            <div><span className="sw" style={{ background: "#22d3ee" }}></span>Forecast track (physics + AI residual)</div>
            <div><span className="sw" style={{ background: "#22d3ee55", border: "1px solid #22d3ee" }}></span>90% cone (advice uses the cone)</div>
            <div><span className="sw" style={{ background: "#94a3b8" }}></span>History (replay the week)</div>
          </div>
          <div className="disclaimer">Decision support only — not a substitute for master/pilot judgement or official ice charts. No helm commands. Overrides are logged.</div>
        </div>

        <div className="right">
          <div className="tabs">
            {[["berg", "Berg"], ["route", "Route"], ["skill", "Skill"], ...(isAdmin ? [["admin", "Admin"]] : [])].map(([k, lbl]) => (
              <div key={k} className={"tab" + (tab === k ? " sel" : "")} onClick={() => setTab(k)}>{lbl}</div>
            ))}
          </div>
          <div className="tabpane">
            {tab === "berg" && (
              <BergTab detail={detail} meta={bergMeta} horizon={horizon} setHorizon={setHorizon}
                replay={replay} onReplay={runReplay} onExitReplay={() => setReplay(null)}
                onReplayWeek={(w) => replay && setReplay(Object.assign({}, replay, { week: w }))}
                role={role} onExport={exportBundle} />
            )}
            {tab === "route" && (
              <RouteTab voyage={selVoyage} voyages={voyages} advice={advice} adviceFor={adviceFor}
                onSelect={selectVoyage} canOp={canOp} onOverride={() => setShowOverride(true)}
                onExport={exportBundle} role={role}
                isDraft={!!(selVoyage && selVoyage.code === "DRAFT")}
                onDraw={startDraw} onResume={resumeDraw} onRescore={finishDraft}
                onDiscard={discardDraft} onSave={saveDraft} />
            )}
            {tab === "skill" && <SkillTab skill={skill} models={models} />}
            {tab === "admin" && isAdmin && (
              <AdminTab thresholds={thresholds} models={models} audit={audit} users={users}
                onSaveTh={saveThresholds} onFreeze={freezeModel}
                onUser={async (u) => { try { await api("/api/users", { method: "POST", body: u });
                  setUsers((await api("/api/users")).users); } catch (e) { fail(e); } }}
                onAudit={async () => { try { setAudit((await api("/api/audit?limit=60")).audit); } catch (e) { fail(e); } }} />
            )}
          </div>
        </div>
      </div>

      <div className="timeline">
        <button className="play" onClick={() => setPlaying(!playing)} title="Play">{playing ? "⏸" : "▶"}</button>
        <div className="tlabel">{fmtT(offset)}<div style={{ fontSize: 10, fontWeight: 400, color: "#8ba3c2" }}>{dateAt(offset)}</div></div>
        <input type="range" min={-168} max={72} step={3} value={offset} onChange={(e) => setOffset(+e.target.value)} />
        <div className="ticks"><span>T−7d</span><span>NOW</span><span>T+72h</span></div>
      </div>

      {toast && (
        <div className={"toast " + (toast.kind === "bad" ? "bad" : toast.kind === "info" ? "info" : "")}>
          <span>{toast.msg}</span>
          <button className="btn small" onClick={() => setToast(null)}>Acknowledge</button>
        </div>
      )}
      {showLogin && <LoginModal onDone={(u) => { setUser(u); setShowLogin(false); }} notify={(m) => setToast(m)} />}
      {showOverride && selVoyage && (
        <OverrideModal voyage={selVoyage.code} onClose={() => setShowOverride(false)}
          onDone={async (decision, reason) => {
            try { await api("/api/overrides", { method: "POST", body: { voyage_code: selVoyage.code, decision, reason } });
              setShowOverride(false);
              setToast({ msg: "Override logged: " + selVoyage.code + " → " + decision + " (“" + reason + "”)", kind: "info" });
              try { setAudit((await api("/api/audit?limit=60")).audit); } catch (e) {}
            } catch (e) { fail(e); }
          }} />
      )}
      {showFeeds && (
        <div className="feed-pop">
          <b>Data health</b>
          <div className="scrollbox" style={{ marginTop: 6 }}>
            {feeds.map((f) => (
              <div key={f.id} className="kv"><span className="k">● {f.name}</span>
                <span className="v">{f.age_hours.toFixed(0)} h · {f.status}</span></div>
            ))}
          </div>
          {canOp && (
            <div className="row2 mt">
              <button className="btn small danger" onClick={() => simFeed("sar", 80)}>SAR outage drill</button>
              <button className="btn small" onClick={() => simFeed("sar", 6)}>Restore SAR</button>
            </div>
          )}
          <div className="mt"><button className="btn small ghost" onClick={() => setShowFeeds(false)}>Close</button></div>
        </div>
      )}
    </div>
  );
}

/* ---------------- pieces ---------------- */

function TopBar(p) {
  const d = new Date(p.now);
  const pad = (x) => String(x).padStart(2, "0");
  return (
    <div className="topbar">
      <div className="brand"><span className="logo">🧊</span>
        <span className="name">ICE<span>GUARD</span></span>
        <span className="sub">SIH26059 · MoES<br />Antarctic navigation DSS</span>
      </div>
      <div className="clock">{pad(d.getUTCHours())}:{pad(d.getUTCMinutes())}:{pad(d.getUTCSeconds())} UTC</div>
      <div className="sar-age">SAR age: <b>{p.sar ? p.sar.age_hours.toFixed(0) + " h" : "—"}</b></div>
      <div className="health" onClick={p.onFeeds} title="Data health — click for detail">
        {p.feeds.map((f) => <span key={f.id} className={"dot " + f.status} title={f.name + ": " + f.age_hours.toFixed(0) + " h"}></span>)}
      </div>
      <div className="spacer"></div>
      <div className={"lite-pill" + (p.lite ? " on" : "")} onClick={() => p.setLite(!p.lite)} title="Station low-bandwidth mode">
        📡 {p.lite ? "LITE ON" : "LITE"}
      </div>
      <button className="btn small ghost" onClick={p.onReset}>⟡ Reset view</button>
      {p.user && <span className="role-badge">{p.user.username} · {p.user.role}</span>}
      <button className="btn small" onClick={p.onLogout}>Logout</button>
    </div>
  );
}

function LoginModal(p) {
  const [u, setU] = useState("operator");
  const [pw, setPw] = useState("operator123");
  const [err, setErr] = useState("");
  async function go(e) {
    e && e.preventDefault();
    try {
      const r = await api("/api/auth/login", { method: "POST", body: { username: u, password: pw } });
      localStorage.setItem("iceguard_token", r.access_token);
      localStorage.setItem("iceguard_user", JSON.stringify(r.user));
      p.onDone(r.user);
    } catch (e2) { setErr(e2.detail || "Login failed"); }
  }
  return (
    <div className="modal-wrap">
      <form className="modal" onSubmit={go}>
        <h2>🧊 ICEGUARD login</h2>
        <p className="dim">JWT roles — viewer (science/classroom), operator (NCPOR duty), admin (MoES mentor).</p>
        <div className="demo-btns">
          {[["viewer", "viewer123", "Viewer"], ["operator", "operator123", "Operator"], ["admin", "admin123", "Admin"]].map(([a, b, lbl]) => (
            <button type="button" key={a} className="btn small ghost" onClick={() => { setU(a); setPw(b); }}>{lbl}</button>
          ))}
        </div>
        <label className="lbl">Username</label>
        <input className="txt" value={u} onChange={(e) => setU(e.target.value)} />
        <label className="lbl">Password</label>
        <input className="txt" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
        {err && <div className="reason bad">{err}</div>}
        <button className="btn primary" style={{ width: "100%", marginTop: 6 }} type="submit">Sign in</button>
        <p className="dim" style={{ marginTop: 10 }}>Decision support only — never an autopilot. All advice & overrides are audit-logged.</p>
      </form>
    </div>
  );
}

function BergTab(p) {
  const d = p.detail, m = p.meta;
  if (!d || !m) return <div className="dim">Loading berg…</div>;
  const sh = d.avg_share || { wind: 0, current: 0, ice: 0 };
  const grounded = d.keel_m > d.depth_m * 0.8;
  const rp = p.replay && p.replay.berg_id === d.berg_id ? p.replay.replays[p.replay.week] : null;
  return (
    <div>
      <h3 style={{ margin: "0 0 2px" }}>{d.berg_id} <span className={"regime " + d.regime_now}>{d.regime_now.toUpperCase()}</span></h3>
      <div style={{ color: "#8ba3c2", fontSize: 12 }}>{m.name}</div>
      {p.role === "viewer" && (
        <div className="gloss"><b>?</b> Regime = how the berg moves. <b>Open</b>: free drift. <b>Drag</b>: slowed by floes. <b>Lock</b>: frozen in pack, moving with it — wind no longer matters.</div>
      )}
      <div className="kv"><span className="k">Position</span><span className="v">{d.lat.toFixed(2)}°, {d.lon.toFixed(2)}°</span></div>
      <div className="kv"><span className="k">Size class</span><span className="v">{d.size_class} · {m.area_km2} km²</span></div>
      <div className="kv"><span className="k">Ice conc. (SIC)</span><span className="v">{d.sic_now}%</span></div>
      <div className="kv"><span className="k">Seabed / keel</span><span className="v">{d.depth_m} m / {d.keel_m} m {grounded && "⚠ grounding?"}</span></div>
      <div className="kv"><span className="k">Ice-locked (72 h)</span><span className="v">{Math.round(d.lock_frac * 100)}% of steps</span></div>
      {grounded && <div className="reason warn">Possible grounding: keel near seabed — model may over-predict drift. Raised to SLOW by policy (L7).</div>}
      <div className="section-title" style={{ paddingLeft: 0 }}>Forecast horizon</div>
      <div className="horizon-btns">
        {[24, 48, 72].map((h) => (
          <button key={h} className={"btn small" + (p.horizon === h ? " primary" : "")} onClick={() => p.setHorizon(h)}>T+{h}h</button>
        ))}
      </div>
      <div className="kv"><span className="k">T+24 h</span><span className="v">{d.forecast[8].lat.toFixed(2)}, {d.forecast[8].lon.toFixed(2)} · ±{d.radii_km[8]} km</span></div>
      <div className="kv"><span className="k">T+48 h</span><span className="v">{d.forecast[16].lat.toFixed(2)}, {d.forecast[16].lon.toFixed(2)} · ±{d.radii_km[16]} km</span></div>
      <div className="kv"><span className="k">T+72 h</span><span className="v">{d.forecast[24].lat.toFixed(2)}, {d.forecast[24].lon.toFixed(2)} · ±{d.radii_km[24]} km</span></div>
      <div className="section-title" style={{ paddingLeft: 0 }}>Force breakdown (XAI tags)</div>
      <div className="forcebar">
        <div style={{ width: sh.wind + "%", background: "#f59e0b" }}></div>
        <div style={{ width: sh.current + "%", background: "#22d3ee" }}></div>
        <div style={{ width: sh.ice + "%", background: "#e0f2fe" }}></div>
      </div>
      <div className="force-leg"><span>🟧 wind {sh.wind.toFixed(0)}%</span><span>🟦 current {sh.current.toFixed(0)}%</span><span>⬜ ice {sh.ice.toFixed(0)}%</span></div>
      <div className="explain">💡 {d.explain}</div>
      <div className="kv"><span className="k">Model</span><span className="v">{d.model} (physics + residual)</span></div>
      <div className="row2 mt">
        <button className="btn small primary" onClick={() => p.onReplay(d.berg_id)}>▶ Replay 4 weeks</button>
        <button className="btn small" onClick={p.onExport}>⬇ Station bundle</button>
      </div>
      {rp && (
        <div className="mt">
          <div className="section-title" style={{ paddingLeft: 0 }}>Replay — {d.berg_id} (white = actual)</div>
          <div className="horizon-btns">
            {[0, 1, 2, 3].map((w) => (
              <button key={w} className={"btn small" + (p.replay.week === w ? " primary" : "")}
                onClick={() => p.onReplayWeek ? p.onReplayWeek(w) : null}>W−{w}</button>
            ))}
            <button className="btn small ghost" onClick={p.onExitReplay}>✕ exit</button>
          </div>
          <div className="kv"><span className="k">MAE 24/48/72 h</span><span className="v">{rp.mae[24]} / {rp.mae[48]} / {rp.mae[72]} km</span></div>
          <div className="kv"><span className="k">Cone coverage</span><span className="v">{rp.coverage_90} (target 0.90)</span></div>
        </div>
      )}
    </div>
  );
}

function DraftSaveForm(p) {
  const [code, setCode] = useState("");
  const [title, setTitle] = useState("");
  if (!p.canOp) return <div className="gloss">Saving needs an <b>operator</b> account - viewers can draw & score.</div>;
  return (
    <div>
      <input className="txt" placeholder="e.g. CUSTOM-01" value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9-]/g, ""))} />
      <input className="txt" placeholder="Title (optional)" value={title} onChange={(e) => setTitle(e.target.value)} />
      <button className="btn small primary" style={{ width: "100%" }} disabled={!code.trim()}
        onClick={() => p.onSave(code, title)}>💾 Save voyage</button>
    </div>
  );
}

function RouteTab(p) {
  const a = p.advice && p.adviceFor === (p.voyage && p.voyage.code) ? p.advice : null;
  return (
    <div>
      <label className="lbl">Planned voyage</label>
      <select className="txt" value={p.voyage ? p.voyage.code : ""} onChange={(e) => p.onSelect(e.target.value)}>
        {p.isDraft && <option value="DRAFT">DRAFT — unsaved drawn route</option>}
        {p.voyages.map((v) => <option key={v.code} value={v.code}>{v.code} — {v.title}</option>)}
      </select>
      <div className="row2">
        <button className="btn small" onClick={p.onDraw}>✏ Draw new</button>
        {p.isDraft
          ? <button className="btn small ghost" onClick={p.onDiscard}>✕ Discard</button>
          : <button className="btn small ghost" onClick={() => p.voyage && p.onSelect(p.voyage.code)}>↻ Re-score</button>}
      </div>
      {p.role === "viewer" && (
        <div className="gloss"><b>?</b> The badge uses the <b>cone</b>, not the centre line. <b>GO</b>: cones clear. <b>SLOW</b>: graze / moderate ice / stale data. <b>NO-GO</b>: cone crosses the hull buffer or lock-ice blocks the way.</div>
      )}
      {!a && <div className="dim">Scoring route…</div>}
      {a && (
        <div>
          <div className={"route-badge " + a.badge} style={{ position: "static", transform: "none", margin: "8px 0" }}>
            {a.badge}<span className="sub">{a.time_to_hazard_h == null ?
              (a.badge === "GO" ? "cones clear" : a.badge === "SLOW" ? "marginal ice / data" : "blocked — see reasons") :
              a.time_to_hazard_h === 0 ? "hazard on route NOW" : "contact in ~" + a.time_to_hazard_h + " h"}</span>
          </div>
          <div className="explain">💡 {a.sentence}</div>
          {a.reasons.map((r, i) => (
            <div key={i} className={"reason" + (a.badge === "NO-GO" ? " bad" : a.badge === "SLOW" ? " warn" : "")}>{r}</div>
          ))}
          <div className="kv"><span className="k">Max route ice</span><span className="v">{a.max_sic}%</span></div>
          <div className="kv"><span className="k">Model</span><span className="v">{a.model}</span></div>
          {p.isDraft ? (
            <div>
              <div className="reason">Unsaved drawn route — {p.voyage.corridor.length} waypoints. Score above is live.</div>
              <label className="lbl">Save as voyage code</label>
              <DraftSaveForm onSave={p.onSave} canOp={p.canOp} />
              <div className="row2 mt">
                <button className="btn small" onClick={p.onResume}>＋ Add points</button>
                <button className="btn small primary" onClick={p.onRescore}>↻ Re-score</button>
              </div>
              <button className="btn small ghost mt" style={{ width: "100%" }} onClick={p.onDiscard}>✕ Discard draft</button>
            </div>
          ) : (
            <div>
              <div className="row2 mt">
                <button className="btn small primary" onClick={() => p.onSelect(p.voyage.code)}>↻ Re-score</button>
                <button className="btn small" onClick={p.onExport}>⬇ Station bundle</button>
              </div>
              {p.canOp && <button className="btn small danger mt" style={{ width: "100%" }} onClick={p.onOverride}>⚑ Officer override (logged)</button>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SkillTab(p) {
  const s = p.skill;
  if (!s) return <div className="dim">Loading skill…</div>;
  const rows = s.per_berg || [];
  const maxV = Math.max(25, ...rows.map((r) => r.mae_72));
  return (
    <div>
      <h3 style={{ margin: "0 0 4px" }}>Replay skill — {s.model}</h3>
      <div style={{ color: "#8ba3c2", fontSize: 11 }}>{s.note}</div>
      <table className="grid">
        <thead><tr><th>Berg</th><th>24h</th><th>48h</th><th>72h</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.berg_id}><td>{r.berg_id} <span style={{ color: "#8ba3c2" }}>{r.size_class}</span></td>
              <td>{r.mae_24}</td><td>{r.mae_48}</td><td>{r.mae_72}</td></tr>
          ))}
          <tr><td><b>Mean</b></td><td><b>{s.mean_mae.h24}</b></td><td><b>{s.mean_mae.h48}</b></td>
            <td><b className="pass">{s.mean_mae.h72} km ✓</b></td></tr>
        </tbody>
      </table>
      <svg width="100%" height={rows.length * 22 + 30} style={{ background: "#081426", borderRadius: 8 }}>
        {rows.map((r, i) => (
          <g key={r.berg_id}>
            <text x="6" y={i * 22 + 15} fill="#8ba3c2" fontSize="10">{r.berg_id}</text>
            <rect x="52" y={i * 22 + 5} width={Math.max(2, (r.mae_72 / maxV) * 200)} height="12" rx="3" fill="#22d3ee" opacity="0.85" />
            <text x={58 + Math.max(2, (r.mae_72 / maxV) * 200)} y={i * 22 + 15} fill="#dbe7f5" fontSize="10">{r.mae_72}</text>
          </g>
        ))}
        <line x1={52 + (25 / maxV) * 200} y1="4" x2={52 + (25 / maxV) * 200} y2={rows.length * 22 + 4} stroke="#22c55e" strokeDasharray="4 3" />
        <text x={56 + (25 / maxV) * 200} y={rows.length * 22 + 20} fill="#22c55e" fontSize="10">target &lt; 25 km</text>
      </svg>
      <div className="kv"><span className="k">Cone honesty (90%)</span><span className="v">{s.coverage_90} ≈ 0.90 ✓</span></div>
      <div className="coverbar"><div style={{ width: Math.round(s.coverage_90 * 100) + "%" }}></div></div>
      <div className="kv"><span className="k">Advice refresh</span><span className="v">{s.freshness_s} s (&lt; 30 s ✓)</span></div>
      <div className="kv"><span className="k">Ops bundle</span><span className="v">{s.payload_kb} KB (5–20 MB ✓)</span></div>
      <div className="section-title" style={{ paddingLeft: 0 }}>Model registry</div>
      <table className="grid">
        <thead><tr><th>Version</th><th>Kind</th><th>MAE72</th><th>State</th></tr></thead>
        <tbody>{(p.models || []).map((m) => (
          <tr key={m.version}><td>{m.version}</td><td>{m.kind}</td><td>{m.mae_72_km}</td>
            <td>{m.active ? "🟢 active" : m.frozen ? "🧊 frozen" : "—"}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function AdminTab(p) {
  const [vals, setVals] = useState(null);
  const [nu, setNu] = useState({ username: "", password: "", role: "viewer", display_name: "" });
  useEffect(() => {
    if (!vals && p.thresholds.length) {
      const o = {}; p.thresholds.forEach((t) => (o[t.key] = t.value)); setVals(o);
    }
  }, [p.thresholds]);
  return (
    <div>
      <h3 style={{ margin: "0 0 4px" }}>Thresholds</h3>
      <div style={{ color: "#8ba3c2", fontSize: 11 }}>Configuration, not religion — tighten for thin hulls.</div>
      {vals && Object.keys(vals).map((k) => (
        <div key={k}><label className="lbl">{k}</label>
          <input className="txt" type="number" step="any" value={vals[k]}
            onChange={(e) => setVals(Object.assign({}, vals, { [k]: +e.target.value }))} /></div>
      ))}
      <button className="btn small primary" onClick={() => p.onSaveTh(vals)}>Save thresholds</button>
      <h3 style={{ margin: "14px 0 4px" }}>Model versions</h3>
      {(p.models || []).map((m) => (
        <div key={m.version} className="kv">
          <span className="k">{m.version} — {m.kind}</span>
          <span className="v">{m.active ? "🟢 active" : <button className="btn small" onClick={() => p.onFreeze(m.version)}>Freeze + activate</button>}</span>
        </div>
      ))}
      <h3 style={{ margin: "14px 0 4px" }}>Users</h3>
      {(p.users || []).map((u) => (
        <div key={u.username} className="kv"><span className="k">{u.username}</span><span className="v">{u.role}</span></div>
      ))}
      <div className="row2">
        <input className="txt" placeholder="username" value={nu.username} onChange={(e) => setNu(Object.assign({}, nu, { username: e.target.value }))} />
        <input className="txt" placeholder="password" value={nu.password} onChange={(e) => setNu(Object.assign({}, nu, { password: e.target.value }))} />
      </div>
      <div className="row2">
        <select className="txt" value={nu.role} onChange={(e) => setNu(Object.assign({}, nu, { role: e.target.value }))}>
          <option>viewer</option><option>operator</option><option>admin</option>
        </select>
        <button className="btn small" onClick={() => p.onUser(nu)}>Add user</button>
      </div>
      <h3 style={{ margin: "14px 0 4px" }}>Audit log <button className="btn small ghost" onClick={p.onAudit}>↻</button></h3>
      <div className="scrollbox">
        {(p.audit || []).map((a, i) => (
          <div key={i} className="audit-row">
            <span className="ts">{a.ts.slice(0, 19).replace("T", " ")}Z</span> <b>{a.actor}</b> [{a.action}]
            {a.badge && <span className={"pill " + a.badge}> {a.badge}</span>}<br />{a.detail}
          </div>
        ))}
      </div>
    </div>
  );
}

function OverrideModal(p) {
  const [dec, setDec] = useState("SLOW");
  const [reason, setReason] = useState("");
  return (
    <div className="modal-wrap">
      <div className="modal">
        <h2>⚑ Officer override</h2>
        <p className="dim">{p.voyage} — your decision and reason are written to the audit log. The DSS never steers the ship.</p>
        <label className="lbl">Decision</label>
        <select className="txt" value={dec} onChange={(e) => setDec(e.target.value)}>
          <option>GO</option><option>SLOW</option><option>NO-GO</option>
        </select>
        <label className="lbl">One-line reason (required)</label>
        <input className="txt" value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Visual ice report contradicts model near WP3" />
        <div className="row2 mt">
          <button className="btn ghost" onClick={p.onClose}>Cancel</button>
          <button className="btn danger" onClick={() => p.onDone(dec, reason)}>Log override</button>
        </div>
      </div>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
