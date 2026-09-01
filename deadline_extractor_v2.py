#!/usr/bin/env python3
"""Extract track-level important dates from Researchr conference websites.

Example:
    python deadline_extractor.py \
        https://conf.researchr.org/home/fse-2027 \
        --output fse_2027_deadlines.csv

Dependencies:
    pip install requests beautifulsoup4 python-dateutil

A text file containing one conference URL per line creates one CSV per
conference. By default, only submission-related deadlines are retained; pass
``--all-important-dates`` to keep notification, camera-ready, conference, and
other dates. The public function ``extract_conference_deadlines`` can also be
imported by a Streamlit application.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse, urlunparse

try:
    import requests
    from bs4 import BeautifulSoup, Tag
    from dateutil import parser as date_parser
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError as exc:  # pragma: no cover - depends on local environment
    raise SystemExit(
        "Missing dependency. Install with:\n"
        "  pip install requests beautifulsoup4 python-dateutil"
    ) from exc


USER_AGENT = "SEFieldCalendar/0.1 (Researchr deadline extractor)"
DEFAULT_TIMEOUT = 30
DEFAULT_DELAY_SECONDS = 0.25

WEEKDAY = r"(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)"
MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
)

# Researchr commonly renders dates as "Fri 2 Oct 2026" and ranges as
# "Mon 14 - Fri 18 Dec 2026".
RANGE_PATTERN = re.compile(
    rf"(?P<whole>"
    rf"(?:(?P<start_weekday>{WEEKDAY})\s+)?"
    rf"(?P<start_day>\d{{1,2}})(?:st|nd|rd|th)?"
    rf"(?:\s+(?P<start_month>{MONTH})(?:\s+(?P<start_year>\d{{4}}))?)?"
    rf"\s*(?:-|–|—|to)\s*"
    rf"(?:(?P<end_weekday>{WEEKDAY})\s+)?"
    rf"(?P<end_day>\d{{1,2}})(?:st|nd|rd|th)?\s+"
    rf"(?P<end_month>{MONTH})\s+(?P<end_year>\d{{4}})"
    rf")",
    re.IGNORECASE,
)

DAY_FIRST_PATTERN = re.compile(
    rf"(?P<whole>(?:(?:{WEEKDAY})\s+)?\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTH}\s+\d{{4}})",
    re.IGNORECASE,
)

MONTH_FIRST_PATTERN = re.compile(
    rf"(?P<whole>{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,)?\s+\d{{4}})",
    re.IGNORECASE,
)

IMPORTANT_DATES_RE = re.compile(r"\bimportant\s+dates?\b", re.IGNORECASE)
TIMEZONE_RE = re.compile(r"\bUTC\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?\b", re.IGNORECASE)
STATUS_ONLY = {
    "new",
    "updated",
    "changed",
    "extended",
    "extension",
    "past",
    "closed",
    "tbd",
    "tentative",
}

# Labels that clearly describe a deadline for submitting material. Explicit
# submit/submission wording is the strongest signal. The object/cue lists cover
# shorter Researchr labels such as "Paper deadline" or "Artifact due".
SUBMISSION_ACTION_RE = re.compile(
    r"\b(?:submission|submissions|submit|submitted|resubmission|resubmit)\b",
    re.IGNORECASE,
)
SUBMISSION_OBJECT_RE = re.compile(
    r"\b(?:abstract|paper|manuscript|proposal|artifact|dataset|data\s+showcase|"
    r"demo|demonstration|poster|tutorial|workshop\s+proposal|doctoral\s+symposium|"
    r"application|competition\s+entry|challenge\s+entry|solution|report)\b",
    re.IGNORECASE,
)
DEADLINE_CUE_RE = re.compile(
    r"\b(?:deadline|due|closes?|closing|cut[ -]?off)\b",
    re.IGNORECASE,
)
NON_SUBMISSION_RE = re.compile(
    r"\b(?:notification|acceptance|decision|author\s+response|rebuttal|discussion|"
    r"camera[ -]?ready|final\s+(?:version|copy)|publication|conference|symposium\s+dates?|"
    r"workshop\s+dates?|event\s+dates?|presentation|presentations|talk|program|schedule|"
    r"attendance|attendee|early[- ]bird|visa|travel|hotel|accommodation|registration)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TrackLink:
    name_hint: str
    url: str


@dataclass
class Deadline:
    label: str
    date_text: str
    start_date: str
    end_date: Optional[str]
    timezone: Optional[str]
    source_url: str
    confidence: str
    needs_review: bool


class ExtractionError(RuntimeError):
    """Raised when a Researchr page cannot be extracted safely."""


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def clean_label(value: str) -> str:
    value = normalize_space(value)
    value = re.sub(r"^[\s:;|,./\\\-–—]+", "", value)
    value = re.sub(r"[\s:;|,./\\\-–—]+$", "", value)
    return value.strip()


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    path = re.sub(r"/+", "/", parsed.path).rstrip("/")
    return urlunparse((parsed.scheme or "https", parsed.netloc, path, "", "", ""))


def conference_slug_from_url(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    for marker in ("home", "track"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    raise ExtractionError(
        "Expected a Researchr URL such as "
        "https://conf.researchr.org/home/fse-2027"
    )


def conference_title_from_slug(slug: str) -> str:
    """Build a stable fallback title such as ``ESEIW 2026`` from a URL slug."""
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9-]*?)-(20\d{2})", slug)
    if not match:
        return slug
    name = match.group(1).replace("-", " ").upper()
    return f"{name} {match.group(2)}"


def conference_home_url(url: str) -> str:
    parsed = urlparse(url)
    slug = conference_slug_from_url(url)
    return urlunparse((parsed.scheme or "https", parsed.netloc, f"/home/{slug}", "", "", ""))


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session


def fetch_html(session: requests.Session, url: str, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    if response.status_code >= 400:
        raise ExtractionError(f"HTTP {response.status_code} while fetching {url}")
    if "html" not in response.headers.get("Content-Type", "").lower():
        raise ExtractionError(f"Expected HTML but received another content type from {url}")
    return response.text


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_page_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        title = normalize_space(h1.get_text(" ", strip=True))
        title = re.sub(r"^Welcome\s+to\s+", "", title, flags=re.IGNORECASE)
        if title:
            return title
    if soup.title:
        return normalize_space(soup.title.get_text(" ", strip=True)).split(" - ")[0]
    return "Unknown conference"


def is_track_path_for_conference(path: str, conference_slug: str) -> bool:
    """Return whether *path* is a track page belonging to the conference.

    Researchr currently uses at least two track URL layouts:

    * ``/track/fse-2027/fse-2027-research-papers``
    * ``/track/msr-2027-technical-papers``

    The second form is common on Researchr sites with a custom conference
    domain, such as ``2027.msrconf.org``.
    """
    normalized_path = re.sub(r"/+", "/", path or "").rstrip("/")
    nested_prefix = f"/track/{conference_slug}/"
    flat_prefix = f"/track/{conference_slug}-"
    return normalized_path.startswith(nested_prefix) or normalized_path.startswith(flat_prefix)


def discover_track_links(
    soup: BeautifulSoup,
    home_url: str,
    conference_slug: str,
) -> list[TrackLink]:
    """Discover unique Researchr track pages from a conference home page.

    Track links may use a custom Researchr-powered hostname, so discovery is
    based on the conference slug embedded in the track path rather than on an
    exact hostname match.
    """
    found: dict[str, str] = {}

    for anchor in soup.find_all("a", href=True):
        href = normalize_space(str(anchor.get("href", "")))
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue

        absolute = canonical_url(urljoin(home_url, href))
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if not is_track_path_for_conference(parsed.path, conference_slug):
            continue

        hint = normalize_space(anchor.get_text(" ", strip=True))
        if not hint or hint.lower() in {"show all tracks", "all important dates"}:
            hint = parsed.path.rsplit("/", 1)[-1]
            prefix = f"{conference_slug}-"
            if hint.startswith(prefix):
                hint = hint[len(prefix):]
            hint = hint.replace("-", " ").title()

        # The same track appears in navigation, content, committee navigation,
        # and the footer. Keep the shortest meaningful label as the hint.
        previous = found.get(absolute)
        if previous is None or len(hint) < len(previous):
            found[absolute] = hint

    return [TrackLink(name_hint=name, url=url) for url, name in sorted(found.items())]


def remove_conference_suffix(track_title: str, conference_title: str) -> str:
    title = normalize_space(track_title)
    conf = normalize_space(conference_title)
    if conf and title.lower().endswith(conf.lower()):
        title = title[: -len(conf)].strip(" -–—|")
    return title or track_title


def extract_track_name(
    soup: BeautifulSoup,
    conference_title: str,
    name_hint: str,
) -> str:
    h1 = soup.find("h1")
    if h1:
        title = remove_conference_suffix(
            normalize_space(h1.get_text(" ", strip=True)), conference_title
        )
        if title:
            return title
    return name_hint


def normalize_date_text(value: str) -> str:
    value = normalize_space(value)
    value = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", value, flags=re.IGNORECASE)
    return value.strip(" \t\r\n.,;:|()[]")


def _parse_date(value: str) -> datetime:
    parsed = date_parser.parse(value, fuzzy=False, dayfirst=False)
    return parsed.replace(tzinfo=None)


def parse_date_text(value: str) -> Optional[tuple[str, Optional[str]]]:
    """Return ISO start/end dates when *value* is a date-only string."""
    candidate = normalize_date_text(value)

    range_match = RANGE_PATTERN.fullmatch(candidate)
    if range_match:
        groups = range_match.groupdict()
        start_month = groups["start_month"] or groups["end_month"]
        start_year = groups["start_year"] or groups["end_year"]
        start = _parse_date(
            f"{groups['start_day']} {start_month} {start_year}"
        ).date()
        end = _parse_date(
            f"{groups['end_day']} {groups['end_month']} {groups['end_year']}"
        ).date()
        return start.isoformat(), end.isoformat()

    for pattern in (DAY_FIRST_PATTERN, MONTH_FIRST_PATTERN):
        match = pattern.fullmatch(candidate)
        if match:
            day = _parse_date(match.group("whole")).date()
            return day.isoformat(), None

    return None


def find_date_fragment(value: str) -> Optional[tuple[str, int, int]]:
    """Find one recognizable date or date range inside a short text string."""
    text = normalize_space(value)
    for pattern in (RANGE_PATTERN, DAY_FIRST_PATTERN, MONTH_FIRST_PATTERN):
        match = pattern.search(text)
        if match:
            return match.group("whole"), match.start("whole"), match.end("whole")
    return None


def timezone_from_text(value: str) -> Optional[str]:
    text = normalize_space(value)
    if re.search(r"\bAoE\b|Anywhere\s+on\s+Earth", text, re.IGNORECASE):
        return "AoE (UTC-12:00)"
    match = TIMEZONE_RE.search(text)
    if not match:
        return None
    sign, hours, minutes = match.groups()
    return f"UTC{sign}{int(hours):02d}:{int(minutes or '0'):02d}"


def _date_occurrence_count(tag: Tag) -> int:
    count = 0
    for raw in tag.stripped_strings:
        text = normalize_space(str(raw))
        if parse_date_text(text):
            count += 1
        elif len(text) <= 180 and find_date_fragment(text):
            count += 1
    return count


def find_important_dates_region(soup: BeautifulSoup) -> tuple[Optional[Tag], str]:
    """Locate the smallest date-dense region associated with Important Dates.

    Researchr has historically changed CSS classes. This function therefore
    relies on the semantic heading plus date density, rather than one class.
    """
    candidates: list[tuple[float, Tag]] = []
    seen: set[int] = set()

    for text_node in soup.find_all(string=lambda s: bool(s and IMPORTANT_DATES_RE.search(s))):
        ancestor = text_node.parent
        depth = 0
        while isinstance(ancestor, Tag) and ancestor.name not in {"html", "body"} and depth < 8:
            identity = id(ancestor)
            if identity not in seen:
                seen.add(identity)
                date_count = _date_occurrence_count(ancestor)
                if date_count:
                    text_length = len(normalize_space(ancestor.get_text(" ", strip=True)))
                    density = (date_count * 1000.0) / max(text_length, 80)
                    structural_bonus = 2.0 if ancestor.name in {"aside", "section", "div", "dl", "ul"} else 0.0
                    score = density + date_count * 2.0 + structural_bonus - depth * 0.05
                    candidates.append((score, ancestor))
            ancestor = ancestor.parent
            depth += 1

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1], "important-dates-region"

    return None, "not-found"


def _is_noise_token(value: str) -> bool:
    token = clean_label(value).lower()
    if not token:
        return True
    if token in STATUS_ONLY:
        return True
    if IMPORTANT_DATES_RE.search(token):
        return True
    if token.startswith("up till ") or token in {"all important dates", "show all tracks"}:
        return True
    if timezone_from_text(token) and len(token.split()) <= 5:
        return True
    return False


def _next_label(strings: list[str], start_index: int) -> Optional[str]:
    for index in range(start_index, min(start_index + 6, len(strings))):
        candidate = clean_label(strings[index])
        if _is_noise_token(candidate):
            continue
        if parse_date_text(candidate):
            return None
        if len(candidate) > 160 or len(candidate.split()) > 24:
            return None
        return candidate
    return None


def _deadline_from_parts(
    *,
    label: str,
    date_text: str,
    timezone_name: Optional[str],
    source_url: str,
    confidence: str,
    needs_review: bool,
) -> Optional[Deadline]:
    parsed = parse_date_text(date_text)
    label = clean_label(label)
    if not parsed or not label or _is_noise_token(label):
        return None
    start_date, end_date = parsed
    return Deadline(
        label=label,
        date_text=normalize_space(date_text),
        start_date=start_date,
        end_date=end_date,
        timezone=timezone_name,
        source_url=source_url,
        confidence=confidence,
        needs_review=needs_review,
    )


def parse_deadlines_from_region(
    region: Tag,
    source_url: str,
    confidence: str = "high",
) -> list[Deadline]:
    region_text = normalize_space(region.get_text(" ", strip=True))
    timezone_name = timezone_from_text(region_text)
    strings = [normalize_space(str(item)) for item in region.stripped_strings]
    deadlines: list[Deadline] = []

    for index, text in enumerate(strings):
        # Most Researchr sidebars use a date token followed by the event label.
        parsed = parse_date_text(text)
        if parsed:
            label = _next_label(strings, index + 1)
            if label:
                deadline = _deadline_from_parts(
                    label=label,
                    date_text=text,
                    timezone_name=timezone_name,
                    source_url=source_url,
                    confidence=confidence,
                    needs_review=False,
                )
                if deadline:
                    deadlines.append(deadline)
            continue

        # Some calls list dates inline, e.g. "Submission deadline - Jan 23, 2027".
        if len(text) > 220:
            continue
        fragment = find_date_fragment(text)
        if not fragment:
            continue
        date_text, start, end = fragment
        prefix = clean_label(text[:start])
        suffix = clean_label(text[end:])
        label = prefix if prefix and not _is_noise_token(prefix) else suffix

        # A status badge such as "new" can follow the date; use the next token.
        if not label or _is_noise_token(label):
            label = _next_label(strings, index + 1)

        if label:
            deadline = _deadline_from_parts(
                label=label,
                date_text=date_text,
                timezone_name=timezone_name,
                source_url=source_url,
                confidence="medium" if confidence == "high" else confidence,
                needs_review=True,
            )
            if deadline:
                deadlines.append(deadline)

    return deduplicate_deadlines(deadlines)


def is_submission_deadline(label: str) -> bool:
    """Classify whether a Researchr important-date label is a submission date.

    Examples retained:
        Submission, Abstract submission, Paper deadline, Artifact due,
        Workshop proposal deadline, Revised paper submission.

    Examples excluded:
        Notification, Author response, Camera-ready, Presentation,
        Registration, Conference dates.

    Explicit submission wording wins even when the submitted object is a
    presentation or poster proposal. This avoids rejecting labels such as
    "Poster submission deadline" while still rejecting a plain
    "Poster presentation" date.
    """
    normalized = normalize_space(label).lower()
    if not normalized:
        return False

    if SUBMISSION_ACTION_RE.search(normalized):
        return True

    # A short label such as "Paper deadline" may omit the word submission.
    if SUBMISSION_OBJECT_RE.search(normalized) and DEADLINE_CUE_RE.search(normalized):
        return not NON_SUBMISSION_RE.search(normalized)

    return False


def filter_submission_deadlines(deadlines: Iterable[Deadline]) -> list[Deadline]:
    """Return only deadlines whose labels describe submitting material."""
    return [deadline for deadline in deadlines if is_submission_deadline(deadline.label)]


def deduplicate_deadlines(deadlines: Iterable[Deadline]) -> list[Deadline]:
    best: dict[tuple[str, str, Optional[str]], Deadline] = {}
    rank = {"high": 3, "medium": 2, "low": 1}

    for deadline in deadlines:
        normalized_label = re.sub(r"\W+", " ", deadline.label.lower()).strip()
        key = (normalized_label, deadline.start_date, deadline.end_date)
        current = best.get(key)
        if current is None or rank.get(deadline.confidence, 0) > rank.get(current.confidence, 0):
            best[key] = deadline

    return sorted(
        best.values(),
        key=lambda item: (item.start_date, item.end_date or item.start_date, item.label.lower()),
    )


def extract_deadlines_from_track_html(
    html: str,
    source_url: str,
    *,
    submission_only: bool = True,
) -> tuple[list[Deadline], Optional[str]]:
    soup = soup_from_html(html)
    region, method = find_important_dates_region(soup)
    if region is None:
        return [], "No semantic Important Dates region was found."

    all_deadlines = parse_deadlines_from_region(region, source_url=source_url)
    if not all_deadlines:
        return [], f"An Important Dates region was found using {method}, but no dates could be parsed."

    if not submission_only:
        return all_deadlines, None

    submission_deadlines = filter_submission_deadlines(all_deadlines)
    if not submission_deadlines:
        return (
            [],
            "Important Dates were found, but none were classified as submission deadlines.",
        )
    return submission_deadlines, None


def extract_conference_deadlines(
    conference_url: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    include_empty_tracks: bool = True,
    submission_only: bool = True,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """Extract all track deadlines from a Researchr conference home URL."""
    session = session or make_session()
    home_url = canonical_url(conference_home_url(conference_url))
    slug = conference_slug_from_url(home_url)

    home_html = fetch_html(session, home_url, timeout)
    home_soup = soup_from_html(home_html)
    conference_title = extract_page_title(home_soup)
    if conference_title.casefold().rstrip("!") in {"welcome", "home", "unknown conference"}:
        conference_title = conference_title_from_slug(slug)
    tracks = discover_track_links(home_soup, home_url, slug)

    track_results: list[dict[str, Any]] = []

    # Some Researchr conferences, such as AST 2027, are configured as a
    # single-track conference: the conference home page itself contains the
    # Call for Papers and Important Dates, and there is no separate /track/
    # URL to discover. In that case, parse the already-fetched home page as
    # the sole track rather than failing with "No track links were found".
    if not tracks:
        deadlines, warning = extract_deadlines_from_track_html(
            home_html, home_url, submission_only=submission_only
        )
        if deadlines or include_empty_tracks:
            track_results.append(
                {
                    "track": conference_title,
                    "url": home_url,
                    "deadlines": [asdict(item) for item in deadlines],
                    "warning": warning,
                }
            )

        if not deadlines:
            detail = warning or "No Important Dates could be parsed from the home page."
            raise ExtractionError(
                f"No separate track links were found on {home_url}, and the "
                f"conference home page could not be used as a single track: {detail}"
            )

    for position, track in enumerate(tracks):
        if position and delay_seconds > 0:
            time.sleep(delay_seconds)

        try:
            html = fetch_html(session, track.url, timeout)
            soup = soup_from_html(html)
            track_name = extract_track_name(soup, conference_title, track.name_hint)
            deadlines, warning = extract_deadlines_from_track_html(
                html, track.url, submission_only=submission_only
            )
            if deadlines or include_empty_tracks:
                track_results.append(
                    {
                        "track": track_name,
                        "url": track.url,
                        "deadlines": [asdict(item) for item in deadlines],
                        "warning": warning,
                    }
                )
        except Exception as exc:  # keep other tracks usable if one page fails
            if include_empty_tracks:
                track_results.append(
                    {
                        "track": track.name_hint,
                        "url": track.url,
                        "deadlines": [],
                        "warning": str(exc),
                    }
                )

    deadline_count = sum(len(track["deadlines"]) for track in track_results)
    return {
        "conference": conference_title,
        "conference_slug": slug,
        "conference_url": home_url,
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
        "track_count": len(track_results),
        "deadline_count": deadline_count,
        "submission_only": submission_only,
        "tracks": track_results,
    }


def flatten_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for track in result["tracks"]:
        for deadline in track["deadlines"]:
            rows.append(
                {
                    "conference": result["conference"],
                    "conference_slug": result["conference_slug"],
                    "conference_url": result["conference_url"],
                    "track": track["track"],
                    "label": deadline["label"],
                    "start_date": deadline["start_date"],
                    "end_date": deadline["end_date"],
                    "date_text": deadline["date_text"],
                    "timezone": deadline["timezone"],
                    "confidence": deadline["confidence"],
                    "needs_review": deadline["needs_review"],
                    "source_url": deadline["source_url"],
                    "extracted_at_utc": result["extracted_at_utc"],
                }
            )
    return rows


def read_conference_urls(source: str) -> list[str]:
    """Read one URL or a newline-delimited text file of URLs.

    Empty lines and lines beginning with ``#`` are ignored. Duplicate URLs are
    removed while preserving their original order.
    """
    candidate = Path(source).expanduser()

    if candidate.is_file():
        raw_values = candidate.read_text(encoding="utf-8-sig").splitlines()
    else:
        # A value that looks like a local text-file path should fail clearly
        # instead of being passed to the URL parser.
        if candidate.suffix.lower() == ".txt":
            raise ValueError(f"Conference URL file was not found: {candidate}")
        raw_values = [source]

    urls: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        value = raw_value.strip()
        if not value or value.startswith("#"):
            continue

        # Allow an optional inline comment after whitespace + '#'.
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if not value:
            continue

        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(
                f"Invalid conference URL in {candidate if candidate.is_file() else 'input'}: {value}"
            )

        normalized = canonical_url(value)
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)

    if not urls:
        raise ValueError("No conference URLs were found in the input.")
    return urls


def write_json(result: Any, destination: Optional[Path]) -> None:
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


def write_csv(results: Any, destination: Optional[Path]) -> None:
    """Write one conference result or a list of results to a combined CSV."""
    if isinstance(results, dict):
        result_list = [results]
    else:
        result_list = list(results)

    rows = [row for result in result_list for row in flatten_result(result)]
    fieldnames = [
        "conference",
        "conference_slug",
        "conference_url",
        "track",
        "label",
        "start_date",
        "end_date",
        "date_text",
        "timezone",
        "confidence",
        "needs_review",
        "source_url",
        "extracted_at_utc",
    ]

    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        stream = destination.open("w", newline="", encoding="utf-8-sig")
    else:
        stream = sys.stdout

    try:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if destination:
            stream.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract track deadlines from one Researchr conference URL or from "
            "a text file containing one conference URL per line."
        )
    )
    parser.add_argument(
        "source",
        help="Researchr home/track URL, or a .txt file containing one URL per line",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "For a single URL, the output file. For a .txt input, the output "
            "directory containing one file per conference."
        ),
    )
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Polite delay between requests (default: {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--only-tracks-with-dates",
        action="store_true",
        help="Omit tracks whose dates are not yet published or could not be parsed",
    )
    parser.add_argument(
        "--all-important-dates",
        action="store_true",
        help=(
            "Keep every parsed Important Date. By default, only submission-related "
            "deadlines are written."
        ),
    )
    return parser


def output_filename(result: dict[str, Any], output_format: str) -> str:
    """Create a stable filename such as ``msr_2027_deadlines.csv``."""
    safe_slug = re.sub(r"[^A-Za-z0-9]+", "_", result["conference_slug"]).strip("_").lower()
    extension = "csv" if output_format == "csv" else "json"
    return f"{safe_slug}_deadlines.{extension}"


def main(argv: Optional[list[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)
    source_path = Path(args.source).expanduser()
    batch_mode = source_path.is_file()

    try:
        conference_urls = read_conference_urls(args.source)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Input failed: {exc}", file=sys.stderr)
        return 1

    # A text file creates one output file per conference. In that mode,
    # --output names a directory. A single URL keeps the conventional behavior
    # where --output names one file.
    if batch_mode:
        output_directory = args.output or Path("data")
        output_directory.mkdir(parents=True, exist_ok=True)
        single_output: Optional[Path] = None
    else:
        output_directory = None
        single_output = args.output or Path(
            "conference_deadlines.csv"
            if args.format == "csv"
            else "conference_deadlines.json"
        )

    session = make_session()
    successful_outputs: list[Path] = []
    failures: list[tuple[str, str]] = []

    for index, conference_url in enumerate(conference_urls, start=1):
        print(
            f"[{index}/{len(conference_urls)}] Extracting {conference_url}",
            file=sys.stderr,
        )
        try:
            result = extract_conference_deadlines(
                conference_url,
                timeout=args.timeout,
                delay_seconds=max(args.delay, 0),
                include_empty_tracks=not args.only_tracks_with_dates,
                submission_only=not args.all_important_dates,
                session=session,
            )

            destination = (
                output_directory / output_filename(result, args.format)
                if batch_mode and output_directory is not None
                else single_output
            )
            if destination is None:  # defensive; all paths above assign it
                raise OSError("No output destination was selected.")

            if args.format == "csv":
                write_csv(result, destination)
            else:
                write_json(result, destination)

            successful_outputs.append(destination)
            print(
                f"  Found {result['deadline_count']} "
                f"{'submission deadlines' if result['submission_only'] else 'important dates'} across "
                f"{result['track_count']} tracks.",
                file=sys.stderr,
            )
            print(f"  Saved {destination}", file=sys.stderr)
        except (requests.RequestException, ExtractionError, ValueError, OSError) as exc:
            failures.append((conference_url, str(exc)))
            print(f"  Failed: {exc}", file=sys.stderr)

    if not successful_outputs:
        print("Extraction failed for every conference; no output was written.", file=sys.stderr)
        return 1

    if failures:
        print(
            f"Completed with {len(failures)} failed conference(s):",
            file=sys.stderr,
        )
        for url, error in failures:
            print(f"  - {url}: {error}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
