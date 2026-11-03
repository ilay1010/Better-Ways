"""Real-world data: place autocomplete (Photon), road/footpath shapes (OSRM)
and live transit stops (Overpass).

Leg geometries come from the FOSSGIS OSRM servers, which route on the live
OpenStreetMap road network (car profile for buses / light rail, foot profile
for walks). Results are cached on disk so repeat searches are instant and
work offline; on any network failure we fall back to straight segments.
"""
import json
import os
import threading
import time

import requests
from geopy.geocoders import Nominatim

from cached_data import CITY, STOPS

PHOTON = "https://photon.komoot.io/api/"
ISRAEL_BBOX = "34.2,29.4,35.95,33.4"
OSRM = {
    "car": "https://routing.openstreetmap.de/routed-car/route/v1/driving/",
    "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/foot/",
}
OVERPASS_MIRRORS = ["https://overpass-api.de/api", "https://maps.mail.ru/osm/tools/overpass/api",
                    "https://overpass.kumi.systems/api", "https://overpass.private.coffee/api"]
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".geometry_cache.json")
TRANSIT_CACHE_FILE = os.path.join(os.path.dirname(__file__), ".transit_cache.json")
TRANSIT_TTL = 7 * 86400          # stops change rarely; refresh weekly
USER_AGENT = "BetterWays-transit-planner"

