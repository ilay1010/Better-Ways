import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import folium
import streamlit as st
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

import gtfs
import rail
import realtime
from cached_data import MAJOR_HUB, MINOR_STOP
from geo import StopIndex, hub_stops, nearby_stops, reverse_geocode, road_path, search_places
from routing import SOFT_WALK_LIMIT, live_network, plan_routes

st.set_page_config(page_title="BetterWays", page_icon="🚌", layout="wide")

MODELS = [("fastest", "המהירה ביותר"), ("direct", "הכי מעט החלפות"), ("hub", "דרך מרכזי תחבורה")]
MODEL_ORDER = {name: i for i, (_, name) in enumerate(MODELS)}
LETTERS = "אבג"
COLORS = ["#7c3aed", "#16a34a", "#dc2626"]  # bus lines per option; blue/cyan/orange are reserved for modes
WEIGHTS = [8, 6, 4]                          # wider underneath so overlapping options stay visible
WALK_COLOR, LRT_COLOR, RAIL_BED, RAIL_DASH = "#2563eb", "#06b6d4", "#111827", "#f97316"
MODE_ICON = {"Bus": "🚌", "Train": "🚆", "Light Rail": "🚊", "Walk": "🚶‍♂️"}

DEPART, ARRIVE = "שעת יציאה", "שעת הגעה"
WALK_ONLY_MAX = 60      # longer walk-only results mean there's no transit at that time
LIVE_WINDOW = (-15, 90)  # live GPS delays apply to trips leaving within this many minutes of now
HEB_DAYS = ["יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳", "שבת", "יום א׳"]  # by date.weekday()

DEFAULT_ORIGIN =(31.7848, 35.2124, "שוק מחנה יהודה", "[שוק מחנה יהודה] ירושלים, אגריפס")
DEFAULT_DEST = (32.0780, 34.7744, "כיכר דיזנגוף", "[כיכר דיזנגוף] תל אביב, דיזנגוף")

