# WebUntis → Google Calendar bridge

A tiny FastAPI service that logs into WebUntis using your credentials and exposes an iCalendar feed (`/calendar.ics`) suitable for Google Calendar or any CalDAV client.

## Setup
1. Install uv (already present in this repo) and create env:
   ```bash
   uv venv .venv
   source .venv/bin/activate
   uv sync
   ```
2. Copy `.env.example` to `.env` and fill in your school credentials and timezone.

## Running
```bash
uv run uvicorn main:app --reload --port 8000
```
Then subscribe Google Calendar to `http://localhost:8000/calendar.ics`.

### Parameters
- `weeks` (int, default 3): how many weeks from the current week to export (ignored if you pass explicit dates).
- `start` / `end` (YYYY-MM-DD): explicit date range override.
- `klasse` (string): exact class name; if set, the service fetches that class timetable and intersects it with your personal timetable when available.
- `token` (string): required if `ACCESS_TOKEN` is set in the environment; pass as `?token=...`.

## Docker
Build and run:
```bash
docker build -t untis-ics .
docker run --rm -p 8000:8000 --env-file .env untis-ics
```
The container uses uv to install dependencies and starts Uvicorn on port 8000.

## Docker Compose (Pi-friendly)
```bash
docker compose up -d
```
Notes:
- Uses the same `.env` file for credentials.
- If your Raspberry Pi needs an explicit platform (e.g., 64-bit Pi OS), set `platform: linux/arm64` in `docker-compose.yml` (already commented there).
- When `ACCESS_TOKEN` is set, include `?token=<value>` in the subscribed URL.

### Cancellation highlighting
- Lessons with `code=cancelled`, `cellState=3`, or any teacher ID `0` are marked `STATUS:CANCELLED`, `CATEGORIES:Cancelled`, and colored grey (Google may ignore color hints).

## Health check
`/health` returns `{ "ok": true }`.

## Notes
- If your school exposes class timetables instead of per-user, replace `my_timetable` with `timetable(klasse=...)` after fetching the class object.
- Timezone must be an Olson TZ string (e.g., `Europe/Berlin`).
- Teacher names are resolved via rights where available; otherwise a hardcoded fallback map is used and IDs are shown alongside names.
- Set `ACCESS_TOKEN` to a secret string to require `?token=...` on the feed URL.
