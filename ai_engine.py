"""AI Resilience Scoring Engine (Gemini via google-genai).

Sends the three candidate routes, their transfer points and line headways to
Gemini and gets back a structured, schema-validated resilience verdict per
option. Without an API key (or on any API error) it falls back to the local
heuristic in routing.resilience_score so the app always renders.
"""
import json
import os
from typing import Literal

from pydantic import BaseModel, Field

from cached_data import MAJOR_HUB, MINOR_STOP, MODE_LABEL
from routing import resilience_score

MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-latest")

HIGH, MODERATE, LOW = "חוסן גבוה", "סיכון בינוני", "סיכון גבוה"
Badge = Literal["חוסן גבוה", "סיכון בינוני", "סיכון גבוה"]


class OptionVerdict(BaseModel):
    option: str = Field(description="Route title exactly as given in the input 'option' field")
    resilience_score: int = Field(ge=0, le=100, description="0-100 likelihood of NOT being stranded")
    status: Badge
    weakest_transfer: str = Field(description="Hebrew stop name of the riskiest transfer, or 'None'")


class ResilienceReport(BaseModel):
    options: list[OptionVerdict]


PROMPT = f"""You are a public-transit reliability analyst for Israel. Data is in Hebrew.
For each route option below, assess the risk that a passenger gets STRANDED at a transfer
point if the connecting vehicle is missed or late. Consider:
- headway of the next line (long headways = long strand time),
- station type ("{MAJOR_HUB}" = major hub vs "{MINOR_STOP}" = minor stop) and safety score
  (shelter, lighting, staffing),
- number of alternative lines serving the transfer stop (fallback options),
- number of transfers (each one compounds risk); zero-transfer routes are inherently robust.

Return one entry per option with a resilience_score 0-100 and a status, using these exact
Hebrew values: "{HIGH}" (>=75), "{MODERATE}" (50-74), "{LOW}" (<50).

Routes (JSON):
{{routes}}"""


def _status(score):
    return HIGH if score >= 75 else MODERATE if score >= 50 else LOW


def route_payload(routes):
    """Serialize routing.Route objects into the compact structure the AI receives."""
    out = []
    for r in routes:
        if r is None:
            continue
        rides = [l for l in r.legs if l.mode != "Walk"]
        out.append({
            "option": r.title,
            "total_minutes": r.total_minutes,
            "transfers": r.transfers,
            "walk_minutes": r.walk_minutes,
            "legs": [{"line": l.label, "mode": MODE_LABEL[l.mode], "headway_min": l.headway,
                      "from": l.names[0], "to": l.names[-1],
                      **({"times_estimated": True} if l.estimated else {})} for l in rides],
            "transfer_points": [{
                "stop": l.names[0],
                "hub_type": l.board_hub,
                "safety": round(l.board_safety, 2),
                "alternative_lines": l.board_lines,
                "next_line": l.label,
                "next_line_headway_min": l.headway,
            } for l in rides[1:]],
        })
    return out


def _api_key():
    """Environment variable first, then Streamlit secrets (.streamlit/secrets.toml
    locally, the app's Secrets settings on Streamlit Community Cloud). Never hardcoded."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get("GEMINI_API_KEY") or st.secrets.get("GOOGLE_API_KEY")
    except Exception:  # no secrets file, or not running under Streamlit
        return None


def _fallback(routes):
    return {r.title: {"score": resilience_score(r), "status": _status(resilience_score(r)),
                      "weakest": None, "source": "heuristic"} for r in routes if r}


def evaluate_route_resilience(route_data):
    """route_data: list of routing.Route (None entries allowed).

    Returns {title: {"score", "status", "weakest", "source"}} where source is
    "gemini" or "heuristic", plus an "_error" key if the AI call failed.
    """
    key = _api_key()
    if not key:
        return _fallback(route_data)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=MODEL,
            contents=PROMPT.format(routes=json.dumps(route_payload(route_data), ensure_ascii=False, indent=1)),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ResilienceReport,
                temperature=0.2,
            ),
        )
        report = resp.parsed or ResilienceReport.model_validate_json(resp.text)
        result = _fallback(route_data)
        for v in report.options:
            if v.option in result:
                result[v.option] = {"score": v.resilience_score, "status": v.status,
                                    "weakest": None if v.weakest_transfer == "None" else v.weakest_transfer,
                                    "source": "gemini"}
        return result
    except Exception as e:  # network, quota, bad key, schema mismatch
        result = _fallback(route_data)
        result["_error"] = f"{type(e).__name__}: {e}"
        return result
