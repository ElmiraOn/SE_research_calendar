"""Build validated Google Calendar and Outlook event links."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


AOE_TIMEZONE = "Etc/GMT+12"
PREFERRED_TIMEZONES = (
    AOE_TIMEZONE,
    "America/Toronto",
    "America/Vancouver",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "UTC",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Tokyo",
    "Australia/Sydney",
)


def timezone_display_name(name: str) -> str:
    if name == AOE_TIMEZONE:
        return "Anywhere on Earth (UTC−12:00)"
    if name == "UTC":
        return "UTC (UTC+00:00)"
    return name.replace("_", " ")


def calendar_timezone_options() -> tuple[str, ...]:
    """Return IANA timezones with common deadline choices first."""
    installed = available_timezones()
    preferred = [name for name in PREFERRED_TIMEZONES if name in installed]
    remainder = sorted(installed.difference(preferred))
    return tuple(preferred + remainder)


def infer_timezone_name(source_timezone: str) -> Optional[str]:
    """Convert an extracted timezone label to an IANA timezone when possible."""
    value = " ".join(str(source_timezone or "").split())
    if not value or value.casefold() in {"not specified", "unknown", "none"}:
        return None
    if re.search(r"\b(?:aoe|anywhere on earth)\b", value, flags=re.IGNORECASE):
        return AOE_TIMEZONE
    if re.search(r"\bUTC\s*-\s*12(?::?00)?\b", value, flags=re.IGNORECASE):
        return AOE_TIMEZONE
    if value.upper() in {"UTC", "GMT", "UTC+00:00", "UTC-00:00"}:
        return "UTC"
    if value in available_timezones():
        return value
    return None


def build_calendar_links(
    *,
    title: str,
    event_date: date,
    event_time: time,
    timezone_name: str,
    description: str = "",
    source_url: str = "",
    duration_minutes: int = 1,
) -> dict[str, str]:
    """Build provider URLs for one timezone-aware deadline event."""
    clean_title = " ".join(str(title).split())
    if not clean_title:
        raise ValueError("An event title is required.")
    if not isinstance(event_date, date) or not isinstance(event_time, time):
        raise ValueError("A valid event date and time are required.")
    if duration_minutes <= 0:
        raise ValueError("Event duration must be positive.")

    try:
        event_zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("A valid time zone is required.") from exc

    starts_at = datetime.combine(event_date, event_time, tzinfo=event_zone)
    ends_at = starts_at + timedelta(minutes=duration_minutes)
    starts_utc = starts_at.astimezone(timezone.utc)
    ends_utc = ends_at.astimezone(timezone.utc)

    detail_parts = [part.strip() for part in (description, source_url) if part.strip()]
    details = "\n\n".join(detail_parts)

    google_query = urlencode(
        {
            "action": "TEMPLATE",
            "text": clean_title,
            "dates": (
                f"{starts_utc.strftime('%Y%m%dT%H%M%SZ')}/"
                f"{ends_utc.strftime('%Y%m%dT%H%M%SZ')}"
            ),
            "ctz": timezone_name,
            "details": details,
        }
    )
    outlook_query = urlencode(
        {
            "rru": "addevent",
            "subject": clean_title,
            "startdt": starts_at.isoformat(timespec="seconds"),
            "enddt": ends_at.isoformat(timespec="seconds"),
            "body": details,
        }
    )
    return {
        "google": f"https://calendar.google.com/calendar/render?{google_query}",
        "outlook": f"https://outlook.office.com/calendar/0/deeplink/compose?{outlook_query}",
    }