st.markdown("""
<style>
.stApp, section[data-testid="stSidebar"] {direction:rtl;text-align:right;}
.card {border:1px solid #e5e7eb;border-radius:14px;padding:18px 20px;
       background:var(--secondary-background-color);}
.card h4 {margin:0 0 10px 0;}
.bus {display:inline-block;padding:2px 9px;margin:2px 0 2px 4px;border-radius:6px;
      color:#fff;font-weight:600;font-size:0.9rem;}
.metric {font-size:1.6rem;font-weight:700;margin:8px 0 0 0;}
.sub {color:#6b7280;font-size:0.8rem;}
.grid {display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px;}
.live {display:inline-flex;align-items:center;gap:7px;margin-top:12px;padding:4px 11px;border-radius:999px;
       background:#dcfce7;color:#166534;font-weight:600;font-size:0.85rem;}
.live.sim {background:#fef9c3;color:#854d0e;}
.live.none {background:#f3f4f6;color:#4b5563;}
.dot {width:9px;height:9px;border-radius:50%;background:#22c55e;animation:pulse 1.6s infinite;}
@keyframes pulse {0% {box-shadow:0 0 0 0 rgba(34,197,94,.7);}
                  70% {box-shadow:0 0 0 9px rgba(34,197,94,0);} 100% {box-shadow:0 0 0 0 rgba(34,197,94,0);}}
</style>""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def stop_index():
    net = load_gtfs()
    return None if net is None else StopIndex(net.d["stop_name"], net.d["stop_city"], net.d["stop_ll"])


def cached_search(term):
    # not st.cache_data: a Photon outage must not be remembered (geo caches successful answers)
    return search_places(term, stop_index=stop_index())


@st.cache_data(show_spinner=False, ttl=86400)
def cached_reverse(lat, lon):
    return reverse_geocode(lat, lon)


@st.cache_resource(show_spinner="טוען את רשת התחבורה הארצית (משרד התחבורה, GTFS)...")
def load_gtfs():
    try:
        return gtfs.load()
    except Exception:
        return None


def place_input(label, default, key):
    """Searchbox whose value lives in session_state[f"{key}_place"], so the map
    can set it too. Values are (lat, lon, short_name, label)."""
    current = st.session_state.setdefault(f"{key}_place", default)
    value = st_searchbox(cached_search, label=label, placeholder=current[3], key=key,
                         default=current, debounce=300) or current
    st.session_state[f"{key}_place"] = value
    st.caption(f"📍 {value[3]}")
    return value


def set_place(key, place):
    st.session_state[f"{key}_place"] = place
    # dropping the searchbox state re-creates it with the new place as default/placeholder
    st.session_state.pop(key, None)


def swap_places():
    origin, destination = st.session_state["origin_place"], st.session_state["destination_place"]
    set_place("origin", destination)
    set_place("destination", origin)


def now_time():
    return dt.datetime.now().time().replace(second=0, microsecond=0)


def reset_now(key):
    st.session_state[key] = now_time()


with st.sidebar:
    st.title("🚌 BetterWays")
    st.caption("חפשו: [שם המבנה] עיר, רחוב מספר")
    origin = place_input("מוצא", DEFAULT_ORIGIN, "origin")
    st.button("⇅ החלף מוצא ויעד", on_click=swap_places, width="stretch")
    destination = place_input("יעד", DEFAULT_DEST, "destination")
    search_mode = st.radio("סוג חיפוש", [DEPART, ARRIVE], horizontal=True)
    arrive_by = search_mode == ARRIVE
    time_key = "arr_time" if arrive_by else "dep_time"
    st.session_state.setdefault(time_key, now_time())
    c_time, c_now = st.columns([3, 2], vertical_alignment="bottom")
    with c_time:
        when = st.time_input(search_mode, key=time_key, step=60)
    with c_now:
        st.button("🔄 עכשיו", on_click=reset_now, args=(time_key,), width="stretch")
    live = st.toggle("🟢 הפעל נתוני זמן אמת (GTFS-RT)", value=False,
                     help="מיקומי אוטובוסים בזמן אמת (SIRI, משרד התחבורה) מעדכנים את זמני הנסיעה ובודקים "
                          "מחדש את ההחלפות. רלוונטי לנסיעות שיוצאות בשעה וחצי הקרובות; "
                          "רכבת ישראל אינה מדווחת ב-SIRI.")
    lucky = st.toggle("🎲 I'm feeling lucky", value=False,
                      help="מציג רק את המסלול המהיר ביותר ואת המסלול עם הכי מעט החלפות.")

net = load_gtfs()
live = live and net is not None  # live delays are matched against GTFS schedules
models = [m for m in MODELS if not (lucky and m[0] == "hub")]
day = dt.date.today()
minute = when.hour * 60 + when.minute


def plan(o, d):
    """-> [(model name, Route)] in `models` order, timed on the real timetable."""
    if net is not None:
        results = net.plan_timed(o, d, [m for m, _ in models], day, minute, arrive_by)
        return [(name, net.route(name, *res)) for (_, name), res in zip(models, results) if res]
    # fallback without the GTFS feed: static backbone + live OSM stops, frequency-based times
    with ThreadPoolExecutor(3) as pool:
        f_h, f_o, f_d = pool.submit(hub_stops), pool.submit(nearby_stops, *o), pool.submit(nearby_stops, *d)
        live = live_network(o, d, f_o.result(), f_d.result(), f_h.result())
    routes = dict(zip([m for m, _ in MODELS], plan_routes(o, d, when.hour, net=live)))
    out = [(name, routes[m]) for m, name in models if routes[m]]
    for _, r in out:
        r.depart = minute - r.total_minutes if arrive_by else minute
        r.arrive = r.depart + r.total_minutes
    return out


with st.spinner("מחשב מסלולים לפי לוח הזמנים..."):
    options = plan(origin[:2], destination[:2])
# a long walk-only "route" means no transit runs then (e.g. Shabbat)
options = [(n, r) for n, r in options if any(l.mode != "Walk" for l in r.legs) or r.walk_minutes <= WALK_ONLY_MAX]

with st.sidebar:
    if net is not None:
        st.caption(f"📅 קווים ותדירויות: משרד התחבורה (GTFS), {net.d['date']}")
    else:
        st.caption("⚠️ קובץ משרד התחבורה לא זמין; מוצגת רשת בסיס + תחנות OpenStreetMap (זמנים משוערים).")
    st.caption(f"עדיפות להליכה של עד {SOFT_WALK_LIMIT} דק׳ · ● {MAJOR_HUB} ○ {MINOR_STOP}")


def signature(route):
    rides = tuple((l.mode, l.line, l.stops[0], l.stops[-1]) for l in route.legs if l.mode != "Walk")
    return rides or ("walk",)


def dominates(a, b):
    """a leaves no earlier, arrives no later and changes no more often than b:
    b is never worth taking."""
    return a.depart >= b.depart - 0.5 and a.arrive <= b.arrive + 0.5 and a.transfers <= b.transfers


# identical itineraries from different models collapse into one card, and so
# does one that another option beats on both departure and arrival
unique = {}
for name, route in options:
    unique.setdefault(signature(route), (route, []))[1].append(name)
kept = []
for route, names in unique.values():
    better = next((k for k in kept if dominates(k[0], route)), None)
    if better:
        better[1].extend(names)
        continue
    for k in [k for k in kept if dominates(route, k[0])]:
        kept.remove(k)
        names = k[1] + names
    kept.append((route, names))
kept.sort(key=lambda k: min(MODEL_ORDER[n] for n in k[1]))
cards = [(f"אפשרות {LETTERS[i]}׳ · {' / '.join(sorted(names, key=MODEL_ORDER.get))}", route)
         for i, (route, names) in enumerate(kept)]

def clock(m):
    """Minutes after midnight of the search day -> "HH:MM", marking other days."""
    t = dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=round(m))
    diff = (t.date() - day).days
    suffix = "" if not diff else " (מחר)" if diff == 1 else " (אתמול)" if diff == -1 else f" ({HEB_DAYS[t.weekday()]})"
    return t.strftime("%H:%M") + suffix


def live_relevant(route):
    """Live GPS delays describe vehicles on the road now: only trips leaving soon."""
    now = dt.datetime.now()
    return LIVE_WINDOW[0] <= route.depart - (now.hour * 60 + now.minute) <= LIVE_WINDOW[1]


lives = [None] * len(cards)
if live:
    near = [i for i, (_, r) in enumerate(cards) if r.timetabled and live_relevant(r)]
    with st.spinner("מושך מיקומי אוטובוסים בזמן אמת..."), ThreadPoolExecutor(3) as pool:
        for i, info in zip(near, pool.map(lambda i: realtime.evaluate(cards[i][1], net, day), near)):
            lives[i] = info
    lives = [info or dict(status="far") for info in lives]


def fmt_delay(m):
    return "בזמן" if m == 0 else f"+{m} דק׳" if m > 0 else f"{m} דק׳"


def live_badge(info):
    if info["status"] in ("live", "partial"):
        d = info["delay"]
        desc = f"עיכוב צפוי: {d} דק׳" if d > 0 else f"מקדים ב-{-d} דק׳" if d < 0 else "ללא עיכוב"
        text = "מדויק לפי GPS" if info["status"] == "live" and d == 0 else f"בזמן אמת ({desc})"
        part = " · חלק מהקווים" if info["status"] == "partial" else ""
        return f'<span class="live"><span class="dot"></span>{text}{part} · {info["vehicles"]} רכבים</span>'
    if info["status"] == "simulated":
        return (f'<span class="live sim">🟡 סימולציה – שירות זמן אמת לא זמין '
                f'(עיכוב משוער: {info["delay"]} דק׳)</span>')
    if info["status"] == "far":
        return '<span class="live none">⚪ זמן אמת – רק לנסיעות שיוצאות בשעה וחצי הקרובות</span>'
    return '<span class="live none">⚪ אין רכבים פעילים בקווים אלה כרגע – לפי לוח הזמנים</span>'


def delay_of(info):
    return info.get("delay", 0) if info else 0


def mode_text(leg):
    towards = f" לכיוון {leg.headsign}" if leg.headsign else ""
    if leg.mode == "Train":
        return f"🚆 רכבת ישראל{towards}"
    if leg.mode == "Light Rail":
        return f"🚊 רכבת קלה קו {leg.label}{towards}"
    agency = f" ({leg.agency})" if leg.agency else ""
    return f"🚌 אוטובוס קו {leg.label}{agency}{towards}"


def steps(route, info=None):
    timed = route.timetabled
    approx = "" if timed else "~"
    out = [f"🏠 יציאה מ{origin[2]} · {approx}{clock(route.depart)}"]
    for i, leg in enumerate(route.legs):
        mins = round(leg.minutes)
        if leg.mode == "Walk":
            target = "היעד" if i == len(route.legs) - 1 else route.legs[i + 1].names[0]
            out.append(f"🚶‍♂️ הליכה {mins} דק׳ אל {target}")
            continue
        ride = round(leg.ride or leg.minutes)
        if timed:
            wait = f" · המתנה {round(leg.wait)} דק׳" if leg.wait >= 1 else ""
        else:
            wait = f" · המתנה ~{round(leg.wait)} דק׳" if leg.wait >= 1 else ""
        out.append(f"🚏 תחנה: {leg.names[0]}" + (f" · יציאה {clock(leg.start)}" if timed else ""))
        n = len(leg.stops) - 1
        n_stops = "תחנה אחת" if n == 1 else f"{n} תחנות"
        lv = info.get("legs", {}).get(i) if info else None
        gps = f" · 🟢 {fmt_delay(round(lv[0]))} לפי {lv[1]} רכבים בדרך" if lv else ""
        est = "~" if leg.estimated else ""
        out.append(f"{mode_text(leg)} · {n_stops} · {est}{ride} דק׳ נסיעה{wait}{gps}")
        out.append(f"⬇️ ירידה ב{leg.names[-1]}" + (f" · {clock(leg.end)}" if timed else ""))
    delay = delay_of(info)
    if info and info["status"] in ("live", "partial", "simulated"):
        out.append(f"🏁 הגעה ליעד: {destination[2]} · ~{clock(route.arrive + delay)} "
                   f"(טווח {clock(route.arrive + info['low'])}–{clock(route.arrive + info['high'])}, "
                   f"לפי לוח: {clock(route.arrive)})")
    else:
        out.append(f"🏁 הגעה ליעד: {destination[2]} · {approx}{clock(route.arrive)}")
    if arrive_by and route.arrive + delay > minute + 0.5:
        out.append(f"⚠️ לפי זמן אמת ההגעה צפויה אחרי {clock(minute)} – כדאי לצאת מוקדם יותר")
    return out


if not cards:
    when_text = f"{'להגעה עד' if arrive_by else 'ליציאה ב-'} {clock(minute)}, {HEB_DAYS[day.weekday()]}"
    st.warning(f"🚫 לא נמצאה תחבורה ציבורית {when_text}. ייתכן שאין שירות בשעה זו "
               f"(למשל בשבת או בלילה) – נסו שעה אחרת.")
cols = st.columns(len(cards) or 1)
for i, (col, (title, route), info) in enumerate(zip(cols, cards, lives)):
    with col:
        chips = "".join(f'<span class="bus" style="background:{COLORS[i]}">{MODE_ICON[l.mode]} {l.label}</span>'
                        for l in route.legs if l.mode != "Walk") or "🚶‍♂️ הליכה בלבד"
        approx = "~" if any(l.estimated for l in route.legs) or not route.timetabled else ""
        delay = delay_of(info)
        scheduled = f'<span class="sub"> (לפי לוח: {route.total_minutes})</span>' if delay else ""
        span_text = f"🕐 יציאה {approx}{clock(route.depart)} · הגעה {approx}{clock(route.arrive + delay)}"
        st.markdown(f"""
<div class="card" style="border-top:5px solid {COLORS[i]}">
  <h4>{title}</h4>
  {chips}
  <div class="metric">{approx}{route.total_minutes + delay} דק׳{scheduled}</div>
  <div class="sub" style="font-size:0.95rem">{span_text}</div>
  <div class="grid">
    <div><b>{route.transit_minutes + delay}</b> דק׳<div class="sub">בתחבורה</div></div>
    <div><b>{route.walk_minutes}</b> דק׳<div class="sub">הליכה</div></div>
    <div><b>{route.transfers}</b><div class="sub">החלפות</div></div>
  </div>
  {live_badge(info) if info else ""}
</div>""", unsafe_allow_html=True)
        with st.expander("הצג פירוט מסלול"):
            st.markdown("\n".join(f"{n}. {s}" for n, s in enumerate(steps(route, info), 1)))
if live and any(info["status"] == "simulated" for info in lives):
    st.caption("🟡 שירות זמן האמת לא הגיב; העיכובים המוצגים הם סימולציה לפי שעות עומס, לא נתוני GPS.")
elif live and any(info["status"] != "far" for info in lives):
    st.caption(f"🟢 זמן אמת: מיקומי GPS מ-SIRI של משרד התחבורה (דרך Open Bus Stride), עודכן "
               f"{dt.datetime.now().strftime('%H:%M')}. העיכובים מוחלים על לוח הזמנים וההחלפות נבדקות מחדש.")

st.write("")


@st.cache_resource(show_spinner="טוען את רשת המסילות (OpenStreetMap)...")
def load_rail():
    # load once before the thread pool so workers don't race to build it
    for mode in rail.KINDS:
        rail._graph(mode)
    return True


def thin(coords, n=25):
    """OSRM takes a limited number of waypoints: keep endpoints + evenly spaced stops."""
    if len(coords) <= n:
        return tuple(coords)
    step = (len(coords) - 1) / (n - 1)
    return tuple(coords[round(k * step)] for k in range(n))


def leg_path(leg):
    if leg.geometry:  # exact GTFS shape
        return leg.geometry, True
    coords = tuple(map(tuple, leg.coords))
    if leg.mode == "Walk":
        return road_path(coords, "foot")
    if leg.mode in rail.KINDS:
        path, ok = rail.rail_path(coords, leg.mode)
        if ok or leg.mode == "Train":  # light rail runs on streets where OSM has no track
            return path, ok
    return road_path(thin(coords), "car")


try:
    load_rail()
except Exception:
    st.caption("⚠️ רשת המסילות לא זמינה; קטעי רכבת ללא צורה מוצגים כקווים ישרים.")

legs = [(i, leg) for i, (_, r) in enumerate(cards) for leg in r.legs]
with st.spinner("טוען גאומטריה של כבישים ומסילות..."), ThreadPoolExecutor(4) as pool:
    paths = list(pool.map(lambda x: leg_path(x[1]), legs))

m = folium.Map(location=[31.5, 34.9], zoom_start=8, tiles="OpenStreetMap")
for (i, leg), (path, _) in sorted(zip(legs, paths), key=lambda x: x[0][0]):
    w, mins = WEIGHTS[i], round(leg.minutes)
    tip = f"{cards[i][0]} · " + (f"🚶‍♂️ הליכה {mins} דק׳" if leg.mode == "Walk"
                                  else f"{mode_text(leg)} · {mins} דק׳")
    line = lambda **kw: folium.PolyLine(path, tooltip=tip, **kw).add_to(m)
    if leg.mode == "Walk":                       # thin blue dots
        line(color=WALK_COLOR, weight=3, opacity=0.9, dash_array="1,8")
    elif leg.mode == "Train":                    # black bed + bold orange dashes: rail track
        line(color=RAIL_BED, weight=w + 5, opacity=0.9)
        line(color=RAIL_DASH, weight=w, opacity=1, dash_array="12,10")
    elif leg.mode == "Light Rail":               # cyan double line
        line(color=LRT_COLOR, weight=w + 5, opacity=0.95)
        line(color="#ffffff", weight=max(w - 2, 2), opacity=1)
    else:                                        # bus: solid, option colour
        line(color=COLORS[i], weight=w, opacity=0.9)

for p, icon, c, title in ((origin, "play", "green", "מוצא"), (destination, "flag", "red", "יעד")):
    folium.Marker(p[:2], tooltip=f"{title}: {p[3]}", icon=folium.Icon(color=c, icon=icon)).add_to(m)

clicked = st.session_state.get("clicked")  # (lat, lon, short_name, label) from the last map click
if clicked:
    folium.Marker(clicked[:2], icon=folium.Icon(color="purple", icon="info-sign"),
                  popup=folium.Popup(f'<div dir="rtl">📍 {clicked[3]}</div>', max_width=260, show=True),
                  ).add_to(m)

all_pts = [pt for path, _ in paths for pt in path] + [origin[:2], destination[:2]]
m.fit_bounds([[min(p[0] for p in all_pts), min(p[1] for p in all_pts)],
              [max(p[0] for p in all_pts), max(p[1] for p in all_pts)]])
if not all(ok for _, ok in paths):
    st.caption("⚠️ חלק מהקטעים מוצגים כקווים ישרים (שירות הגאומטריה לא זמין או שהמסילה לא אותרה).")

for name, lat, lon, kind in {p for _, r in cards for p in r.stop_points}:
    hub = kind == MAJOR_HUB
    folium.CircleMarker((lat, lon), radius=7 if hub else 5, color="#111827",
                        fill=True, fill_color="#111827" if hub else "#ffffff", fill_opacity=1,
                        tooltip=f"🚏 {name} ({kind})").add_to(m)

if clicked:
    with st.container(border=True):
        st.markdown(f"**📍 מיקום שנבחר במפה:** {clicked[3]}")
        b1, b2, b3 = st.columns(3)
        b1.button("🟢 קבע כמוצא", on_click=set_place, args=("origin", clicked), width="stretch")
        b2.button("🔴 קבע כיעד", on_click=set_place, args=("destination", clicked), width="stretch")
        b3.button("✖ נקה", on_click=st.session_state.pop, args=("clicked", None), width="stretch")
else:
    st.caption("💡 לחצו על המפה כדי לבחור מוצא או יעד")

out = st_folium(m, height=520, use_container_width=True, key="map", returned_objects=["last_clicked"])
st.caption("קו מלא בצבע האפשרות – אוטובוס · מקווקו כתום על שחור – רכבת ישראל · "
           "קו כפול תכלת – רכבת קלה · נקודות כחולות – הליכה")

click = (out or {}).get("last_clicked")
if click:
    lat, lon = round(click["lat"], 6), round(click["lng"], 6)
    # st_folium keeps returning the last click on every rerun: only act on a new one,
    # then rerun so the marker and card render for it
    if st.session_state.get("handled_click") != (lat, lon):
        st.session_state["handled_click"] = (lat, lon)
        with st.spinner("מאתר כתובת..."):
            label, name = cached_reverse(lat, lon)
        st.session_state["clicked"] = (lat, lon, name, label)
        st.rerun()
