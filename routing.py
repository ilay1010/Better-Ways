"""Route generation over a line graph with pedestrian access and transfers.

The network is the static backbone in cached_data plus live stops fetched
from OpenStreetMap around the origin, the destination and the backbone hubs
(geo.nearby_stops / geo.hub_stops). Live stops are linked into "lines" by
the line numbers in their `route_ref` tag; ride times for those links are
estimated from distance.

Nodes are (stop, line) "platform" states, (stop, line, "b") just-boarded
states, (stop, None) station nodes reached by alighting, (stop, "W") nodes
reached on foot, plus "ORIGIN" and "DEST". Walk edges only lead into "W"
nodes, which can only board, so walks never chain. Walking has no hard
limit: minutes beyond SOFT_WALK_LIMIT cost WALK_PENALTY times as much, so
short walks win whenever transit exists and long ones remain a fallback.
"""
import re
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from cached_data import (LINES, MAJOR_HUB, MINOR_STOP, PEAK_BUS_FACTOR, PEAK_WINDOWS,
                         STOPS, line_label)

TRANSFER_PENALTY_DIRECT = 120   # minutes-equivalent per boarding for the "fewest transfers" model
MINOR_TRANSFER_PENALTY = 60     # minutes-equivalent cost of boarding at a minor stop (hub model)

WALK_M_PER_MIN = 80             # ~4.8 km/h
WALK_DETOUR = 1.3               # street network vs. straight line
SOFT_WALK_LIMIT = 15            # minutes (~1 km); longer walks are penalized, not forbidden
WALK_PENALTY = 5                # cost multiplier for each walking minute beyond the soft limit
TRANSFER_WALK_REACH = 30        # stop-to-stop walk edges considered (keeps the graph small)
TRANSFER_WALK_NEIGHBORS = 10    # ...to the nearest N stops within reach
ACCESS_WALK_REACH = 60          # origin/destination to stops
ACCESS_FALLBACK_STOPS = 3       # always reachable, however far: the "no buses nearby" fallback

# live (OSM) lines: no timetable, so estimate from distance and line class
HUB_MIN_LINES = 8               # a live stop served by this many lines counts as a hub
# urban networks (Gush Dan, Haifa, Jerusalem...) reuse numbers below ~300, so those
# only link stops within LOCAL_LINE_MAX_KM; 300+ intercity numbers link at any distance
LOCAL_LINE_MAX_NUMBER = 300
LOCAL_LINE_MAX_KM = 30
ENDPOINT_STOPS_PER_LINE = 2     # nearest stops per line at each endpoint (one per direction)


@dataclass
class Leg:
    line: str
    mode: str
    stops: list
    minutes: float
    coords: list = field(default_factory=list)
    label: str = ""
    names: list = field(default_factory=list)
    headway: float = 0
    board_hub: str = ""        # boarding-stop properties, for resilience scoring
    board_safety: float = 0
    board_lines: int = 0
    estimated: bool = False    # live OSM link: times estimated from distance
    wait: float = 0            # expected wait at boarding (half headway), included in minutes
    ride: float = 0            # in-vehicle minutes
    agency: str = ""
    headsign: str = ""         # last stop of the line ("towards ...")
    geometry: list = None      # exact path (GTFS shape) when known
    pattern: tuple = None      # GTFS (route index, board position, alight position)
    start: float = None        # timetabled clock times, minutes after midnight
    end: float = None


@dataclass
class Route:
    title: str
    legs: list = field(default_factory=list)
    total_minutes: int = 0
    walk_minutes: int = 0
    transfers: int = 0
    resilience: int = 0
    stop_points: list = field(default_factory=list)  # (name, lat, lon, hub_type)
    depart: float = None       # leave the origin / reach the destination, minutes after midnight
    arrive: float = None
    timetabled: bool = False   # times are real trips, not frequency estimates

    @property
    def transit_minutes(self):
        return self.total_minutes - self.walk_minutes


def _xy(points):
    """(lat, lon) array -> approx metres, good enough for walking distances in Israel."""
    p = np.asarray(points, dtype=float)
    return np.column_stack((p[:, 0] * 111_000, p[:, 1] * 94_000))


def walk_minutes(a, b):
    """a, b: (lat, lon)."""
    d = _xy([a, b])
    return float(np.hypot(*(d[0] - d[1]))) * WALK_DETOUR / WALK_M_PER_MIN


def walk_cost(minutes):
    return minutes if minutes <= SOFT_WALK_LIMIT else SOFT_WALK_LIMIT + (minutes - SOFT_WALK_LIMIT) * WALK_PENALTY


def _is_peak(hour):
    return any(a <= hour < b for a, b in PEAK_WINDOWS)


def _line_number(ref):
    m = re.match(r"\d+", ref)
    return int(m.group()) if m else 0


