"""Streamlit visualization for software-engineering conference deadlines.

Run locally with:
    pip install streamlit streamlit-calendar pandas
    streamlit run app.py

Place one or more ``*_deadlines.csv`` files beside this file or in a
``data/`` directory. CSVs produced by ``deadline_extractor.py`` are loaded
automatically.
"""

from __future__ import annotations

import hashlib
import html
import importlib.util
import re
import sys
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar

from calendar_links import (
    build_calendar_links,
    calendar_timezone_options,
    infer_timezone_name,
    timezone_display_name,
)
from deadline_cleaner import (
    DEADLINE_TYPE_LABELS,
    DEFAULT_DEADLINE_TYPES,
    TRACK_CATEGORIES,
    clean_deadline_frame,
)


st.set_page_config(
    page_title="SE Field Calendar",
    page_icon="📅",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFERENCES_FILE = BASE_DIR / "conferences.txt"
EVENT_SUBMISSION_URL = (
    "https://github.com/ElmiraOn/SE_research_calendar/issues/new/choose"
)
APP_TIMEZONE = ZoneInfo("America/Toronto")
AUTO_REFRESH_AFTER = timedelta(hours=24)

REQUIRED_COLUMNS = {
    "conference",
    "conference_slug",
    "track",
    "label",
    "start_date",
    "end_date",
    "date_text",
    "timezone",
    "source_url",
    "deadline_type",
    "track_category",
    "track_original",
}

# Distinct, readable colors on both light and dark Streamlit themes.
CONFERENCE_PALETTE = (
    "#2563EB",  # blue
    "#EA580C",  # orange
    "#16A34A",  # green
    "#9333EA",  # purple
    "#DB2777",  # pink
    "#0891B2",  # cyan
    "#CA8A04",  # gold
    "#4F46E5",  # indigo
    "#DC2626",  # red
    "#0F766E",  # teal
)


@st.cache_data(show_spinner=False)
def read_deadline_csv(path_string: str, modified_ns: int) -> pd.DataFrame:
    """Read one CSV; ``modified_ns`` invalidates the cache after file changes."""
    del modified_ns
    return pd.read_csv(path_string)


def discover_deadline_csvs() -> list[Path]:
    """Find extracted conference CSVs without crawling unrelated folders."""
    candidates = list(BASE_DIR.glob("*_deadlines.csv"))
    if DATA_DIR.exists():
        candidates.extend(DATA_DIR.glob("*_deadlines.csv"))

    # A file can be found through two paths when data/ is symlinked.
    unique: dict[str, Path] = {}
    for path in candidates:
        if path.is_file():
            unique[str(path.resolve())] = path
    return sorted(unique.values(), key=lambda item: item.name.lower())


def normalize_deadlines(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize data from one or more extractor CSVs."""
    frame = clean_deadline_frame(frame)
    missing = REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise ValueError(
            "Deadline data is missing required columns: " + ", ".join(sorted(missing))
        )

    result = frame.copy()
    text_columns = [
        "conference",
        "conference_slug",
        "track",
        "track_category",
        "track_original",
        "label",
        "date_text",
        "timezone",
        "source_url",
    ]
    for column in text_columns:
        result[column] = result[column].fillna("").astype(str).str.strip()

    result["start_date"] = pd.to_datetime(result["start_date"], errors="coerce").dt.normalize()
    result["end_date"] = pd.to_datetime(result["end_date"], errors="coerce").dt.normalize()

    result = result.dropna(subset=["start_date"])
    result = result[
        (result["conference"] != "")
        & (result["track"] != "")
        & (result["label"] != "")
    ]

    # Ignore malformed ranges rather than generating reversed calendar events.
    reversed_range = result["end_date"].notna() & (
        result["end_date"] < result["start_date"]
    )
    result.loc[reversed_range, "end_date"] = pd.NaT

    result = result.drop_duplicates(
        subset=[
            "conference_slug",
            "track",
            "label",
            "start_date",
            "end_date",
        ],
        keep="last",
    )

    track_columns = ["conference", "conference_slug", "track"]
    first_deadlines = (
        result.groupby(track_columns, as_index=False, dropna=False)["start_date"]
        .min()
        .rename(columns={"start_date": "first_deadline"})
    )
    result = result.merge(first_deadlines, on=track_columns, how="left")

    today = date.today() if APP_TIMEZONE is None else pd.Timestamp.now(APP_TIMEZONE).date()
    result["first_deadline_passed"] = (
        result["first_deadline"].dt.date < today
    )

    return result.sort_values(
        ["start_date", "conference", "track", "label"],
        kind="stable",
    ).reset_index(drop=True)


def load_local_data() -> tuple[list[pd.DataFrame], list[str]]:
    frames: list[pd.DataFrame] = []
    errors: list[str] = []

    for path in discover_deadline_csvs():
        try:
            frames.append(read_deadline_csv(str(path), path.stat().st_mtime_ns))
        except Exception as exc:  # Keep other conferences usable.
            errors.append(f"{path.name}: {exc}")
    return frames, errors


def read_configured_conference_urls() -> tuple[list[str], list[str]]:
    """Read unique URLs from conferences.txt and report invalid entries."""
    if not CONFERENCES_FILE.is_file():
        return [], [f"Conference list was not found: {CONFERENCES_FILE.name}"]

    urls: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(
        CONFERENCES_FILE.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        value = re.split(r"\s+#", raw_line, maxsplit=1)[0].strip()
        if not value or value.startswith("#"):
            continue
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"{CONFERENCES_FILE.name}:{line_number}: invalid URL: {value}")
            continue
        normalized = value.rstrip("/")
        if normalized not in seen:
            seen.add(normalized)
            urls.append(normalized)
    return urls, errors


def cache_path_for_conference_url(url: str) -> Path:
    """Return the extractor's stable CSV path for a Researchr home URL."""
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    safe_slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_").lower()
    return DATA_DIR / f"{safe_slug}_deadlines.csv"


def automatic_refresh_due(urls: list[str], now: Optional[datetime] = None) -> bool:
    """Refresh when a configured cache is absent or at least 24 hours old."""
    if not urls:
        return False
    checked_at = now or datetime.now(APP_TIMEZONE)
    for url in urls:
        cache_path = cache_path_for_conference_url(url)
        if not cache_path.is_file():
            return True
        modified_at = datetime.fromtimestamp(cache_path.stat().st_mtime, APP_TIMEZONE)
        if checked_at - modified_at >= AUTO_REFRESH_AFTER:
            return True
    return False


def refresh_configured_conferences(
    urls: list[str],
    extractor: Callable[..., dict[str, Any]],
    flattener: Callable[[dict[str, Any]], list[dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    """Extract configured conferences and atomically replace successful caches."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    refreshed: list[str] = []
    errors: list[str] = []

    for url in urls:
        try:
            # Cache every Important Date. The cleaner assigns stable types and
            # the calendar controls which types are visible.
            result = extractor(url, submission_only=False)
            rows = flattener(result)
            if not rows:
                raise ValueError("the extractor returned no deadlines")

            destination = cache_path_for_conference_url(url)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            clean_deadline_frame(pd.DataFrame(rows)).to_csv(
                temporary, index=False, encoding="utf-8-sig"
            )
            temporary.replace(destination)
            refreshed.append(str(result.get("conference", result.get("conference_slug", url))))
        except Exception as exc:
            # Never remove the previous cache: stale data is better than no data.
            errors.append(f"{url}: {exc}")

    return refreshed, errors


def load_extractor() -> tuple[
    Optional[Callable[..., dict[str, Any]]],
    Optional[Callable[[dict[str, Any]], list[dict[str, Any]]]],
]:
    """Load the current extractor, with a fallback for alternate filenames."""
    try:
        from deadline_extractor_v2 import (
            extract_conference_deadlines,
            flatten_result,
        )

        return extract_conference_deadlines, flatten_result
    except Exception:
        pass

    candidates = sorted(
        BASE_DIR.glob("deadline_extractor*.py"),
        key=lambda path: (path.name != "deadline_extractor_v2.py", path.name),
    )
    for candidate in candidates:
        if candidate.name == Path(__file__).name:
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                "se_field_calendar_deadline_extractor", candidate
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module.extract_conference_deadlines, module.flatten_result
        except Exception:
            continue

    return None, None


def assign_conference_colors(conferences: list[str]) -> dict[str, str]:
    """Assign one consistent color to each conference in the loaded dataset."""
    return {
        conference: CONFERENCE_PALETTE[index % len(CONFERENCE_PALETTE)]
        for index, conference in enumerate(sorted(conferences, key=str.casefold))
    }


def make_calendar_events(
    frame: pd.DataFrame,
    color_by_conference: dict[str, str],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    for row in frame.itertuples(index=False):
        color = color_by_conference[row.conference]
        start = row.start_date.date()
        end = row.end_date.date() if pd.notna(row.end_date) else None

        event: dict[str, Any] = {
            "title": f"{row.conference} · {row.track_category}: {row.label}",
            "start": start.isoformat(),
            "allDay": True,
            "backgroundColor": color,
            "borderColor": color,
            "textColor": "#FFFFFF",
            "extendedProps": {
                "conference": row.conference,
                "track": row.track_original,
                "trackCategory": row.track_category,
                "deadlineLabel": row.label,
                "deadlineType": DEADLINE_TYPE_LABELS.get(
                    row.deadline_type, row.deadline_type
                ),
                "displayDate": row.date_text,
                "startDate": start.isoformat(),
                "deadlineTime": row.deadline_time,
                "timezone": row.timezone or "Not specified",
                "sourceUrl": row.source_url,
                "firstDeadline": row.first_deadline.date().isoformat(),
                "firstDeadlinePassed": bool(row.first_deadline_passed),
            },
        }

        # FullCalendar treats an all-day event's end as exclusive.
        if end is not None:
            event["end"] = (end + timedelta(days=1)).isoformat()

        events.append(event)

    return events


def initial_calendar_date(frame: pd.DataFrame) -> str:
    today = pd.Timestamp.now(APP_TIMEZONE).normalize().tz_localize(None)
    upcoming = frame.loc[frame["start_date"] >= today, "start_date"]
    selected = upcoming.min() if not upcoming.empty else frame["start_date"].min()
    return selected.date().isoformat()


def render_conference_legend(
    conferences: list[str],
    color_by_conference: dict[str, str],
) -> None:
    chips = []
    for conference in sorted(conferences, key=str.casefold):
        safe_name = html.escape(conference)
        color = color_by_conference[conference]
        chips.append(
            f"""
            <span class="conference-chip">
                <span class="conference-dot" style="background:{color};"></span>
                {safe_name}
            </span>
            """
        )

    st.markdown(
        """
        <style>
            .conference-legend {
                display: flex;
                flex-wrap: wrap;
                gap: 0.5rem;
                margin: 0.15rem 0 0.85rem 0;
            }
            .conference-chip {
                display: inline-flex;
                align-items: center;
                gap: 0.42rem;
                padding: 0.28rem 0.65rem;
                border: 1px solid rgba(128, 128, 128, 0.30);
                border-radius: 999px;
                font-size: 0.86rem;
                line-height: 1.2;
            }
            .conference-dot {
                width: 0.72rem;
                height: 0.72rem;
                border-radius: 50%;
                display: inline-block;
                flex: 0 0 auto;
            }
        </style>
        <div class="conference-legend">"""
        + "".join(chips)
        + "</div>",
        unsafe_allow_html=True,
    )


def render_clicked_event(calendar_state: dict[str, Any]) -> None:
    """Show details defensively because callback payloads vary by component version."""
    event_click = calendar_state.get("eventClick")
    if not isinstance(event_click, dict):
        return

    clicked = event_click.get("event", event_click)
    if not isinstance(clicked, dict):
        return

    props = clicked.get("extendedProps", {})
    if not isinstance(props, dict) or not props:
        return

    with st.container(border=True):
        st.subheader(props.get("deadlineLabel", "Deadline"))
        st.write(f"**Conference:** {props.get('conference', '—')}")
        st.write(f"**Category:** {props.get('trackCategory', '—')}")
        st.write(f"**Original track:** {props.get('track', '—')}")
        st.write(f"**Type:** {props.get('deadlineType', '—')}")
        st.write(f"**Date:** {props.get('displayDate', clicked.get('start', '—'))}")
        st.write(f"**Timezone:** {props.get('timezone', '—')}")
        source_url = props.get("sourceUrl")
        if source_url:
            st.link_button("Open source page", source_url)

        raw_start_date = str(props.get("startDate") or clicked.get("start") or "")[:10]
        try:
            default_date = date.fromisoformat(raw_start_date)
        except ValueError:
            default_date = date.today()

        source_timezone = str(props.get("timezone", ""))
        inferred_timezone = infer_timezone_name(source_timezone)
        timezone_options = calendar_timezone_options()
        timezone_index = (
            timezone_options.index(inferred_timezone)
            if inferred_timezone in timezone_options
            else None
        )
        event_identity = "|".join(
            (
                str(props.get("conference", "")),
                str(props.get("track", "")),
                str(props.get("deadlineLabel", "")),
                raw_start_date,
            )
        )
        event_key = hashlib.sha1(event_identity.encode("utf-8")).hexdigest()[:12]

        published_time = str(props.get("deadlineTime", "")).strip()
        try:
            default_time = clock_time.fromisoformat(published_time)
        except ValueError:
            default_time = clock_time(23, 59)

        st.markdown("#### Add to calendar")
        st.caption(
            "Confirm the source deadline's date, time, and time zone before "
            "opening your calendar. AoE deadlines default to 23:59 UTC−12:00."
        )
        with st.form(f"calendar_export_{event_key}"):
            date_column, time_column, timezone_column = st.columns([1, 1, 2])
            with date_column:
                selected_date = st.date_input(
                    "Date *", value=default_date, key=f"export_date_{event_key}"
                )
            with time_column:
                selected_time = st.time_input(
                    "Time *",
                    value=default_time,
                    step=60,
                    key=f"export_time_{event_key}",
                )
            with timezone_column:
                selected_timezone = st.selectbox(
                    "Time zone *",
                    options=timezone_options,
                    index=timezone_index,
                    format_func=timezone_display_name,
                    placeholder="Select the source time zone",
                    key=f"export_timezone_{event_key}",
                )
            submitted = st.form_submit_button("Prepare calendar links", type="primary")

        if submitted:
            if selected_timezone is None:
                st.error("Select a time zone before adding this deadline.")
            else:
                event_title = (
                    f"{props.get('conference', 'Conference')} — "
                    f"{props.get('trackCategory', 'Track')}: "
                    f"{props.get('deadlineLabel', 'Deadline')}"
                )
                description = (
                    f"Original track: {props.get('track', 'Not specified')}\n"
                    f"Deadline type: {props.get('deadlineType', 'Not specified')}\n"
                    f"Published timezone: {source_timezone or 'Not specified'}"
                )
                try:
                    links = build_calendar_links(
                        title=event_title,
                        event_date=selected_date,
                        event_time=selected_time,
                        timezone_name=selected_timezone,
                        description=description,
                        source_url=str(source_url or ""),
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    google_column, outlook_column = st.columns(2)
                    with google_column:
                        st.link_button(
                            "Add to Google Calendar",
                            links["google"],
                            use_container_width=True,
                        )
                    with outlook_column:
                        st.link_button(
                            "Add to Outlook",
                            links["outlook"],
                            use_container_width=True,
                        )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("SE Trackline")
st.caption(
    "Conference deadlines in one calendar. Each conference keeps the same color "
    "across all of its tracks. Only contains deadlines extracted after July 12, 2026. " \
    "If the conference is not included please contact the author via GitHub or email to add it. " \
    "Deadline data is refreshed automatically from the configured conference list."
)

if "extracted_deadline_frames" not in st.session_state:
    st.session_state.extracted_deadline_frames = {}

extract_conference_deadlines, flatten_result = load_extractor()
configured_urls, configuration_errors = read_configured_conference_urls()
refresh_now = st.sidebar.button(
    "Refresh deadline data now",
    type="primary",
    disabled=extract_conference_deadlines is None or flatten_result is None,
    help="Fetch every conference in conferences.txt and update its cached CSV.",
)
st.sidebar.link_button(
    "Add Your Event",
    EVENT_SUBMISSION_URL,
    use_container_width=True,
    help="Submit a conference deadline for manual review on GitHub.",
)
st.sidebar.caption(
    "Submissions are reviewed manually. Submitting an event does not guarantee approval."
)

with st.sidebar:
    st.divider()
    st.subheader("About the author")
    st.markdown(
        "**Elmira Onagh**  \n"
        "York University  \n"
        "[onaghelmira@gmail.com](mailto:onaghelmira@gmail.com)  \n"
        "[LinkedIn](https://www.linkedin.com/in/elmiraonagh/)"
    )

refresh_due = automatic_refresh_due(configured_urls)
automatic_check_needed = not st.session_state.get("automatic_refresh_checked", False)
should_refresh = refresh_now or (refresh_due and automatic_check_needed)
refresh_errors: list[str] = []

if should_refresh and extract_conference_deadlines is not None and flatten_result is not None:
    # A failed automatic attempt should not repeat on every widget interaction.
    st.session_state.automatic_refresh_checked = True
    with st.spinner(f"Refreshing deadlines for {len(configured_urls)} conferences…"):
        refreshed, refresh_errors = refresh_configured_conferences(
            configured_urls, extract_conference_deadlines, flatten_result
        )
    if refreshed:
        st.success(
            f"Updated {len(refreshed)} conference{'s' if len(refreshed) != 1 else ''}: "
            + ", ".join(refreshed)
        )
    if refresh_now and not refreshed and not refresh_errors:
        st.info("No conferences are configured in conferences.txt.")

local_frames, load_errors = load_local_data()
# with st.expander("Add or refresh a Researchr conference", expanded=False):
#     conference_url = st.text_input(
#         "Conference home URL",
#         placeholder="https://conf.researchr.org/home/fse-2027",
#     )
#     load_button = st.button(
#         "Load conference",
#         type="primary",
#         disabled=extract_conference_deadlines is None or flatten_result is None,
#     )

#     if extract_conference_deadlines is None or flatten_result is None:
#         st.caption(
#             "Live extraction is unavailable. Keep deadline_extractor.py beside app.py "
#             "or place an extracted *_deadlines.csv file in this folder."
#         )

#     if load_button:
#         if not conference_url.strip():
#             st.warning("Enter a Researchr conference URL.")
#         else:
#             try:
#                 with st.spinner("Extracting track deadlines…"):
#                     extracted = extract_conference_deadlines(conference_url.strip())
#                     extracted_frame = pd.DataFrame(flatten_result(extracted))
#                     slug = str(extracted.get("conference_slug", conference_url.strip()))
#                     st.session_state.extracted_deadline_frames[slug] = extracted_frame
#                 st.success(
#                     f"Loaded {extracted.get('conference', slug)} with "
#                     f"{len(extracted_frame)} deadlines."
#                 )
#             except Exception as exc:
#                 st.error(f"Could not extract that conference: {exc}")

all_frames = list(local_frames) + list(
    st.session_state.extracted_deadline_frames.values()
)

all_data_errors = configuration_errors + load_errors + refresh_errors
if all_data_errors:
    with st.expander("Data-loading warnings"):
        for message in all_data_errors:
            st.warning(message)

if not all_frames:
    st.info(
        "No deadline data was found. Place an extractor-generated "
        "`*_deadlines.csv` file beside `app.py` or in a `data/` directory."
    )
    st.stop()

try:
    deadlines = normalize_deadlines(pd.concat(all_frames, ignore_index=True))
except ValueError as exc:
    st.error(str(exc))
    st.stop()

if deadlines.empty:
    st.warning("The loaded files do not contain any valid deadline dates.")
    st.stop()

all_conferences = sorted(deadlines["conference"].unique().tolist(), key=str.casefold)
color_by_conference = assign_conference_colors(all_conferences)

# Filters are deliberately placed immediately above the single calendar.
conference_col, type_col, track_col = st.columns([1.0, 1.2, 2.5])
with conference_col:
    selected_conferences = st.multiselect(
        "Conferences",
        options=all_conferences,
        default=all_conferences,
        placeholder="Choose conferences",
    )

conference_filtered = deadlines[
    deadlines["conference"].isin(selected_conferences)
].copy()

available_deadline_types = sorted(
    conference_filtered["deadline_type"].unique().tolist(),
    key=lambda value: DEADLINE_TYPE_LABELS.get(value, value).casefold(),
)
default_deadline_types = [
    value for value in DEFAULT_DEADLINE_TYPES if value in available_deadline_types
]
with type_col:
    selected_deadline_types = st.multiselect(
        "Deadline types",
        options=available_deadline_types,
        default=default_deadline_types,
        format_func=lambda value: DEADLINE_TYPE_LABELS.get(value, value),
        placeholder="Choose deadline types",
    )

conference_filtered = conference_filtered[
    conference_filtered["deadline_type"].isin(selected_deadline_types)
].copy()

available_track_categories = [
    category
    for category in TRACK_CATEGORIES
    if category in set(conference_filtered["track_category"])
]

with track_col:
    selected_track_categories = st.multiselect(
        "Track categories",
        options=available_track_categories,
        default=available_track_categories,
        placeholder="Choose track categories",
    )

# with status_col:
#     first_deadline_filter = st.selectbox(
#         "First deadline",
#         options=(
#             "All tracks",
#             "Not passed",
#             "Passed",
#         ),
#         help=(
#             "A track is marked passed when its earliest extracted deadline is "
#             "before today. A deadline occurring today is not treated as passed."
#         ),
#     )

filtered = conference_filtered[
    conference_filtered["track_category"].isin(selected_track_categories)
].copy()

# if first_deadline_filter == "Not passed":
#     filtered = filtered[~filtered["first_deadline_passed"]]
# elif first_deadline_filter == "Passed":
#     filtered = filtered[filtered["first_deadline_passed"]]

metric_conferences, metric_tracks, metric_deadlines = st.columns(3)
metric_conferences.metric("Conferences", filtered["conference"].nunique())
metric_tracks.metric(
    "Tracks",
    filtered[["conference_slug", "track"]].drop_duplicates().shape[0],
)
metric_deadlines.metric("Deadlines", len(filtered))

visible_conferences = filtered["conference"].unique().tolist()
# if visible_conferences:
#     render_conference_legend(visible_conferences, color_by_conference)

if filtered.empty:
    st.info("No deadlines match the selected filters.")
    st.stop()

events = make_calendar_events(filtered, color_by_conference)
calendar_options = {
    "initialView": "dayGridMonth",
    "initialDate": initial_calendar_date(filtered),
    "firstDay": 1,
    "height": 1100,
    # "contentHeight": 900,
    "navLinks": True,
    "dayMaxEvents": False,
    "eventDisplay": "block",
    "displayEventTime": False,
    "fixedWeekCount": False,
    "multiMonthMaxColumns": 3,
    "multiMonthMinWidth": 260,
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "multiMonthYear,dayGridMonth,dayGridWeek,listMonth",
    },
    "buttonText": {
        "today": "Today",
        "year": "Year",
        "month": "Month",
        "week": "Week",
        "list": "List",
    },
}

# calendar_state = calendar(
#     events=events,
#     options=calendar_options,
#     custom_css="""
#         .fc .fc-toolbar-title {
#             font-size: 1.45rem;
#             font-weight: 700;
#         }
#         .fc .fc-button {
#             border-radius: 0.45rem;
#         }
#         .fc .fc-daygrid-event {
#             border-radius: 0.35rem;
#             padding: 0.08rem 0.22rem;
#             cursor: pointer;
#             white-space: normal;
#         }
#         .fc .fc-event-title {
#             font-weight: 600;
#             line-height: 1.25;
#         }
#         .fc .fc-event-past {
#             opacity: 0.58;
#         }
#         .fc .fc-day-today {
#             background: rgba(37, 99, 235, 0.08) !important;
#         }
#     """,
#     key="conference_deadline_calendar",
# )
with st.container(height=750, border=False):
    calendar_state = calendar(
        events=events,
        options=calendar_options,
        key="conference_deadline_calendar",
    )
st.caption(
    "Past events are faded. Click a deadline to inspect its details or open the "
    "original Researchr track page."
)
render_clicked_event(calendar_state or {})
