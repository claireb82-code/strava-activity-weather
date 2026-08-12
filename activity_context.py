#!/usr/bin/env python3
"""Appends location and weather conditions to the description of recent
Strava walks, hikes, and snowboard activities, and renames still-default-
titled ones to a fun name based on activity, location, and weather. Never
touches any other activity type, and never re-touches an activity that
already has this info."""

import json
import random
import re
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
LOG_PATH = ROOT / "activity_context.log"

MARKER = "\U0001F4CD"  # 📍 — presence of this marker means already processed

WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Slight rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}

# Groups of Open-Meteo weather codes to playful title adjectives.
WEATHER_ADJECTIVES = {
    "clear": (["0", "1"], ["Sunny", "Golden", "Bluebird", "Sunshine-Soaked"]),
    "cloudy": (["2", "3"], ["Cloud-Dappled", "Grey-Sky", "Moody"]),
    "fog": (["45", "48"], ["Foggy", "Misty", "Hazy"]),
    "rain": (["51", "53", "55", "56", "57", "61", "63", "65", "66", "67", "80", "81", "82"],
             ["Rainy", "Drizzly", "Soggy", "Puddle-Jumping"]),
    "snow": (["71", "73", "75", "77", "85", "86"], ["Snowy", "Powder", "Frosty", "Wintry"]),
    "storm": (["95", "96", "99"], ["Stormy", "Thunder-Chasing", "Electric"]),
}
WEATHER_ADJECTIVES_BY_CODE = {
    int(code): adjectives
    for codes, adjectives in WEATHER_ADJECTIVES.values()
    for code in codes
}
FALLBACK_ADJECTIVES = ["Fresh-Air", "Open-Sky", "Wide-Open"]

ACTIVITY_NOUNS = {
    "Walk": ["Wander", "Stroll", "Ramble", "Amble", "Saunter", "Meander"],
    "Hike": ["Trek", "Trail Quest", "Summit Push", "Climb", "Ridge Ramble"],
    "Snowboard": ["Powder Chase", "Shred Session", "Carve Run", "Slope Ride"],
}
CONNECTORS = ["in", "through", "at", "around", "near"]

# Matches Strava's auto-generated titles like "Morning Walk", "Lunch Hike".
DEFAULT_TITLE_RE = re.compile(r"^(morning|afternoon|evening|night|lunch)\s+\S+$", re.IGNORECASE)

DRY_RUN = "--dry-run" in sys.argv


def generate_title(activity_type, place, weather_code):
    noun = random.choice(ACTIVITY_NOUNS.get(activity_type, ["Outing"]))
    adjectives = WEATHER_ADJECTIVES_BY_CODE.get(weather_code, FALLBACK_ADJECTIVES)
    adjectives = [a for a in adjectives if a.lower() not in noun.lower()] or adjectives
    adjective = random.choice(adjectives)
    if place:
        connector = random.choice(CONNECTORS)
        return f"{adjective} {noun} {connector} {place}"
    return f"{adjective} {noun}"