class Network:
    """Static backbone + live stops/lines for one query."""

    def __init__(self, stops=None, lines=None, labels=None, estimated=()):
        self.stops = {**STOPS, **(stops or {})}
        self.lines = {**LINES, **(lines or {})}
        self.labels = labels or {}
        self.estimated = set(estimated)
        self.served = {}
        for _, _, line_stops, _ in self.lines.values():
            for s in line_stops:
                self.served[s] = self.served.get(s, 0) + 1

    def label(self, line):
        return self.labels.get(line) or line_label(line)

    def ll(self, sid):
        return self.stops[sid][1], self.stops[sid][2]


def live_network(origin, destination, origin_stops, dest_stops, hubs):
    """Link live OSM stops into ride segments by shared line number.

    For every line number serving a stop near the origin or destination, add a
    two-stop segment from that stop to each other place the same number serves:
    the other endpoint's area, or a backbone hub (one stop per hub). Times are
    estimated from distance; numbers below LOCAL_LINE_MAX_NUMBER repeat between
    cities, so those only link stops within LOCAL_LINE_MAX_KM.
    """
    def safety(s):
        return min(1.0, (0.8 if s["station"] else 0.35) + 0.2 * s["shelter"] + 0.1 * s["lit"])

    stops = {}
    for s in [*origin_stops, *dest_stops, *hubs]:
        if s["routes"] or s["station"]:
            hub = MAJOR_HUB if s["station"] or len(s["routes"]) >= HUB_MIN_LINES else MINOR_STOP
            stops[s["id"]] = (s["name"], s["lat"], s["lon"], hub, safety(s))

    def nearest_per_line(area, point):
        by_line = {}
        for s in sorted(area, key=lambda s: walk_minutes(point, (s["lat"], s["lon"]))):
            for ref in s["routes"]:
                if len(by_line.setdefault(ref, [])) < ENDPOINT_STOPS_PER_LINE:
                    by_line[ref].append(s)
        return by_line

    ends_o = nearest_per_line(origin_stops, origin)
    ends_d = nearest_per_line(dest_stops, destination)
    hub_by_line = {}
    for s in hubs:  # one stop per (line, backbone hub)
        for ref in s["routes"]:
            hub_by_line.setdefault(ref, {}).setdefault(s["near"], s)

    lines, labels = {}, {}
    for ref in set(ends_o) | set(ends_d):
        local = _line_number(ref) < LOCAL_LINE_MAX_NUMBER
        ends = ends_o.get(ref, []) + ends_d.get(ref, [])
        targets = ends + list(hub_by_line.get(ref, {}).values())
        for a in ends:
            for b in targets:
                if a["id"] == b["id"]:
                    continue
                km = walk_minutes((a["lat"], a["lon"]), (b["lat"], b["lon"])) * WALK_M_PER_MIN / WALK_DETOUR / 1000
                if km < 0.3 or (local and km > LOCAL_LINE_MAX_KM):
                    continue
                lid = f"osm:{ref}:" + ":".join(sorted((a["id"], b["id"])))
                if lid in lines:
                    continue
                speed = 30 if local else 55  # km/h incl. stops
                minutes = 2 + km * WALK_DETOUR * 60 / speed
                lines[lid] = ("Bus", 30 if local else 45, [a["id"], b["id"]], [minutes])
                labels[lid] = ref
    used = {s for _, _, st, _ in lines.values() for s in st}
    stops = {k: v for k, v in stops.items() if k in used}
    return Network(stops, lines, labels, estimated=lines)


def build_graph(net, origin, destination, hour=12):
    g = nx.DiGraph()
    peak = _is_peak(hour)
    for line, (mode, headway, stops, mins) in net.lines.items():
        factor = PEAK_BUS_FACTOR if (peak and mode == "Bus") else 1.0
        for s in stops:
            # boarding lands in a "just boarded" state with no alight edge, so a
            # board-then-alight at the same stop can't be used to chain walks
            for src in ((s, None), (s, "W")):
                g.add_edge(src, (s, line, "b"), kind="board", time=headway / 2,
                           hub=net.stops[s][3] == MAJOR_HUB, headway=headway, stop=s)
            g.add_edge((s, line), (s, None), kind="alight", time=0, stop=s)
        # lines run both directions
        for i, m in enumerate(mins):
            a, b = stops[i], stops[i + 1]
            for u, v in ((a, b), (b, a)):
                for src in ((u, line), (u, line, "b")):
                    g.add_edge(src, (v, line), kind="ride", time=m * factor)

    ids = [s for s in net.stops if s in net.served]
    xy = _xy([net.ll(s) for s in ids])
    for i, a in enumerate(ids):
        mins = np.hypot(*(xy - xy[i]).T) * WALK_DETOUR / WALK_M_PER_MIN
        near = np.argsort(mins)[1:TRANSFER_WALK_NEIGHBORS + 1]
        for j in near[mins[near] <= TRANSFER_WALK_REACH]:
            g.add_edge((a, None), (ids[j], "W"), kind="walk", time=float(mins[j]))

    for point, node, outbound in ((origin, "ORIGIN", True), (destination, "DEST", False)):
        mins = np.hypot(*(xy - _xy([point])[0]).T) * WALK_DETOUR / WALK_M_PER_MIN
        order = np.argsort(mins)
        reach = set(order[:ACCESS_FALLBACK_STOPS]) | set(np.where(mins <= ACCESS_WALK_REACH)[0])
        for j in reach:
            if outbound:
                g.add_edge(node, (ids[j], "W"), kind="walk", time=float(mins[j]))
            else:
                g.add_edge((ids[j], None), node, kind="walk", time=float(mins[j]))
    # just walking is always an option; the soft penalty keeps it to short distances
    g.add_edge("ORIGIN", "DEST", kind="walk", time=walk_minutes(origin, destination))
    return g


