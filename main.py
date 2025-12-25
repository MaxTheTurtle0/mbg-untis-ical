from datetime import date, datetime, timedelta
import hmac
import os
from functools import lru_cache

import pytz
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from dotenv import load_dotenv
import webuntis
from icalendar import Calendar, Event

# Load environment variables from .env if present
load_dotenv()

app = FastAPI(title="WebUntis → iCal bridge")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")


def get_tz():
    tzname = os.getenv("TIMEZONE", "UTC")
    try:
        return pytz.timezone(tzname)
    except Exception as exc:
        raise RuntimeError(f"Invalid TIMEZONE '{tzname}': {exc}")


def make_session():
    required = ["WEBUNTIS_SERVER", "WEBUNTIS_SCHOOL", "WEBUNTIS_USERNAME", "WEBUNTIS_PASSWORD"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")

    s = webuntis.Session(
        username=os.environ["WEBUNTIS_USERNAME"],
        password=os.environ["WEBUNTIS_PASSWORD"],
        server=os.environ["WEBUNTIS_SERVER"],
        school=os.environ["WEBUNTIS_SCHOOL"],
        useragent=os.getenv("WEBUNTIS_USERAGENT", "UntisICSBridge/1.0"),
    )

    s.login()
    return s


def period_is_cancelled(period):
    # Common WebUntis flags for cancellations
    code = getattr(period, "code", "")
    cell_state = getattr(period, "cellState", None)
    return (code and code.lower() == "cancelled") or cell_state == 3


def format_people(items):
    return ", ".join(getattr(i, "longname", i.name) for i in items)


# Provided fallback mapping when API rights are missing
HARDCODED_TEACHERS = {
    0: "cancelled",
    32: "gri",
    106: "sma",
    31: "grä",
    308: "lie",
    372: "ned",
    41: "höl",
    430: "spe",
    167: "uhr",
    117: "tei",
    412: "flo",
    46: "jel",
    110: "std",
}


def localize_dt(dt, tz):
    """
    WebUntis sometimes returns naive datetimes. Ensure they carry timezone.
    """
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return tz.localize(dt)
    return dt.astimezone(tz)


@lru_cache(maxsize=1)
def cached_timezone():
    return get_tz()


@app.get("/calendar.ics")
def calendar(
    weeks: int = Query(3, ge=1, description="How many weeks to include starting this week"),
    start: date | None = Query(None, description="Override start date (YYYY-MM-DD)"),
    end: date | None = Query(None, description="Override end date (YYYY-MM-DD)"),
    klasse: str | None = Query(None, description="If set, fetch timetable for class name instead of logged-in user"),
    token: str | None = Query(None, description="Access token; required if ACCESS_TOKEN is set"),
):
    """
    Export the logged-in user's timetable as an ICS feed.

    Args:
        weeks: number of weeks to include starting from the current week (default 3).
        start/end: explicit date range overrides.
        klasse: class name (exact match from Untis) if you want class timetable instead of my_timetable.
        token: optional access token; required when ACCESS_TOKEN env is set.
    """
    if ACCESS_TOKEN:
        if not token or not hmac.compare_digest(token, ACCESS_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid or missing token")
    if not start or not end:
        today = date.today()
        start = today - timedelta(days=today.weekday())  # Monday of this week
        end = start + timedelta(days=7 * weeks)

    try:
        with make_session() as session:
            # Attempt to fetch the personal timetable regardless of klasse; if it works we can use it for filtering
            my_periods = []
            try:
                my_periods = session.my_timetable(start=start, end=end)
            except Exception:
                my_periods = []

            if klasse:
                class_obj = next((k for k in session.klassen() if k.name == klasse), None)
                if not class_obj:
                    raise HTTPException(status_code=404, detail=f"Klasse '{klasse}' not found")
                class_periods = session.timetable(klasse=class_obj, start=start, end=end)
                if my_periods:
                    my_ids = {p.id for p in my_periods}
                    periods = [p for p in class_periods if p.id in my_ids]
                else:
                    periods = class_periods
            else:
                periods = my_periods or session.my_timetable(start=start, end=end)

            def try_map(fetcher):
                try:
                    return {obj.id: getattr(obj, "longname", obj.name) for obj in fetcher()}
                except Exception:
                    return {}

            subject_map = try_map(session.subjects)
            teacher_map = {**HARDCODED_TEACHERS}
            teacher_map.update(try_map(session.teachers))
            room_map = try_map(session.rooms)

            # If teacher_map is empty but we have teacher IDs, try a targeted fetch
            teacher_ids_needed = {
                item.get("id")
                for p in periods
                for item in getattr(p, "_data", {}).get("te", [])
                if isinstance(item, dict) and item.get("id") is not None
            }
            if teacher_ids_needed and len(teacher_map) <= len(HARDCODED_TEACHERS):
                try:
                    filtered = session.teachers().filter(id=list(teacher_ids_needed))
                    teacher_map.update({t.id: getattr(t, "longname", t.name) for t in filtered})
                except Exception:
                    pass
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"WebUntis error: {exc}")

    tz = cached_timezone()
    cal = Calendar()
    cal.add("prodid", "-//Untis ICS Bridge//")
    cal.add("version", "2.0")
    cal.add("X-WR-CALNAME", "School Timetable")
    cal.add("X-WR-TIMEZONE", tz.zone)

    for period in periods:
        event = Event()
        event.add("uid", f"{period.id}@untis")

        # WebUntis Period objects expose .start and .end datetimes; use them directly
        start_dt = localize_dt(period.start, tz)
        end_dt = localize_dt(period.end, tz)
        event.add("dtstart", start_dt)
        event.add("dtend", end_dt)

        pdata = getattr(period, "_data", {})
        su_entries = [item for item in pdata.get("su", []) if isinstance(item, dict)]
        te_entries = [item for item in pdata.get("te", []) if isinstance(item, dict)]
        ro_entries = [item for item in pdata.get("ro", []) if isinstance(item, dict)]

        def names_from_ids(entries, mapping, include_id=False):
            names = []
            for item in entries:
                _id = item.get("id")
                name = mapping.get(_id)
                if not name:
                    name = item.get("longname") or item.get("name")
                label = name or str(_id)
                if include_id and _id is not None:
                    label = f"{label} ({_id})"
                names.append(label)
            return ", ".join(n for n in names if n)

        subject_name = names_from_ids(su_entries, subject_map) or "Lesson"
        teacher_names = names_from_ids(te_entries, teacher_map, include_id=True)
        room_names = names_from_ids(ro_entries, room_map)

        # Summary and location
        title_parts = [subject_name]
        if teacher_names:
            title_parts.append(f"@ {teacher_names}")
        event.add("summary", " ".join(title_parts))
        event.add("location", room_names or "TBD")

        # Description contains details shown in most calendar apps
        event.add(
            "description",
            f"Subject: {subject_name}\nTeachers: {teacher_names or 'n/a'}\nRoom: {room_names or 'n/a'}",
        )

        teacher_ids = {item.get("id") for item in te_entries if isinstance(item, dict)}
        teacher_cancelled = 0 in teacher_ids

        is_cancelled = period_is_cancelled(period) or teacher_cancelled

        # Visual differentiation; keep STATUS confirmed so Google doesn't drop it from the feed
        event.add("status", "CONFIRMED")
        if is_cancelled:
            event.add("categories", ["Cancelled"])
            event.add("X-GOOGLE-CALENDAR-COLOR", "#9e9e9e")  # grey
            event["summary"] = f"[Cancelled] {event['summary']}"
        else:
            event.add("categories", ["Lesson"])
            event.add("X-GOOGLE-CALENDAR-COLOR", "#1976d2")  # blue

        cal.add_component(event)

    if not periods:
        raise HTTPException(
            status_code=404,
            detail=f"No periods returned between {start} and {end}. Check credentials, rights, klasse, or date range.",
        )

    return Response(content=cal.to_ical(), media_type="text/calendar")


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