def load_env():
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def save_env(env):
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def api_request(url, method="GET", data=None, headers=None, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def refresh_access_token(env):
    resp = api_request(
        "https://www.strava.com/oauth/token",
        method="POST",
        data={
            "client_id": env["STRAVA_CLIENT_ID"],
            "client_secret": env["STRAVA_CLIENT_SECRET"],
            "refresh_token": env["STRAVA_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
    )
    if resp.get("refresh_token") and resp["refresh_token"] != env["STRAVA_REFRESH_TOKEN"]:
        env["STRAVA_REFRESH_TOKEN"] = resp["refresh_token"]
        save_env(env)
    return resp["access_token"]


def reverse_geocode(lat, lng):
    resp = api_request(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 14},
        headers={"User-Agent": "strava-activity-weather/1.0 (personal automation script)"},
    )
    addr = resp.get("address", {})
    # Prefer the actual settlement name (city/town/village) — smaller labels
    # like "hamlet" or "neighbourhood" can name a tiny sub-area that isn't
    # how the place is normally identified (e.g. "Letten" hamlet inside the
    # village of Rümlang), so they're only used as a qualifier, not primary.
    place = (
        addr.get("city") or addr.get("town") or addr.get("village")
        or addr.get("municipality") or addr.get("hamlet")
    )
    sub = addr.get("suburb") or addr.get("neighbourhood") or addr.get("quarter")
    if sub and place and sub != place:
        return f"{sub}, {place}"
    return place or resp.get("display_name", "").split(",")[0]


def fetch_weather(lat, lng, local_date, local_hour):
    resp = api_request(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lng,
            "hourly": "temperature_2m,weather_code",
            "start_date": local_date,
            "end_date": local_date,
            "timezone": "auto",
            "temperature_unit": "celsius",
        },
    )
    hourly = resp.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    codes = hourly.get("weather_code", [])
    if not times:
        return None
    target = f"{local_date}T{local_hour:02d}:00"
    idx = min(range(len(times)), key=lambda i: abs(_hours_between(times[i], target)))
    return temps[idx], codes[idx]


def _hours_between(t1, t2):
    def to_minutes(t):
        _, hm = t.split("T")
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    return to_minutes(t1) - to_minutes(t2)


def main():
    env = load_env()
    access_token = refresh_access_token(env)
    headers = {"Authorization": f"Bearer {access_token}"}

    activity_types = {t.strip() for t in env.get("ACTIVITY_TYPES", "Walk,Hike,Snowboard").split(",") if t.strip()}
    lookback_hours = float(env.get("LOOKBACK_HOURS", 24))

    after = int(time.time()) - int(lookback_hours * 3600)
    activities = api_request(
        "https://www.strava.com/api/v3/athlete/activities",
        headers=headers,
        params={"after": after, "per_page": 50},
    )

    for act in activities:
        if act.get("type") not in activity_types:
            continue

        act_id = act["id"]
        detail = api_request(
            f"https://www.strava.com/api/v3/activities/{act_id}",
            headers=headers,
        )
        description = detail.get("description") or ""
        if MARKER in description:
            continue

        start = act.get("start_latlng")
        if not start or len(start) != 2:
            log(f"Skipping activity {act_id} '{act.get('name')}': no start location")
            continue
        lat, lng = start

        try:
            location = reverse_geocode(lat, lng)
        except Exception as e:
            log(f"Geocoding failed for activity {act_id}: {e}")
            location = None
        time.sleep(1)  # Nominatim usage policy: max 1 req/sec

        start_date_local = act.get("start_date_local", "")
        local_date = start_date_local[:10]
        local_hour = int(start_date_local[11:13]) if len(start_date_local) >= 13 else 12

        try:
            weather_result = fetch_weather(lat, lng, local_date, local_hour)
        except Exception as e:
            log(f"Weather lookup failed for activity {act_id}: {e}")
            weather_result = None

        weather = None
        weather_code = None
        if weather_result:
            temp, weather_code = weather_result
            weather = f"{round(temp)}°C, {WEATHER_CODES.get(weather_code, f'code {weather_code}')}"

        if not location and not weather:
            log(f"Skipping activity {act_id} '{act.get('name')}': no location or weather available")
            continue

        parts = [MARKER]
        if location:
            parts.append(location)
        if weather:
            parts.append(f"— {weather}" if location else weather)
        info_line = " ".join(parts)

        new_description = f"{description}\n{info_line}".strip() if description else info_line
        update_data = {"description": new_description}

        current_name = act.get("name", "")
        new_name = None
        if DEFAULT_TITLE_RE.match(current_name.strip()):
            place = location.split(",")[0].strip() if location else None
            new_name = generate_title(act.get("type"), place, weather_code)
            update_data["name"] = new_name

        if DRY_RUN:
            log(
                f"[dry-run] Would update activity {act_id} '{current_name}': "
                f"name -> '{new_name}', description += '{info_line}'"
                if new_name else
                f"[dry-run] Would update activity {act_id} '{current_name}' description to:\n{new_description}"
            )
            continue

        updated = api_request(
            f"https://www.strava.com/api/v3/activities/{act_id}",
            method="PUT",
            data=update_data,
            headers=headers,
        )
        log(
            f"Updated activity {act_id}: name '{current_name}' -> '{updated.get('name')}', "
            f"description += '{info_line}'"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR: {e}")
        raise
