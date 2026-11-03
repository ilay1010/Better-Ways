"""Official Israeli transit network from the Ministry of Transport GTFS feed.

Every line in the country (Israel Railways, light rail, intercity, urban and
regional-council feeder buses) with its real stop sequence, scheduled travel
times, frequency and shape.

build() turns the ~135 MB feed into .gtfs/network.pkl once: for each route
pattern it keeps one representative trip (the one departing closest to 10:00
on a reference weekday), the day's frequency, and the trip's shape.

Routing runs on a national frequency-based graph with scipy's C Dijkstra.
Edge weights are rebuilt per query, which is how the direction penalty works:
every ride or walk edge that takes the traveller farther from the destination
costs DIRECTION_PENALTY minutes per km, so itineraries that head south for a
northern trip lose to ones that make progress.
"""
import csv
import datetime as dt
import io
import os
import pickle
import re
import zipfile

import numpy as np
import requests
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from cached_data import MAJOR_HUB, MINOR_STOP
from routing import (ACCESS_FALLBACK_STOPS, MINOR_TRANSFER_PENALTY, SOFT_WALK_LIMIT,
                     TRANSFER_PENALTY_DIRECT, WALK_DETOUR, WALK_M_PER_MIN, WALK_PENALTY, Leg, Route)

FEED_URL = "https://gtfs.mot.gov.il/gtfsfiles/israel-public-transportation.zip"
DIR = os.path.join(os.path.dirname(__file__), ".gtfs")
FEED_ZIP = os.path.join(DIR, "israel-public-transportation.zip")
NET_FILE = os.path.join(DIR, "network.pkl")
FEED_MAX_AGE_DAYS = 14
BUILD_VERSION = 4  # bump when the network.pkl format changes

MODES = {"0": "Light Rail", "2": "Train", "3": "Bus", "715": "Bus"}  # 715 = on-demand regional
REP_MINUTE = 10 * 60          # representative trip: departs closest to 10:00
DAY_WINDOW = (6 * 60, 21 * 60)  # frequency measured over this service window
MAX_WAIT = 60                 # expected wait = half headway, capped
MAX_IDLE = 90                 # timetable: longer idle time at a stop (or before leaving) triggers a re-plan
REPAIR_TRIES = 4
SPAN_SLACK = (15, 60)         # a line counts as running from 60 min before its first pass to 15 min after its last
TIME_STAGES = 8              # hourly frequency snapshots along the journey (0..7 h after the requested time)

DIRECTION_PENALTY = 1.5       # minutes per km moved away from the destination
TRANSFER_WALK_MAX = 20        # minutes, stop-to-stop
TRANSFER_WALK_NEIGHBORS = 10
ACCESS_WALK_MAX = 30          # minutes, origin/destination to stops (nearest few always allowed)
HUB_MIN_PATTERNS = 10         # stops served by this many route patterns count as hubs

WALK, BOARD, RIDE, ALIGHT = 0, 1, 2, 3


# ---------------------------------------------------------------------------
# Feed download + build
# ---------------------------------------------------------------------------