def _weight_fn(model):
    def w(u, v, d):
        t = d["time"]
        if d["kind"] == "walk":
            return walk_cost(t)
        if d["kind"] != "board":
            return t
        if model == "direct":
            return t + TRANSFER_PENALTY_DIRECT
        if model == "hub":
            return t + (5 if d["hub"] else MINOR_TRANSFER_PENALTY)
        return t
    return w


def _sid(node):
    return node if isinstance(node, str) else node[0]


def _path_to_route(net, g, path, title, origin, destination):
    points = {"ORIGIN": origin, "DEST": destination}
    coord = lambda s: points[s] if s in points else net.ll(s)
    legs, cur = [], None
    for u, v in zip(path, path[1:]):
        d = g.edges[u, v]
        if d["kind"] == "board":
            line = v[1]
            s = u[0]
            name, _, _, hub, safety = net.stops[s]
            cur = Leg(line, net.lines[line][0], [s], d["time"], label=net.label(line),
                      headway=net.lines[line][1], board_hub=hub, board_safety=safety,
                      board_lines=net.served.get(s, 0), estimated=line in net.estimated)
            legs.append(cur)
        elif d["kind"] == "ride":
            cur.stops.append(v[0])
            cur.minutes += d["time"]
        elif d["kind"] == "walk":
            legs.append(Leg("Walk", "Walk", [_sid(u), _sid(v)], d["time"]))
            cur = None
    return _make_route(net, title, legs, coord)


def _make_route(net, title, legs, coord):
    for l in legs:
        l.coords = [coord(s) for s in l.stops]
        l.names = [net.stops[s][0] if s in net.stops else s for s in l.stops]
    rides = [l for l in legs if l.mode != "Walk"]
    r = Route(title, legs,
              total_minutes=round(sum(l.minutes for l in legs)),
              walk_minutes=round(sum(l.minutes for l in legs if l.mode == "Walk")),
              transfers=max(0, len(rides) - 1))
    r.stop_points = [(net.stops[s][0], *net.ll(s), net.stops[s][3])
                     for l in legs for s in l.stops if s in net.stops]
    r.resilience = resilience_score(r)
    return r


def resilience_score(route):
    """0-100: how likely the passenger is to recover from a missed connection.

    Each transfer point is scored on station safety, alternative line density
    and the headway of the next line; the route score is the weakest-link
    blended with the average, minus a small per-transfer penalty.
    """
    rides = [l for l in route.legs if l.mode != "Walk"]
    if not rides:
        return 100  # walking only: no connection to miss
    first_freq = max(0.0, 1 - rides[0].headway / 90)
    points = []
    for leg in rides[1:]:
        density = min(leg.board_lines / 5, 1.0)
        freq = max(0.0, 1 - leg.headway / 60)
        hub_bonus = 1.0 if leg.board_hub == MAJOR_HUB else 0.6
        points.append((0.4 * leg.board_safety + 0.3 * density + 0.3 * freq) * hub_bonus)
    if not points:
        return round(100 * (0.85 + 0.15 * first_freq))
    worst, avg = min(points), sum(points) / len(points)
    score = (0.6 * worst + 0.4 * avg) * (0.97 ** len(points))
    return max(0, min(100, round(100 * (0.15 + 0.85 * score))))


MODELS = [
    ("fastest", "אפשרות א׳ · המהירה ביותר"),
    ("direct", "אפשרות ב׳ · ישירה"),
    ("hub", "אפשרות ג׳ · דרך מרכזי תחבורה"),
]


def plan_routes(origin, destination, hour=12, net=None):
    """origin, destination: (lat, lon) points anywhere in Israel. Never fails:
    if no transit path exists, the option is a walking-only route."""
    net = net or Network()
    g = build_graph(net, origin, destination, hour)
    routes = []
    for model, title in MODELS:
        try:
            path = nx.dijkstra_path(g, "ORIGIN", "DEST", weight=_weight_fn(model))
            routes.append(_path_to_route(net, g, path, title, origin, destination))
        except nx.NetworkXNoPath:
            walk = Leg("Walk", "Walk", ["ORIGIN", "DEST"], walk_minutes(origin, destination))
            routes.append(_make_route(net, title, [walk], {"ORIGIN": origin, "DEST": destination}.get))
    return routes
