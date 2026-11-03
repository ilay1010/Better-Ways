# BetterWays: Transit Resilience Router

A door-to-door public transport planner for Israel, built with Streamlit. It routes on the
Ministry of Transport's official GTFS timetable (every bus, train and light-rail line in the
country), fits each itinerary to real scheduled trips, and can adjust them with live bus GPS
positions. The interface is in Hebrew (right-to-left).

## Features

- **Nationwide timetable routing.** It covers every line in the official MOT GTFS feed. Each
  itinerary is fitted to real trips:
  - **Leave at:** the first trip after you reach each stop.
  - **Arrive by:** the last trip that still gets you there in time.
  - Lines that aren't running at that hour (nights, Friday afternoon, Shabbat) are excluded.
- **Three route options:**
  - **Fastest:** shortest door-to-door time.
  - **Fewest transfers.**
  - **Via transit hubs:** prefers major stations for transfers.

  Identical options, and options beaten on both departure and arrival time, are merged into
  one card.
- **Departure or arrival time**, defaulting to now, with a one-click "now" reset.
- **Real-time mode (optional).** Live bus GPS positions from the MOT SIRI feed come through the
  [Open Bus Stride API](https://open-bus-stride-api.hasadna.org.il). They update ride times
  and re-check connections, so a late bus can make you miss a transfer. If the feed is
  unreachable, the app shows a clearly labelled simulation instead of claiming GPS data.
- **Smart search** for addresses and places (Photon / OpenStreetMap). When Photon is down, it
  falls back to the ~24,000 official stops and cities, which needs no network.
- **Interactive map:**
  - Real street and rail geometry.
  - Distinct styles: bus solid, heavy rail dashed orange on black, light rail cyan double line,
    walking blue dots.
  - Click anywhere to set the origin or destination.
- **Step-by-step directions** with clock times for every boarding and alighting.
- **AI resilience scoring (optional module).** `ai_engine.py` asks Gemini how likely a
  passenger is to be stranded at each transfer. It falls back to a local heuristic when no key
  is set. It is not currently shown in the UI.

## Tech stack

| Area | Tools |
|---|---|
| App / UI | Streamlit, streamlit-searchbox, folium + streamlit-folium |
| Routing | NumPy, SciPy (sparse-graph Dijkstra over ~470k nodes), NetworkX |
| Transit data | MOT GTFS feed, Open Bus Stride (SIRI real-time) |
| Maps & geodata | OpenStreetMap: OSRM (street shapes), Overpass, osmnx (rail network), Photon and Nominatim via geopy (geocoding) |
| AI (optional) | Google Gemini via `google-genai` |

## Project structure

```
app.py          Streamlit UI: search, cards, directions, map
gtfs.py         GTFS download, network build, timetable routing
realtime.py     Live bus delays (SIRI via Open Bus Stride)
routing.py      Shared data classes + fallback engine (static network + OSM stops)
geo.py          Geocoding / autocomplete, OSRM shapes, Overpass stop lookups
rail.py         Rail-track geometry from OpenStreetMap (osmnx)
cached_data.py  Static backbone network and Hebrew labels
ai_engine.py    Optional Gemini resilience scoring
```

## Running locally

Requires Python 3.11 or newer (3.12 recommended).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

**First start takes a few minutes.** The app downloads the MOT GTFS feed (~135 MB) and builds
the national network into `.gtfs/`. Later starts load it in seconds. It rebuilds automatically
once a week, and when the feed is older than 14 days. These files are generated, not part of
the repository.

## API keys (optional)

Only the AI resilience module needs a key. The planner, map and real-time mode all work
without one. Keys are read from the environment, or from Streamlit secrets, and never from
the code:

```bash
export GEMINI_API_KEY="your-key"   # Windows PowerShell: $env:GEMINI_API_KEY="your-key"
```

or create `.streamlit/secrets.toml`, which is git-ignored:

```toml
GEMINI_API_KEY = "your-key"
```

## Deploying to Streamlit Community Cloud

1. Push this repository to GitHub.
2. On [share.streamlit.io](https://share.streamlit.io), click **Create app** and select the
   repository, branch `main` and main file `app.py`.
3. Under **Advanced settings**, choose Python **3.12**.
4. If you use the AI module, paste `GEMINI_API_KEY = "..."` into the **Secrets** box.

The first launch builds the network as described above. This peaks at about 1.4 GB of memory
and takes a few minutes, so the first visitor sees a loading spinner.

## Data sources & credits

- Timetables: [Israel Ministry of Transport GTFS](https://gtfs.mot.gov.il/gtfsfiles/)
- Real-time positions: [Open Bus Stride](https://open-bus-stride-api.hasadna.org.il) by the
  Public Knowledge Workshop (Hasadna)
- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors; routing by
  [FOSSGIS OSRM](https://routing.openstreetmap.de); search by [Photon](https://photon.komoot.io)

## Limitations

- Times along a trip are scaled from one representative trip per route pattern, so stop times
  can be off by a minute or two.
- Searches are for today. There is no date picker yet.
- Israel Railways and the light rail don't report to SIRI, so their legs follow the timetable
  even in real-time mode.
