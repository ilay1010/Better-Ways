"""Live bus delays from the Ministry of Transport SIRI feed.

The MOT's own SIRI endpoint needs a registered key and IP allow-listing, so
we read the same feed through the Public Knowledge Workshop's Open Bus Stride
API, which republishes every vehicle position within a minute or two.

For each ride leg we take the line's buses that are on the road right now
(SIRI LineRef == GTFS route_id) and compare their progress with the GTFS
schedule: a bus that is D metres into its journey should, by timetable, be
`interp(D, dists, times)` minutes out; it actually left `now - scheduled_start`
minutes ago. The ratio is how much slower (or faster) than scheduled the line
is running, and it scales the leg's ride time. The 25th-75th percentile of
those ratios gives the arrival range.

Israel Railways is not in SIRI: train legs stay on the timetable.
"""
import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

from cached_data import PEAK_BUS_FACTOR, PEAK_WINDOWS

STRIDE = "https://open-bus-stride-api.hasadna.org.il"
WINDOW_MIN = 15          # positions recorded in the last N minutes count as "on the road"
CACHE_TTL = 60           # seconds; Stride refreshes about once a minute
TIMEOUT = 8              # seconds per request: beyond this the feed counts as unavailable
MIN_SCHED_MINUTES = 10   # ignore buses barely out of the terminal (start lateness dominates)
FACTOR_RANGE = (0.7, 3.0)

_lock = threading.Lock()
_cache = {}


class FeedUnavailable(Exception):
    pass


def _time(s):
    return dt.datetime.fromisoformat(s)


def _vehicles(line_ref):
    """Latest position of every ride of this line seen in the last WINDOW_MIN minutes."""
    with _lock:
        hit = _cache.get(line_ref)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    now = dt.datetime.now(dt.timezone.utc)
    try:
        r = requests.get(f"{STRIDE}/siri_vehicle_locations/list", timeout=TIMEOUT, params={
            "siri_routes__line_ref": line_ref, "limit": 500, "order_by": "recorded_at_time desc",
            "recorded_at_time_from": (now - dt.timedelta(minutes=WINDOW_MIN)).isoformat(),
            "recorded_at_time_to": now.isoformat()})
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        raise FeedUnavailable(str(e)) from e
    latest = {}
    for v in rows:  # newest first
        latest.setdefault(v.get("siri_ride__id"), v)
    vehicles = list(latest.values())
    with _lock:
        _cache[line_ref] = (time.time(), vehicles)
    return vehicles


def line_factors(patterns):
    """patterns: every GTFS variant of one line+direction (each has its own
    route_id = SIRI LineRef). -> actual/scheduled progress ratios of the
    line's buses on the road now, each measured against its own variant."""
    factors = []
    with ThreadPoolExecutor(4) as pool:
        fleets = list(pool.map(lambda p: _vehicles(p["route_id"]), patterns))
    for pattern, vehicles in zip(patterns, fleets):
        if not pattern["dists"][-1]:
            continue  # no distance-along-route in the feed: can't place the bus on the timetable
        for v in vehicles:
            d, start, seen = (v.get("distance_from_journey_start"), v.get("siri_ride__scheduled_start_time"),
                              v.get("recorded_at_time"))
            if not d or not start or not seen:
                continue
            sched = float(np.interp(d, pattern["dists"], pattern["times"]))
            actual = (_time(seen) - _time(start)).total_seconds() / 60
            if sched >= MIN_SCHED_MINUTES and actual > 0:
                factors.append(min(max(actual / sched, FACTOR_RANGE[0]), FACTOR_RANGE[1]))
    return np.array(factors)


def _simulated_factor(leg, now):
    """Clearly-labelled stand-in when the feed is down: typical peak/off-peak slowdown."""
    if leg.mode != "Bus":
        return 1.0
    peak = any(a <= now.hour < b for a, b in PEAK_WINDOWS)
    return PEAK_BUS_FACTOR if peak else 1.08


def _retime(route, net, day, factors):
    """Arrival shift when the itinerary is re-run on the timetable with live
    running-time factors, from the same departure. A late bus boards and
    arrives later, and may miss the next connection (then the next trip is
    taken). None if no trip fits any more."""
    span = net.timetable(route.legs, day, route.depart, factors=factors)
    return None if span is None else span[-1][1] - route.arrive


def evaluate(route, net, day):
    """-> dict(status, delay, low, high, legs, vehicles)

    status: "live" (every bus leg has GPS data), "partial", "none" (no buses of
    these lines on the road / trains only), or "simulated" (feed unreachable).
    delay/low/high: arrival shift vs. the timetable (median, 25th, 75th
    percentile running times), connections re-checked.
    legs: {leg index: (ride delay, vehicles)} for legs with live data.
    """
    rides = [(i, l) for i, l in enumerate(route.legs) if l.mode in ("Bus", "Light Rail")]
    if not rides:
        return dict(status="none", delay=0, low=0, high=0, legs={}, vehicles=0)
    try:
        with ThreadPoolExecutor(4) as pool:
            factors = list(pool.map(lambda x: line_factors(net.variants(x[1].pattern[0])), rides))
    except FeedUnavailable:
        now = dt.datetime.now()
        f = {i: _simulated_factor(l, now) for i, l in rides}
        shifts = [_retime(route, net, day, {i: 1 + (v - 1) * k for i, v in f.items()}) for k in (1, 0.5, 1.5)]
        delay, low, high = (round(s) if s is not None else 0 for s in shifts)
        return dict(status="simulated", delay=delay, low=low, high=high, legs={}, vehicles=0)
    legs, quart, n = {}, {}, 0
    for (i, leg), f in zip(rides, factors):
        if not len(f):
            continue
        quart[i] = np.percentile(f, [50, 25, 75])
        legs[i] = (leg.ride * (quart[i][0] - 1), len(f))
        n += len(f)
    if not legs:
        return dict(status="none", delay=0, low=0, high=0, legs={}, vehicles=0)
    shifts = [_retime(route, net, day, {i: q[k] for i, q in quart.items()}) for k in range(3)]
    delay, low, high = (round(s) if s is not None else 0 for s in shifts)
    status = "live" if len(legs) == len(rides) else "partial"
    return dict(status=status, delay=delay, low=min(low, delay), high=max(high, delay), legs=legs, vehicles=n)
