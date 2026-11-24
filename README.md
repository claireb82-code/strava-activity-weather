# strava-activity-weather

Automatically appends the geographical location and weather conditions to
the description of recent Strava **Walk**, **Hike**, and **Snowboard**
activities. It never changes an activity's title, and it never overwrites an
existing description — it only adds to the end. This
project only ever touches those activity types — it never modifies rides,
runs, or any other activity or account setting (that's what
[`strava-commute-titler`](https://github.com/claireb82-code/strava-commute-titler) is for).

For each matching activity that doesn't already have this info, the script:

- Reverse-geocodes the start coordinates via [OpenStreetMap Nominatim](https://nominatim.org)
  (free, no API key) to get a neighbourhood/city name. Queries at two zoom
  levels: a coarse one for the real enclosing town (OSM sometimes tags a
  tiny hamlet/hillside locality as "village" too, which would otherwise
  shadow the actual town — e.g. "Mondacce" instead of "Minusio"), and a
  fine one only for a genuine suburb/neighbourhood qualifier
- Looks up historical weather for that place and time via
  [Open-Meteo](https://open-meteo.com) (free, no API key) — temperature and
  conditions (e.g. "Partly cloudy")
- Appends a line like `📍 Letten, Zürich — 18°C, Mainly clear` to the
  activity's description, preserving whatever was already there
- Leaves the title exactly as it is — titles are never renamed.

Matching is idempotent: an activity is skipped once its description already
contains the 📍 marker, so re-running never duplicates the line.

## Setup

This reuses the same Strava API app and refresh token as `strava-commute-titler`
(same account, same `activity:write` scope covers all activity types — no
separate OAuth flow needed). `.env` was seeded from that project's credentials.

- `ACTIVITY_TYPES` — comma-separated Strava activity types to process
  (default `Walk,Hike,Snowboard`)
- `LOOKBACK_HOURS` — how far back to check for new activities each run
  (default `24`)

Run it: `python3 activity_context.py` (stdlib only, no dependencies).
Use `--dry-run` to log what would change without writing anything.

## Running on a schedule

Installed as a macOS `launchd` LaunchAgent
(`~/Library/LaunchAgents/com.claire.strava-activity-weather.plist`) using
`StartInterval` to run every 30 minutes, any day/time (unlike the commute
titler, these activities aren't confined to weekday commute hours).

```bash
launchctl load -w ~/Library/LaunchAgents/com.claire.strava-activity-weather.plist   # start
launchctl unload ~/Library/LaunchAgents/com.claire.strava-activity-weather.plist    # stop
```

## Notes

- All network requests use a 30-second timeout. Without one, a stalled
  connection could hang the script indefinitely — and since this runs as a
  `StartInterval` LaunchAgent, launchd won't fire the next scheduled run
  until the current process exits, so a single hung request silently paused
  the whole automation (this happened for 36+ hours before being caught).
- Weather comes from Open-Meteo's forecast endpoint with `start_date`/`end_date`
  set to the activity's local date, not the historical archive endpoint —
  the archive (ERA5) has a ~5 day delay, which would miss anything recent.
  A failed weather lookup is retried once after a 5s delay before falling
  back to a location-only update, since a transient API error would
  otherwise be marked processed and never retried.
- Nominatim's usage policy caps requests at 1/sec; the script sleeps between
  calls accordingly.
- `.env` is git-ignored and never committed — it contains the API secret and
  refresh token (shared with `strava-commute-titler`; if Strava ever rotates
  the refresh token, only whichever script ran most recently will have the
  current one — see project note if both start failing auth).