class JsonCache:
    """Disk-backed dict shared by worker threads (road_path runs in a
    ThreadPoolExecutor). Every access and the file write happen under one lock,
    so json.dump never sees the dict change size; writes go through a temp
    file + os.replace, so a crash mid-write can't corrupt the cache."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        try:
            with open(path, encoding="utf-8") as f:
                self._data = json.load(f)
        except (OSError, ValueError):
            self._data = {}

    def get(self, key):
        with self._lock:
            return self._data.get(key)

    def put(self, key, value):
        with self._lock:
            self._data[key] = value
            tmp = f"{self.path}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data.copy(), f, ensure_ascii=False)
            os.replace(tmp, self.path)


_geometry = JsonCache(CACHE_FILE)
_transit = JsonCache(TRANSIT_CACHE_FILE)


def road_path(coords, profile="car"):
    """coords: [(lat, lon), ...] waypoints in order -> [(lat, lon), ...] along real streets.

    Returns (path, is_real). is_real is False when we had to fall back to straight lines.
    """
    key = profile + ":" + ";".join(f"{lat:.5f},{lon:.5f}" for lat, lon in coords)
    cached = _geometry.get(key)
    if cached is not None:
        return [tuple(p) for p in cached], True
    url = OSRM[profile] + ";".join(f"{lon:.5f},{lat:.5f}" for lat, lon in coords)
    try:
        r = requests.get(url, params={"overview": "full", "geometries": "geojson"},
                         headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok":
            raise ValueError(data.get("code"))
        path = [(lat, lon) for lon, lat in data["routes"][0]["geometry"]["coordinates"]]
    except Exception:
        return list(coords), False
    _geometry.put(key, path)
    return path, True


def format_place(name, city, street=None, number=None):
    """-> '[name] city, street number' (each part only if present)."""
    addr = " ".join(p for p in (street, number) if p)
    label = ", ".join(p for p in (city, addr) if p)
    if name and name not in (street, city):
        label = f"[{name}] {label}".strip()
    return label


def reverse_geocode(lat, lon):
    """Map click -> (label, short_name) in Hebrew, formatted like the autocomplete."""
    try:
        loc = Nominatim(user_agent=USER_AGENT, timeout=10).reverse(
            (lat, lon), language="he", zoom=18, addressdetails=True)
    except Exception:
        loc = None
    if not loc:
        return f"{lat:.5f}, {lon:.5f}", "נקודה במפה"
    a = loc.raw.get("address", {})
    city = next((a[k] for k in ("city", "town", "village", "municipality", "county") if k in a), None)
    street = next((a[k] for k in ("road", "pedestrian", "footway", "square") if k in a), None)
    name = loc.raw.get("name") or None
    label = format_place(name, city, street, a.get("house_number")) or loc.address
    return label, name or street or city or label


def _station_hits(term):
    hits = [(format_place(name, CITY[sid]), lat, lon, name)
            for sid, (name, lat, lon, _, _) in STOPS.items() if term in name or term in CITY[sid]]
    return [(label, (lat, lon, name, label)) for label, lat, lon, name in hits]


PHOTON_TIMEOUT = 4    # seconds; search-as-you-type can't wait longer
PHOTON_RETRY = 60     # after a failure, skip Photon for this many seconds
_photon_lock = threading.Lock()
_photon_cache = {}    # term -> features; only successful answers are kept
_photon_down_until = 0.0


def _photon(term, limit):
    """Photon features for term, or [] while the service is unreachable."""
    global _photon_down_until
    with _photon_lock:
        if term in _photon_cache:
            return _photon_cache[term]
        if time.time() < _photon_down_until:
            return []
    try:
        r = requests.get(PHOTON, params={"q": term, "limit": limit, "bbox": ISRAEL_BBOX},
                         headers={"User-Agent": USER_AGENT}, timeout=PHOTON_TIMEOUT)
        r.raise_for_status()
        features = r.json()["features"]
    except Exception:
        with _photon_lock:
            _photon_down_until = time.time() + PHOTON_RETRY
        return []
    with _photon_lock:
        _photon_cache[term] = features
    return features


class StopIndex:
    """Offline search over the official GTFS stops and the cities they serve,
    so autocomplete keeps working when Photon is down."""

    def __init__(self, names, cities, lls):
        places, by_city = {}, {}
        for name, city, (lat, lon) in zip(names, cities, lls):
            places.setdefault((name, city), (lat, lon))  # both directions share a name
            if city:
                by_city.setdefault(city, []).append((lat, lon))
        self.stops = [(name, city, lat, lon, f"{name} {city}") for (name, city), (lat, lon) in places.items()]
        # city "centre" = mean of its stops; its stop count ranks big cities first
        self.size = {city: len(pts) for city, pts in by_city.items()}
        self.cities = [(city, sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
                       for city, pts in by_city.items()]

    def search(self, term, limit):
        words = term.split()
        cities = sorted((c for c in self.cities if term in c[0]),
                        key=lambda c: (not c[0].startswith(term), -self.size[c[0]]))
        out = [(city, (lat, lon, city, city)) for city, lat, lon in cities[:2]]
        stops = [s for s in self.stops if all(w in s[4] for w in words)]
        stops.sort(key=lambda s: (not s[0].startswith(words[0]), -self.size.get(s[1], 0), len(s[0])))
        for name, city, lat, lon, _ in stops[:limit]:
            label = format_place(name, city)
            out.append((f"🚏 {label}", (lat, lon, name, label)))
        return out


def search_places(term, limit=8, stop_index=None):
    """Autocomplete: free text -> [(label, (lat, lon, short_name, label)), ...].

    Our own stations match locally first; addresses and buildings come from
    Photon (OSM-based, built for search-as-you-type), restricted to Israel;
    GTFS stops and cities (stop_index) fill the rest, and keep search working
    when Photon is unreachable.
    """
    term = term.strip()
    if len(term) < 2:
        return []
    out = _station_hits(term)[:3]
    features = _photon(term, limit)
    for f in features:
        p = f["properties"]
        if p.get("countrycode", "IL") != "IL":
            continue
        city = p.get("city") or p.get("town") or p.get("village") or p.get("county") or ""
        name, street, kind = p.get("name"), p.get("street"), p.get("type")
        if kind == "street":
            name, street = None, street or name
        elif kind in ("city", "town", "village"):
            name, city = None, name
        label = format_place(name, city, street, p.get("housenumber"))
        lon, lat = f["geometry"]["coordinates"]
        out.append((label, (lat, lon, p.get("name") or label, label)))
    if stop_index is not None:
        out += stop_index.search(term, limit)
    seen, uniq = set(), []
    for label, val in out:
        if label and val[3] not in seen:
            seen.add(val[3])
            uniq.append((label, val))
    return uniq[:limit]


# ---------------------------------------------------------------------------
# Live transit stops (Overpass)
#
# Israeli stops in OSM are imported from the Ministry of Transport GTFS feed:
# every official stop is there, tagged with its MOT stop code and `route_ref`
# (the line numbers serving it). Route relations are mostly *not* mapped, so
# routing.py links stops through shared line numbers.
# ---------------------------------------------------------------------------

STOP_SELECTORS = ('[highway=bus_stop]', '[railway~"^(station|halt)$"][!subway]')


def _overpass(query, timeout=90):
    # public servers are often at capacity: main server, the fastest mirror,
    # then the main server again after a pause (other mirrors mostly time out)
    for base, pause in ((OVERPASS_MIRRORS[0], 0), (OVERPASS_MIRRORS[1], 0), (OVERPASS_MIRRORS[0], 5)):
        time.sleep(pause)
        try:
            r = requests.post(f"{base}/interpreter", data={"data": query},
                              headers={"User-Agent": USER_AGENT}, timeout=timeout)
            r.raise_for_status()
            return r.json()["elements"]
        except Exception:
            continue
    return None


def _parse_stop(e, near=None):
    t = e.get("tags", {})
    name = t.get("name:he") or t.get("name") or t.get("gtfs:stop_name:IL-MOT") or f"תחנה {t.get('ref', '')}"
    city = t.get("object:city")
    return {
        "id": f"n{e['id']}",
        "name": f"{name} ({city})" if city and city not in name else name,
        "lat": e["lat"], "lon": e["lon"],
        "routes": [r.strip() for r in t.get("route_ref", "").split(";") if r.strip()],
        "station": "railway" in t,
        "shelter": t.get("shelter") == "yes",
        "lit": t.get("lit") == "yes",
        "near": near,  # static stop id this was fetched around (hub layer only)
    }


def _cached_query(key, build):
    hit = _transit.get(key)
    if hit and time.time() - hit["at"] < TRANSIT_TTL:
        return hit["stops"]
    stops = build()
    if stops is None:  # network failure: don't cache, use a stale copy if we have one
        return hit["stops"] if hit else []
    _transit.put(key, {"at": time.time(), "stops": stops})
    return stops


def nearby_stops(lat, lon, radius=1500, fallback_radius=3000):
    """Every OSM bus stop / rail station within `radius` m (widening to
    `fallback_radius` if the area is empty) -> list of stop dicts."""
    def build():
        for r in (radius, fallback_radius):
            body = "".join(f"node(around:{r},{lat:.5f},{lon:.5f}){s};" for s in STOP_SELECTORS)
            els = _overpass(f"[out:json][timeout:60];({body});out body;")
            if els is None:
                return None
            stops = [_parse_stop(e) for e in els if e["type"] == "node"]
            if any(s["routes"] for s in stops):
                return stops
        return stops
    return _cached_query(f"near:{lat:.3f},{lon:.3f}:{radius}", build)


def hub_stops(radius=400):
    """Bus stops around each backbone station in cached_data.STOPS: the places
    where live lines from the origin/destination can join the static network."""
    def build():
        body = "".join(f"node(around:{radius},{la:.5f},{lo:.5f})[highway=bus_stop];"
                       for _, la, lo, _, _ in STOPS.values())
        els = _overpass(f"[out:json][timeout:120];({body});out body;", timeout=150)
        if els is None:
            return None
        out = []
        for e in els:
            if e["type"] != "node" or not e.get("tags", {}).get("route_ref"):
                continue
            near = min(STOPS, key=lambda s: (STOPS[s][1] - e["lat"]) ** 2 + (STOPS[s][2] - e["lon"]) ** 2)
            out.append(_parse_stop(e, near))
        return out
    return _cached_query(f"hubs:{radius}:{len(STOPS)}", build)