def ensure_feed():
    """Download the feed if missing or stale. Returns the zip path, or None."""
    fresh = os.path.exists(FEED_ZIP) and (
        dt.datetime.now().timestamp() - os.path.getmtime(FEED_ZIP) < FEED_MAX_AGE_DAYS * 86400)
    if fresh:
        return FEED_ZIP
    os.makedirs(DIR, exist_ok=True)
    tmp = FEED_ZIP + ".part"
    try:
        with requests.get(FEED_URL, stream=True, timeout=120,
                          headers={"User-Agent": "BetterWays-transit-planner"}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
        os.replace(tmp, FEED_ZIP)
    except Exception:
        return FEED_ZIP if os.path.exists(FEED_ZIP) else None  # stale beats nothing
    return FEED_ZIP


def _rows(z, name):
    with z.open(name) as f:
        reader = csv.reader(io.TextIOWrapper(f, "utf-8-sig", newline=""))
        header = next(reader)
        col = {h: i for i, h in enumerate(header)}
        for row in reader:
            yield col, row


def _minutes(hms):
    h, m, s = hms.split(":")
    return int(h) * 60 + int(m) + int(s) / 60


def _ref_date(z):
    cal = [(row[c["start_date"]], row[c["end_date"]]) for c, row in _rows(z, "calendar.txt")]
    start = dt.datetime.strptime(min(a for a, _ in cal), "%Y%m%d").date()
    end = dt.datetime.strptime(max(b for _, b in cal), "%Y%m%d").date()
    day = max(dt.date.today(), start)
    while day <= end:
        if day.weekday() in (6, 0, 1, 2, 3):  # Sunday-Thursday
            return day
        day += dt.timedelta(days=1)
    return start


DOW = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _week_masks(z, ref):
    """service_id -> bit mask of the weekdays (bit = date.weekday()) it runs on,
    over the 7 days starting at ref."""
    week = [ref + dt.timedelta(days=i) for i in range(7)]
    masks = {}
    for c, row in _rows(z, "calendar.txt"):
        m = 0
        for day in week:
            ymd = day.strftime("%Y%m%d")
            if row[c[DOW[day.weekday()]]] == "1" and row[c["start_date"]] <= ymd <= row[c["end_date"]]:
                m |= 1 << day.weekday()
        if m:
            masks[row[c["service_id"]]] = m
    return masks


def build(zip_path=FEED_ZIP, out=NET_FILE):
    z = zipfile.ZipFile(zip_path)
    date = _ref_date(z)
    ref_bit = 1 << date.weekday()
    week_start = max(dt.date.today(), date - dt.timedelta(days=6))
    masks = _week_masks(z, week_start)

    agency = {row[c["agency_id"]]: row[c["agency_name"]] for c, row in _rows(z, "agency.txt")}
    routes = {}
    for c, row in _rows(z, "routes.txt"):
        if row[c["route_type"]] in MODES:
            desc = row[c["route_desc"]].split("-")
            routes[row[c["route_id"]]] = dict(
                short=row[c["route_short_name"]], long=row[c["route_long_name"]],
                agency=agency.get(row[c["agency_id"]], ""), mode=MODES[row[c["route_type"]]],
                # all variants of one line+direction share "makat-direction" in route_desc
                group=(row[c["agency_id"]], *desc[:2]) if len(desc) >= 2 else row[c["route_id"]])

    trips = {}  # every trip running in the coming week: (route_id, shape_id, weekday mask)
    for c, row in _rows(z, "trips.txt"):
        if row[c["route_id"]] in routes and row[c["service_id"]] in masks:
            trips[row[c["trip_id"]]] = (row[c["route_id"]], row[c["shape_id"]], masks[row[c["service_id"]]])

    # pass 1: first departure and last arrival of every trip (its own running time)
    first_dep, last_arr = {}, {}
    for c, row in _rows(z, "stop_times.txt"):
        t = row[c["trip_id"]]
        if t in trips:
            seq = int(row[c["stop_sequence"]])
            if seq == 1:
                first_dep[t] = _minutes(row[c["departure_time"]])
            if seq > last_arr.get(t, (0, 0))[0]:
                last_arr[t] = (seq, row[c["arrival_time"]])
    by_route, group_day = {}, {}
    for trip, dep in first_dep.items():
        rid, _, mask = trips[trip]
        by_route.setdefault(rid, []).append((dep, trip))
        if mask & ref_bit and DAY_WINDOW[0] <= dep < DAY_WINDOW[1]:
            g = routes[rid]["group"]
            group_day[g] = group_day.get(g, 0) + 1
    span = DAY_WINDOW[1] - DAY_WINDOW[0]
    headway_of = lambda n: min(240.0, max(3.0, span / n)) if n else 240.0
    rep = {}
    for rid, deps in by_route.items():
        # representative: reference-day trip closest to 10:00 (any day for weekend-only lines)
        pool = [d for d in deps if trips[d[1]][2] & ref_bit] or deps
        _, trip = min(pool, key=lambda d: abs(d[0] - REP_MINUTE))
        rep[trip] = (rid, headway_of(group_day.get(routes[rid]["group"], 0)))
    duration = lambda t: _minutes(last_arr[t][1]) - first_dep[t] if t in last_arr else 0

    # pass 2: stop sequences of the representative trips, plus every train trip:
    # rail patterns are individual services, so their frequency is counted per
    # track segment (all trains running station A -> next station B)
    seqs, train_seqs = {}, {}
    for c, row in _rows(z, "stop_times.txt"):
        t = row[c["trip_id"]]
        if t in rep or (t in trips and routes[trips[t][0]]["mode"] == "Train"):
            item = (int(row[c["stop_sequence"]]), row[c["stop_id"]], _minutes(row[c["arrival_time"]]),
                    float(row[c["shape_dist_traveled"]] or 0))
            if t in rep:
                seqs.setdefault(t, []).append(item)
            if routes[trips[t][0]]["mode"] == "Train":
                train_seqs.setdefault(t, []).append(item)
    segment_day = {}
    for t, seq in train_seqs.items():
        seq.sort()
        if not trips[t][2] & ref_bit:
            continue
        for (_, a, ta, _), (_, b, _, _) in zip(seq, seq[1:]):
            if DAY_WINDOW[0] <= ta < DAY_WINDOW[1]:
                segment_day[a, b] = segment_day.get((a, b), 0) + 1

    used = {s for v in seqs.values() for _, s, _, _ in v}
    stops = {}
    city_re = re.compile(r"עיר:\s*(.*?)\s*רציף")
    for c, row in _rows(z, "stops.txt"):
        sid = row[c["stop_id"]]
        if sid in used:
            m = city_re.search(row[c["stop_desc"]])
            stops[sid] = (row[c["stop_name"]], float(row[c["stop_lat"]]), float(row[c["stop_lon"]]),
                          m.group(1) if m else "", row[c["stop_code"]])
    stop_ids = sorted(stops)
    index = {s: i for i, s in enumerate(stop_ids)}

    shape_ids = {trips[t][1] for t in rep if trips[t][1]}
    pts = {}
    for c, row in _rows(z, "shapes.txt"):
        sh = row[c["shape_id"]]
        if sh in shape_ids:
            pts.setdefault(sh, []).append((int(row[c["shape_pt_sequence"]]),
                                           float(row[c["shape_pt_lat"]]), float(row[c["shape_pt_lon"]])))
    shapes = {sh: np.array([(la, lo) for _, la, lo in sorted(v)], dtype=np.float32) for sh, v in pts.items()}

    out_routes = []
    for trip, (rid, headway) in rep.items():
        seq = sorted(seqs.get(trip, []))
        if len(seq) < 2:
            continue
        r = routes[rid]
        if r["mode"] == "Train":  # per boarding position: trains to the next station
            hw = [headway_of(segment_day.get((a, b), 0)) for (_, a, _, _), (_, b, _, _) in zip(seq, seq[1:])]
            headways = np.array(hw + [240.0], dtype=np.float32)
        else:
            headways = np.full(len(seq), headway, dtype=np.float32)
        # timetable: every trip of this pattern in the coming week. A trip's time at
        # stop k is start + times[k] * scale (its running time relative to the
        # representative trip, so rush-hour trips are slower)
        rep_len = duration(trip)
        tt = sorted(by_route[rid])
        out_routes.append(dict(
            r, route_id=rid, headway=headways, shape=trips[trip][1],
            deps=np.array([d for d, _ in tt], dtype=np.float32),
            days=np.array([trips[t][2] for _, t in tt], dtype=np.uint8),
            scale=np.array([np.clip(duration(t) / rep_len, 0.5, 2.0) if rep_len > 0 and duration(t) > 0 else 1.0
                            for _, t in tt], dtype=np.float32),
            stops=np.array([index[s] for _, s, _, _ in seq], dtype=np.int32),
            times=np.array([m - seq[0][2] for _, _, m, _ in seq], dtype=np.float32),
            # metres along the route at each stop: maps live GPS progress to scheduled time
            dists=np.array([d for _, _, _, d in seq], dtype=np.float32)))

    net = dict(
        version=BUILD_VERSION, date=date.isoformat(), feed_mtime=os.path.getmtime(zip_path),
        week=[(week_start + dt.timedelta(days=i)).isoformat() for i in range(7)],
        stop_name=[stops[s][0] for s in stop_ids], stop_city=[stops[s][3] for s in stop_ids],
        stop_code=[stops[s][4] for s in stop_ids],
        stop_ll=np.array([(stops[s][1], stops[s][2]) for s in stop_ids], dtype=np.float64),
        routes=out_routes, shapes=shapes)
    tmp = out + ".part"
    with open(tmp, "wb") as f:
        pickle.dump(net, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, out)
    return net


# ---------------------------------------------------------------------------
# Graph + routing
# ---------------------------------------------------------------------------

def _xy(ll):
    ll = np.asarray(ll, dtype=float).reshape(-1, 2)
    return np.column_stack((ll[:, 0] * 111_000, ll[:, 1] * 94_000))


def _walk_min(meters):
    return meters * WALK_DETOUR / WALK_M_PER_MIN


class GtfsNetwork:
    """National graph. Node ids: S(i)=i station (after alighting), W(i)=N+i
    reached on foot, P(p)=2N+p on board at pattern position p, B(p)=2N+T+p
    just boarded (no alight edge, so board+alight can't chain two walks)."""

    def __init__(self, data):
        self.d = data
        self.N = N = len(data["stop_name"])
        self.ll = data["stop_ll"]
        self.xy = _xy(self.ll)
        self.tree = cKDTree(self.xy)
        routes = data["routes"]
        self.routes = routes

        pos_route, pos_k, pos_stop, pos_t, pos_hw = [], [], [], [], []
        for r_i, r in enumerate(routes):
            n = len(r["stops"])
            pos_route.append(np.full(n, r_i, dtype=np.int32))
            pos_k.append(np.arange(n, dtype=np.int32))
            pos_stop.append(r["stops"])
            pos_t.append(r["times"])
            pos_hw.append(r["headway"])
        self.pos_route = np.concatenate(pos_route)
        self.pos_k = np.concatenate(pos_k)
        self.pos_stop = np.concatenate(pos_stop)
        pos_t = np.concatenate(pos_t)
        headway = np.concatenate(pos_hw)
        self.T = T = len(self.pos_route)
        self.ORIGIN, self.DEST = 2 * N + 2 * T, 2 * N + 2 * T + 1
        self.n_nodes = self.DEST + 1
        P = lambda p: 2 * N + p
        B = lambda p: 2 * N + T + p

        patterns = np.bincount(self.pos_stop, minlength=N)
        rail = np.zeros(N, dtype=bool)
        for r in routes:
            if r["mode"] != "Bus":
                rail[r["stops"]] = True
        self.hub = rail | (patterns >= HUB_MIN_PATTERNS)
        self.patterns = patterns

        p = np.arange(T)
        last = np.r_[self.pos_route[1:] != self.pos_route[:-1], True]   # last stop of its pattern
        first = self.pos_k == 0
        wait = np.minimum(headway / 2, MAX_WAIT)

        nxt = p[~last]
        ride_t = np.maximum(pos_t[nxt + 1] - pos_t[nxt], 0.3)
        brd = p[~last]
        alg = p[~first]
        # transfer walks: each station to its nearest stops within reach
        dist, nbr = self.tree.query(self.xy, k=TRANSFER_WALK_NEIGHBORS + 1)
        src_w = np.repeat(np.arange(N), TRANSFER_WALK_NEIGHBORS + 1)
        dist, nbr = dist.ravel(), nbr.ravel()
        ok = (nbr != src_w) & (_walk_min(dist) <= TRANSFER_WALK_MAX) & (nbr < N)

        self.src = np.concatenate([
            P(nxt), B(nxt),                                   # ride
            self.pos_stop[brd], N + self.pos_stop[brd],       # board from station / on foot
            P(alg),                                           # alight
            src_w[ok]])                                       # walk
        self.dst = np.concatenate([
            P(nxt + 1), P(nxt + 1),
            B(brd), B(brd),
            self.pos_stop[alg],
            N + nbr[ok]])
        self.time = np.concatenate([
            ride_t, ride_t, wait[brd], wait[brd], np.zeros(len(alg)), _walk_min(dist[ok])])
        self.kind = np.concatenate([
            np.full(2 * len(nxt), RIDE), np.full(2 * len(brd), BOARD),
            np.full(len(alg), ALIGHT), np.full(ok.sum(), WALK)]).astype(np.int8)
        self.board_hub = np.concatenate([
            np.zeros(2 * len(nxt), bool), np.tile(self.hub[self.pos_stop[brd]], 2),
            np.zeros(len(alg) + ok.sum(), bool)])
        # stop index of every node, for coordinates (ORIGIN/DEST filled per query)
        self.node_stop = np.concatenate([np.arange(N), np.arange(N), self.pos_stop, self.pos_stop])
        self.board_edges = np.arange(2 * len(nxt), 2 * len(nxt) + 2 * len(brd))
        self.board_pos = np.tile(brd, 2)
        self.route_start = np.r_[0, np.cumsum([len(r["stops"]) for r in routes])]  # first position of each route
        self._staged = None
        self._frequency_events()

    def _frequency_events(self):
        """Departure events behind time-of-day frequencies, counted per key.
        Train positions: every train over the track segment to the next station.
        Bus/light-rail positions: the trips of every variant of the same line
        that calls at this stop (a night-only branch doesn't make its siblings'
        stops look frequent, and vice versa). Key k sums the events of
        self.key_sources[k] (a sparse key x source matrix)."""
        R = len(self.routes)
        seg_keys, pos_key = {}, np.empty(self.T, dtype=np.int64)
        ev_src, ev_time, ev_days, ev_train = [], [], [], []
        by_group = {}
        for r_i, r in enumerate(self.routes):
            if r["mode"] != "Train":
                by_group.setdefault(r["group"], []).append(r_i)
        rows, cols = [], []
        start = 0
        for r_i, r in enumerate(self.routes):
            n = len(r["stops"])
            if r["mode"] == "Train":
                for k in range(n - 1):
                    seg = seg_keys.setdefault((int(r["stops"][k]), int(r["stops"][k + 1])), len(seg_keys))
                    pos_key[start + k] = R + seg
                    ev_time.append(r["deps"] + r["times"][k] * r["scale"])
                    ev_src.append(np.full(len(r["deps"]), R + seg))
                    ev_days.append(r["days"])
                    ev_train.append(np.ones(len(r["deps"]), bool))
                pos_key[start + n - 1] = pos_key[start + n - 2]
            else:
                ev_time.append(r["deps"])
                ev_src.append(np.full(len(r["deps"]), r_i))
                ev_days.append(r["days"])
                ev_train.append(np.zeros(len(r["deps"]), bool))
                pos_key[start:start + n] = -1  # filled below: one key per (line group, stop)
            start += n
        n_src = R + len(seg_keys)
        # bus keys: (group, stop) -> the group's variants calling there
        gs_keys = {}
        for g, members in by_group.items():
            serving = {}
            for r_i in members:
                for s in set(self.routes[r_i]["stops"].tolist()):
                    serving.setdefault(s, []).append(r_i)
            for s, variants in serving.items():
                gs_keys[g, s] = len(gs_keys)
                rows += [gs_keys[g, s]] * len(variants)
                cols += variants
        n_gs = len(gs_keys)
        bus_pos = pos_key < 0
        pos_key[~bus_pos] += n_gs - R  # segment source R+i -> key n_gs+i
        pos_key[bus_pos] = [gs_keys[self.routes[int(self.pos_route[p])]["group"], int(self.pos_stop[p])]
                            for p in np.flatnonzero(bus_pos)]
        rows += list(range(n_gs, n_gs + len(seg_keys)))
        cols += list(range(R, n_src))
        self.key_sources = csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n_gs + len(seg_keys), n_src))
        self.n_src, self.pos_key = n_src, pos_key
        # service span of every key per weekday: first and last time a vehicle
        # passes (minutes after that day's midnight). Frequencies count trip
        # starts, so without this a line that ended service would still look
        # alive near the end of its route
        first = np.full((self.T, 7), np.inf, np.float32)
        last = np.full((self.T, 7), -np.inf, np.float32)
        start = 0
        for r in self.routes:
            n = len(r["stops"])
            passes = r["deps"][:, None] + r["scale"][:, None] * r["times"][None, :]
            for b in range(7):
                sel = r["days"] & (1 << b) > 0
                if sel.any():
                    first[start:start + n, b] = passes[sel].min(0)
                    last[start:start + n, b] = passes[sel].max(0)
            start += n
        n_keys = n_gs + len(seg_keys)
        self.key_first = np.full((n_keys, 7), np.inf, np.float32)
        self.key_last = np.full((n_keys, 7), -np.inf, np.float32)
        np.minimum.at(self.key_first, pos_key, first)
        np.maximum.at(self.key_last, pos_key, last)
        self.ev_src, self.ev_time = np.concatenate(ev_src), np.concatenate(ev_time)
        self.ev_days, self.ev_train = np.concatenate(ev_days), np.concatenate(ev_train)

    def _waits(self, day, minute, arrive_by):
        """Expected wait (half headway) of every pattern position around the
        requested time; NaN where the line doesn't run then."""
        # bus events are trip starts, which pass a stop somewhat later; train events are at the stop
        if arrive_by:
            windows = {False: (minute - 240, minute - 30), True: (minute - 240, minute)}
        else:
            windows = {False: (minute - 60, minute + 120), True: (minute, minute + 180)}
        lo = np.where(self.ev_train, windows[True][0], windows[False][0])
        hi = np.where(self.ev_train, windows[True][1], windows[False][1])
        active = np.zeros(len(self.ev_time), bool)
        for shift in (-1, 0, 1):  # trips of yesterday running past midnight, tomorrow's early ones
            bit = 1 << ((day.weekday() + shift) % 7)
            t = self.ev_time + 1440 * shift
            active |= (self.ev_days & bit > 0) & (t >= lo) & (t <= hi)
        counts = self.key_sources @ np.bincount(self.ev_src[active], minlength=self.n_src)
        alive = np.zeros(len(counts), bool)  # a vehicle actually passes the stop around then
        for shift in (-1, 0, 1):
            b = (day.weekday() + shift) % 7
            alive |= ((self.key_last[:, b] + 1440 * shift >= minute - SPAN_SLACK[0])
                      & (self.key_first[:, b] + 1440 * shift <= minute + SPAN_SLACK[1]))
        with np.errstate(divide="ignore"):
            headway = np.clip(180 / counts, 3, 240)  # every window spans ~3 hours
        wait = np.where((counts > 0) & alive, np.minimum(headway / 2, MAX_WAIT), np.nan)
        return wait[self.pos_key], headway[self.pos_key]

    # -- query --------------------------------------------------------------

    def _access(self, point_xy):
        reach = ACCESS_WALK_MAX * WALK_M_PER_MIN / WALK_DETOUR
        near = set(self.tree.query_ball_point(point_xy, reach))
        _, nearest = self.tree.query(point_xy, k=ACCESS_FALLBACK_STOPS)
        idx = np.array(sorted(near | set(np.atleast_1d(nearest))), dtype=np.int64)
        return idx, _walk_min(np.hypot(*(self.xy[idx] - point_xy).T))

    def _staged_time(self, src, dst, access, day, minute, arrive_by):
        """Edge times whose boarding waits follow the clock: a rough first search
        tells when each stop is reached (or, arriving by a deadline, how long
        before it), and each boarding's wait comes from the trips running at
        that hour."""
        board = self.board_edges
        stages = [self._waits(day, minute + (-h if arrive_by else h) * 60, arrive_by)[0]
                  for h in range(TIME_STAGES)]
        time = self.time.copy()
        time[board] = stages[0][self.board_pos]
        t = np.concatenate([time, access])
        ok = ~np.isnan(t)
        g = csr_matrix((t[ok] + 1e-6, (src[ok], dst[ok])), shape=(self.n_nodes, self.n_nodes))
        if arrive_by:
            reach = dijkstra(g.T.tocsr(), indices=self.DEST)
            at = reach[self.dst[board]]   # boarding happens this long before the deadline
        else:
            reach = dijkstra(g, indices=self.ORIGIN)
            at = reach[self.src[board]]   # this long after leaving
        stage = np.clip(np.nan_to_num(at, posinf=0) // 60, 0, TIME_STAGES - 1).astype(int)
        time[board] = np.choose(stage, [w[self.board_pos] for w in stages])
        return time

    def plan(self, origin, destination, models, day=None, minute=None, arrive_by=False, banned=()):
        """day/minute: requested date and minutes after midnight. Waits then come
        from the trips running around that time, and lines that don't run then
        can't be boarded. Without them, all-day frequencies are used.
        banned: pattern positions that may not be boarded."""
        o_xy, d_xy = _xy(origin)[0], _xy(destination)[0]
        o_idx, o_min = self._access(o_xy)
        d_idx, d_min = self._access(d_xy)
        direct = _walk_min(np.hypot(*(o_xy - d_xy)))
        N = self.N
        src = np.concatenate([self.src, np.full(len(o_idx), self.ORIGIN), d_idx, [self.ORIGIN]])
        dst = np.concatenate([self.dst, N + o_idx, np.full(len(d_idx), self.DEST), [self.DEST]])
        kind = np.concatenate([self.kind, np.full(len(o_idx) + len(d_idx) + 1, WALK)]).astype(np.int8)
        hub = np.concatenate([self.board_hub, np.zeros(len(o_idx) + len(d_idx) + 1, bool)])
        access = np.concatenate([o_min, d_min, [direct]])
        board = self.board_edges
        if day is None:
            time = self.time
        else:
            key = (tuple(origin), tuple(destination), day, minute, arrive_by)
            cached = self._staged  # one slot: plan_timed's repair loop reuses it
            if cached and cached[0] == key:
                time = cached[1]
            else:
                time = self._staged_time(src, dst, access, day, minute, arrive_by)
                self._staged = (key, time)
        if banned:
            time = time.copy()
            time[board[np.isin(self.board_pos, list(banned))]] = np.nan
        t = np.concatenate([time, access])
        running = ~np.isnan(t)
        src, dst, t, kind, hub = src[running], dst[running], t[running], kind[running], hub[running]

        # distance-to-destination of every node (km), for the direction penalty
        node_xy = np.vstack([self.xy[self.node_stop], o_xy, d_xy])
        to_dest = np.hypot(*(node_xy - d_xy).T) / 1000
        away = np.maximum(0, to_dest[dst] - to_dest[src])
        base = np.where(kind == WALK, np.where(t <= SOFT_WALK_LIMIT, t,
                                               SOFT_WALK_LIMIT + (t - SOFT_WALK_LIMIT) * WALK_PENALTY), t)
        base = base + DIRECTION_PENALTY * away * ((kind == RIDE) | (kind == WALK))
        is_board = kind == BOARD

        results = []
        for model in models:
            w = base.copy()
            if model == "direct":
                w[is_board] += TRANSFER_PENALTY_DIRECT
            elif model == "hub":
                w[is_board] += np.where(hub[is_board], 5, MINOR_TRANSFER_PENALTY)
            g = csr_matrix((w + 1e-6, (src, dst)), shape=(self.n_nodes, self.n_nodes))
            _, pred = dijkstra(g, indices=self.ORIGIN, return_predecessors=True)
            path, n = [self.DEST], self.DEST
            while n != self.ORIGIN and n >= 0:
                n = pred[n]
                path.append(n)
            results.append(self._to_legs(path[::-1], origin, destination) if n == self.ORIGIN else None)
        return results

    # -- timetable ----------------------------------------------------------

    def _serving(self, leg):
        """[(route index, k0, k1)]: every pattern that can carry this leg — all
        variants of the same line (buses, light rail), or any train — calling at
        the boarding stop and later at the alighting stop."""
        r_i, k0, k1 = leg.pattern
        r = self.routes[r_i]
        s0, s1 = int(r["stops"][k0]), int(r["stops"][k1])
        out = []
        for p in np.flatnonzero(self.pos_stop == s0):
            c = int(self.pos_route[p])
            other = self.routes[c]
            if other["mode"] != r["mode"] or (r["mode"] != "Train" and other["group"] != r["group"]):
                continue
            a = int(self.pos_k[p])
            later = np.flatnonzero(other["stops"][a + 1:] == s1)
            if len(later):
                out.append((c, a, a + 1 + int(later[0])))
        return out or [leg.pattern]

    def _trips(self, leg, day, factor=1.0):
        """(board, alight) minutes after midnight of `day` for every trip that can
        carry the leg around that day. factor stretches running times (live delays)."""
        board, alight = [], []
        for r_i, k0, k1 in self._serving(leg):
            r = self.routes[r_i]
            for shift in (-1, 0, 1, 2):  # yesterday's trips past midnight ... Sunday's after Shabbat
                sel = r["days"] & (1 << ((day.weekday() + shift) % 7)) > 0
                s, scale = r["deps"][sel] + 1440 * shift, r["scale"][sel] * factor
                board.append(s + r["times"][k0] * scale)
                alight.append(s + r["times"][k1] * scale)
        return np.concatenate(board), np.concatenate(alight)

    def timetable(self, legs, day, minute, arrive_by=False, factors=None):
        """Fit an itinerary to real trips: forward from a departure time (first
        trip boarding at or after we reach each stop) or backward from an arrival
        deadline (last trip arriving in time for what follows). factors: {leg
        index: live running-time factor}.

        -> [(start, end)] minutes after midnight per leg, or None if a leg has no
        trip that fits. Walks before the first ride end just as it boards, so
        the traveller leaves no earlier than needed."""
        return self._schedule(legs, day, minute, arrive_by, factors)[0]

    def plan_timed(self, origin, destination, models, day, minute, arrive_by=False):
        """plan() + timetable(), repaired: frequencies are per line, but a given
        variant may barely run (a night-only branch) or a connection may not
        exist then. A leg that has no fitting trip, or leaves the traveller idle
        over MAX_IDLE minutes, has its boarding banned and the search reruns.
        -> [(legs, span) or None] per model."""
        out, found = [], []
        for model in models:
            banned, best = set(), None
            for _ in range(REPAIR_TRIES):
                legs = self.plan(origin, destination, [model], day, minute, arrive_by, banned)[0]
                if legs is None:
                    break
                span, bad = self._schedule(legs, day, minute, arrive_by)
                if span is not None:
                    # clean (no idle stretch) first, then latest leave / earliest arrival
                    score = (bad is not None, -span[0][0] if arrive_by else span[-1][1])
                    if best is None or score < best[0]:
                        best = (score, legs, span)
                    if bad is None:
                        found.append((score, legs, span))
                if bad is None:
                    break
                # a line that isn't running at this stop isn't running at its neighbours
                # either: ban every stop of the variants involved, not just this one
                for c, _, _ in self._serving(legs[bad]):
                    banned.update(range(self.route_start[c], self.route_start[c + 1]))
            out.append(best)
        # "fastest" is judged on the real timetable, as the traveller sees it: the
        # shortest door-to-door trip of every clean itinerary any model found
        # (ties: the earlier arrival / later departure)
        if "fastest" in models and found:
            out[models.index("fastest")] = min(found, key=lambda b: (b[2][-1][1] - b[2][0][0], b[0][1]))
        if any(b and not b[0][0] for b in out):  # some model found a clean itinerary: drop the rest
            out = [b if b and not b[0][0] else None for b in out]
        return [b and b[1:] for b in out]

    def _schedule(self, legs, day, minute, arrive_by=False, factors=None, tighten=True):
        """-> (span or None, index of the leg to avoid or None).

        tighten: the first pass catches the first trip at every stop (or, by a
        deadline, the last one), which can mean boarding early only to wait at
        a transfer. A mirror pass from the result's arrival (departure) keeps
        it but boards every leg as late (alights as early) as possible."""
        span, bad = self._fit(legs, day, minute, arrive_by, factors)
        if span is not None and tighten and any(l.pattern for l in legs):
            if arrive_by:
                tight, _ = self._fit(legs, day, span[0][0], False, factors)
                if tight is not None and tight[-1][1] <= span[-1][1] + 1e-6:
                    span = tight
            else:
                tight, _ = self._fit(legs, day, span[-1][1], True, factors)
                if tight is not None and tight[0][0] >= span[0][0] - 1e-6:
                    span = tight
        return span, bad

    def _fit(self, legs, day, minute, arrive_by=False, factors=None):
        """One timetable pass. -> (span or None, index of the leg to avoid or None)."""
        factors = factors or {}
        span = [None] * len(legs)
        rides = [i for i, l in enumerate(legs) if l.pattern]
        order = range(len(legs) - 1, -1, -1) if arrive_by else range(len(legs))
        cur, worst = minute, (MAX_IDLE, None)
        for i in order:
            leg = legs[i]
            if not leg.pattern:
                span[i] = (cur - leg.minutes, cur) if arrive_by else (cur, cur + leg.minutes)
                cur = span[i][0] if arrive_by else span[i][1]
                continue
            board, alight = self._trips(leg, day, factors.get(i, 1.0))
            if arrive_by:
                ok = alight <= cur + 1e-6
                if not ok.any():
                    return None, i
                j = np.flatnonzero(ok)[np.argmax(board[ok])]   # latest boarding that still makes it
                idle = cur - alight[j]
            else:
                ok = board >= cur - 1e-6
                if not ok.any():
                    return None, i
                j = np.flatnonzero(ok)[np.argmin(alight[ok])]  # earliest arrival
                idle = board[j] - cur
            if idle > worst[0]:
                worst = (idle, i)
            span[i] = (float(board[j]), float(alight[j]))
            cur = span[i][0] if arrive_by else span[i][1]
        if rides:  # walk legs hug the rides: leave just in time, arrive straight after
            first, last = rides[0], rides[-1]
            for i in range(first - 1, -1, -1):
                span[i] = (span[i + 1][0] - legs[i].minutes, span[i + 1][0])
            for i in range(last + 1, len(legs)):
                span[i] = (span[i - 1][1], span[i - 1][1] + legs[i].minutes)
            for i in range(first + 1, last):  # transfer walks start on alighting
                if not legs[i].pattern:
                    span[i] = (span[i - 1][1], span[i - 1][1] + legs[i].minutes)
        return span, worst[1]

    def _node(self, n):
        N, T = self.N, self.T
        if n == self.ORIGIN:
            return "O", None
        if n == self.DEST:
            return "D", None
        if n < N:
            return "S", n
        if n < 2 * N:
            return "W", n - N
        if n < 2 * N + T:
            return "P", n - 2 * N
        return "B", n - 2 * N - T

    def _to_legs(self, path, origin, destination):
        legs, cur = [], None
        for u, v in zip(path, path[1:]):
            (ku, iu), (kv, iv) = self._node(u), self._node(v)
            if kv == "B":                                   # board
                r = self.routes[self.pos_route[iv]]
                k = int(self.pos_k[iv])
                cur = dict(route=int(self.pos_route[iv]), k0=k, k1=k,
                           headway=float(r["headway"][k]), wait=min(float(r["headway"][k]) / 2, MAX_WAIT))
                legs.append(cur)
            elif kv == "P":                                 # ride
                cur["k1"] = int(self.pos_k[iv])
            elif ku in ("O", "S") and kv in ("W", "D"):     # walk
                a = origin if ku == "O" else tuple(self.ll[iu])
                b = destination if kv == "D" else tuple(self.ll[iv])
                legs.append(dict(walk=(a, b), stops=(iu if ku == "S" else None, iv if kv == "W" else None)))
                cur = None
        return [self._leg(l) for l in legs]

    def _leg(self, l):
        if "walk" in l:
            a, b = l["walk"]
            minutes = _walk_min(np.hypot(*(_xy(a)[0] - _xy(b)[0])))
            names = [self.d["stop_name"][s] if s is not None else "" for s in l["stops"]]
            return Leg("Walk", "Walk", list(l["stops"]), minutes, coords=[a, b], names=names)
        r = self.routes[l["route"]]
        stops = [int(s) for s in r["stops"][l["k0"]:l["k1"] + 1]]
        ride = float(r["times"][l["k1"]] - r["times"][l["k0"]])
        leg = Leg(str(l["route"]), r["mode"], stops, l["wait"] + ride,
                  coords=[tuple(self.ll[s]) for s in stops], label=self._label(r),
                  names=[self.stop_label(s) for s in stops], headway=l["headway"],
                  board_hub=MAJOR_HUB if self.hub[stops[0]] else MINOR_STOP,
                  board_lines=int(self.patterns[stops[0]]), wait=l["wait"], ride=ride,
                  agency=r["agency"], headsign=self.stop_label(int(r["stops"][-1])),
                  pattern=(l["route"], l["k0"], l["k1"]))
        leg.geometry = self._shape_slice(r, stops)
        return leg

    def variants(self, route_idx):
        """All patterns of the same line + direction (shared makat-direction group)."""
        if not hasattr(self, "_groups"):
            self._groups = {}
            for r in self.routes:
                self._groups.setdefault(r["group"], []).append(r)
        return self._groups[self.routes[route_idx]["group"]]

    def _label(self, r):
        if r["mode"] == "Train":
            return "רכבת ישראל"
        return r["short"] or r["long"].split("<->")[0]

    def stop_label(self, s):
        name, city = self.d["stop_name"][s], self.d["stop_city"][s]
        return f"{name} ({city})" if city and city not in name else name

    def _shape_slice(self, r, stops):
        pts = self.d["shapes"].get(r["shape"])
        if pts is None or len(pts) < 2:
            return None
        sx = _xy(pts)
        a = int(np.argmin(np.hypot(*(sx - self.xy[stops[0]]).T)))
        b = a + int(np.argmin(np.hypot(*(sx[a:] - self.xy[stops[-1]]).T)))
        return [tuple(map(float, p)) for p in pts[a:b + 1]] if b > a else None

    def route(self, title, legs, span=None):
        """span: timetable() result; sets clock times, real waits and ride times."""
        rides = [l for l in legs if l.mode != "Walk"]
        if span:
            for i, (leg, (a, b)) in enumerate(zip(legs, span)):
                leg.start, leg.end = a, b
                if leg.pattern:
                    leg.wait = a - (span[i - 1][1] if i else a)
                    leg.ride = b - a
                    leg.minutes = leg.wait + leg.ride
        r = Route(title, legs, total_minutes=round(span[-1][1] - span[0][0]) if span else
                  round(sum(l.minutes for l in legs)),
                  walk_minutes=round(sum(l.minutes for l in legs if l.mode == "Walk")),
                  transfers=max(0, len(rides) - 1))
        if span:
            r.depart, r.arrive, r.timetabled = span[0][0], span[-1][1], True
        r.stop_points = [(self.stop_label(s), *self.ll[s], MAJOR_HUB if self.hub[s] else MINOR_STOP)
                         for l in rides for s in (l.stops[0], l.stops[-1])]
        return r


def load():
    """-> GtfsNetwork, building/downloading as needed; None if the feed is unavailable."""
    zip_path = ensure_feed()
    if zip_path is None:
        return None
    data = None
    if os.path.exists(NET_FILE):
        with open(NET_FILE, "rb") as f:
            data = pickle.load(f)
        if (data.get("feed_mtime") != os.path.getmtime(zip_path) or data.get("version") != BUILD_VERSION
                or dt.date.today().isoformat() not in data.get("week", ())):  # weekday masks cover one week
            data = None
    if data is None:
        data = build(zip_path)
    return GtfsNetwork(data)
