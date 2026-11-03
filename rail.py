"""Railway track geometry for Israel from OpenStreetMap (osmnx).

The rail layer (heavy rail + light rail / tram) for the whole country is a
single small Overpass download, saved to .rail_graph.graphml on first use.
Train legs are routed station-to-station along the actual tracks; if a
station can't be snapped to a track or the path is implausible, that hop
falls back to a straight segment.
"""
import ast
import os
from functools import lru_cache

import networkx as nx
import numpy as np
import osmnx as ox
from geopy.distance import geodesic

from geo import OVERPASS_MIRRORS

GRAPH_FILE = os.path.join(os.path.dirname(__file__), ".rail_graph.graphml")
BBOX = (34.2, 29.4, 35.95, 33.4)  # west, south, east, north
RAIL_FILTER = '["railway"~"^(rail|light_rail|tram)$"]["service"!~"yard|siding|spur|crossover"]'
KINDS = {"Train": {"rail"}, "Light Rail": {"light_rail", "tram"}}
MAX_SNAP_M = 1500      # station must be within this distance of a track
SNAP_SLACK_M = 150     # also consider other tracks up to this much farther than the nearest
MAX_DETOUR = 2.5        # track path vs. straight-line distance


def _download():
    ox.settings.useful_tags_way = list({*ox.settings.useful_tags_way, "railway"})
    # rail-only results are tiny, so one Overpass query for the whole country is fine
    ox.settings.max_query_area_size = 1e11
    ox.settings.requests_timeout = 300
    err = None
    for url in OVERPASS_MIRRORS:  # the main server is often at capacity
        ox.settings.overpass_url = url
        try:
            g = ox.graph_from_bbox(BBOX, custom_filter=RAIL_FILTER, simplify=True, retain_all=True)
        except Exception as e:
            err = e
            continue
        ox.save_graphml(g, GRAPH_FILE)
        return g
    raise err


@lru_cache(maxsize=1)
def _full_graph():
    g = ox.load_graphml(GRAPH_FILE) if os.path.exists(GRAPH_FILE) else _download()
    return ox.convert.to_undirected(g)


def _tags(v):
    # simplified edges may merge several ways; graphml round-trips lists as their repr
    if isinstance(v, str) and v.startswith("["):
        v = ast.literal_eval(v)
    return set(v) if isinstance(v, list) else {v}


def _meters(p, q):
    # equirectangular approximation is plenty at track-vertex spacing
    return float(np.hypot((p[0] - q[0]) * 111_000, (p[1] - q[1]) * 94_000))


@lru_cache(maxsize=None)
def _graph(mode):
    """Explode simplified edges back into their track vertices, so a station can
    snap to the track that passes it rather than to the nearest junction."""
    g = _full_graph()
    want = KINDS[mode]
    track = nx.Graph()
    node_ll = lambda n: (round(g.nodes[n]["y"], 6), round(g.nodes[n]["x"], 6))
    for u, v, d in g.edges(data=True):
        if not _tags(d.get("railway")) & want:
            continue
        if "geometry" in d:
            pts = [(round(y, 6), round(x, 6)) for x, y in d["geometry"].coords]
        else:
            pts = [node_ll(u), node_ll(v)]
        for p, q in zip(pts, pts[1:]):
            track.add_edge(p, q, length=_meters(p, q))
    nodes = list(track.nodes)
    return track, nodes, np.array(nodes)


def _candidates(mode, lat, lon):
    """Track vertices near a station. Stations sit on several parallel tracks
    that may only join far away, so we keep every track within reach."""
    _, nodes, xy = _graph(mode)
    d = np.hypot((xy[:, 0] - lat) * 111_000, (xy[:, 1] - lon) * 94_000)
    best = d.min()
    if best > MAX_SNAP_M:
        return []
    return [nodes[i] for i in np.where(d <= best + SNAP_SLACK_M)[0]]


def _hop(mode, a, b):
    g = _graph(mode)[0]
    src, dst = _candidates(mode, *a), set(_candidates(mode, *b))
    if not src or not dst:
        return None
    limit = MAX_DETOUR * geodesic(a, b).meters + 2000
    dist, paths = nx.multi_source_dijkstra(g, src, cutoff=limit, weight="length")
    reached = [n for n in dst if n in dist]
    if not reached:
        return None
    return [a, *paths[min(reached, key=dist.get)], b]


@lru_cache(maxsize=512)
def rail_path(coords, mode="Train"):
    """coords: tuple of (lat, lon) stations in order -> ([(lat, lon), ...], all_hops_on_track)."""
    try:
        _graph(mode)
    except Exception:
        return list(coords), False
    path, ok = [], True
    for a, b in zip(coords, coords[1:]):
        hop = _hop(mode, a, b)
        ok &= hop is not None
        path += hop or [a, b]
    return path, ok
