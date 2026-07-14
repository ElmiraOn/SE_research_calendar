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
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar


st.set_page_config(
    page_title="SE Field Calendar",
    page_icon="📅",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
APP_TIMEZONE = ZoneInfo("America/Toronto")

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


def load_extractor() -> tuple[
    Optional[Callable[..., dict[str, Any]]],
    Optional[Callable[[dict[str, Any]], list[dict[str, Any]]]],
]:
    """Load the extractor normally, with a fallback for duplicate filenames."""
    try:
        from ignore.deadline_extractor import (  # type: ignore
            extract_conference_deadlines,
            flatten_result,
        )

        return extract_conference_deadlines, flatten_result
    except Exception:
        pass

    for candidate in sorted(BASE_DIR.glob("deadline_extractor*.py")):
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


def track_identifier(conference_slug: str, track: str) -> str:
    return f"{conference_slug}\u241f{track}"


def track_display_name(conference: str, track: str) -> str:
    return f"{conference} — {track}"


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
            "title": f"{row.conference} · {row.track}: {row.label}",
            "start": start.isoformat(),
            "allDay": True,
            "backgroundColor": color,
            "borderColor": color,
            "textColor": "#FFFFFF",
            "extendedProps": {
                "conference": row.conference,
                "track": row.track,
                "deadlineLabel": row.label,
                "displayDate": row.date_text,
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
        st.write(f"**Track:** {props.get('track', '—')}")
        st.write(f"**Date:** {props.get('displayDate', clicked.get('start', '—'))}")
        st.write(f"**Timezone:** {props.get('timezone', '—')}")
        source_url = props.get("sourceUrl")
        if source_url:
            st.link_button("Open source page", source_url)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

st.title("SE Trackline")
st.caption(
    "Conference deadlines in one calendar. Each conference keeps the same color "
    "across all of its tracks. Only contains deadlines extracted after July 12, 2026. " \
    "If the conference is not included please contact the author via GitHub or email to add it. " \
    "The calendar is updated every first saturday of the month."
)

local_frames, load_errors = load_local_data()
if "extracted_deadline_frames" not in st.session_state:
    st.session_state.extracted_deadline_frames = {}

extract_conference_deadlines, flatten_result = load_extractor()
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

if load_errors:
    with st.expander("Data-loading warnings"):
        for message in load_errors:
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
conference_col, track_col= st.columns([1.0, 3])
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

track_rows = (
    conference_filtered[["conference", "conference_slug", "track"]]
    .drop_duplicates()
    .sort_values(["conference", "track"], key=lambda series: series.str.casefold())
)
track_options: dict[str, str] = {
    track_display_name(row.conference, row.track): track_identifier(
        row.conference_slug, row.track
    )
    for row in track_rows.itertuples(index=False)
}
track_widget_key = "track_filter_" + hashlib.sha1(
    "|".join(selected_conferences).encode("utf-8")
).hexdigest()[:10]

with track_col:
    selected_track_names = st.multiselect(
        "Tracks",
        options=list(track_options.keys()),
        default=list(track_options.keys()),
        placeholder="Choose tracks",
        key=track_widget_key,
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

selected_track_ids = {track_options[name] for name in selected_track_names}
filtered = conference_filtered[
    conference_filtered.apply(
        lambda row: track_identifier(row["conference_slug"], row["track"])
        in selected_track_ids,
        axis=1,
    )
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
    "headerToolbar": {
        "left": "prev,next today",
        "center": "title",
        "right": "dayGridMonth,listMonth",
    },
    "buttonText": {
        "today": "Today",
        "month": "Month",
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
