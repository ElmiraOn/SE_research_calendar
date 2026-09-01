"""Normalize extracted Researchr dates into a stable calendar schema."""

from __future__ import annotations

import re
from typing import Final

import pandas as pd


DEADLINE_TYPE_LABELS: Final[dict[str, str]] = {
    "abstract_submission": "Abstract submission",
    "paper_submission": "Full paper submission",
    "other_submission": "Other submission",
    "artifact_submission": "Artifact submission",
    "notification": "Notification",
    "camera_ready": "Camera-ready",
    "author_response": "Author response / rebuttal",
    "registration": "Registration",
    "conference_date": "Conference date",
    "other": "Other date",
}

DEFAULT_DEADLINE_TYPES: Final[tuple[str, ...]] = (
    "abstract_submission",
    "paper_submission",
)

TRACK_CATEGORIES: Final[tuple[str, ...]] = (
    "Research",
    "Industry",
    "New Ideas & Emerging Results",
    "Reflections & Negative Results",
    "Journal First",
    "Registered Reports",
    "Artifacts",
    "Tools, Demos & Data",
    "Doctoral Symposium",
    "Education",
    "Workshops",
    "Tutorials",
    "Workshops & Tutorials",
    "Short Papers & Posters",
    "Student Research Competition",
    "Mentoring & Community",
    "Competitions & Challenges",
    "Society",
    "Other",
)

CONFERENCE_NAMES: Final[dict[str, str]] = {
    "ast-2027": "AST 2027",
    "cain-2027": "CAIN 2027",
    "chase-2027": "CHASE 2027",
    "eseiw-2026": "ESEIW 2026",
    "fse-2027": "FSE 2027",
    "icse-2027": "ICSE 2027",
    "msr-2027": "MSR 2027",
    "re-2026": "RE 2026",
    "saner-2027": "SANER 2027",
}


def _contains(value: str, pattern: str) -> bool:
    return re.search(pattern, value, flags=re.IGNORECASE) is not None


def classify_deadline(label: str, track: str = "") -> str:
    """Classify a raw Important Dates label, using the track for context."""
    text = " ".join(str(label).split())
    context = f"{track} {text}"

    if _contains(text, r"\b(?:notification|acceptance|decision|outcome)\b"):
        return "notification"
    if _contains(text, r"\b(?:camera[ -]?ready|final (?:version|copy))\b"):
        return "camera_ready"
    if _contains(text, r"\b(?:rebuttal|author response|response period|discussion)\b"):
        return "author_response"
    if _contains(text, r"\bregistration\b"):
        return "registration"
    if _contains(text, r"\babstract\b"):
        return "abstract_submission"
    if _contains(text, r"\bparticipate in\b") or _contains(
        text,
        r"\b(?:conference|symposium|workshop|session|event)\b.*\b(?:date|day|start|end|session)\b",
    ):
        return "conference_date"
    if _contains(context, r"\bartifact(?:s| evaluation)?\b") and _contains(
        text, r"\b(?:submission|registration|deadline|due)\b"
    ):
        return "artifact_submission"
    if _contains(
        context,
        r"\b(?:proposal|demo(?:nstration)?|poster|tutorial|competition|doctoral|registered report|student volunteers?|open data)\b",
    ) and _contains(text, r"\b(?:submission|submit|deadline|due|proposal)\b"):
        return "other_submission"
    if _contains(
        text,
        r"\b(?:full paper|paper submission|submission (?:deadline|due)|deadline for submissions|manuscript)\b",
    ) or text.casefold() in {"submission", "submission deadline", "paper deadline"}:
        return "paper_submission"
    if _contains(text, r"\b(?:submission|submit|deadline|due|proposal)\b"):
        return "other_submission"
    if _contains(
        text,
        r"\b(?:conference|symposium|workshop|session|event|presentation|program)\b",
    ):
        return "conference_date"
    return "other"


def normalize_conference_name(conference: str, conference_slug: str) -> str:
    """Return a stable conference label, preferring the URL-derived slug."""
    slug = " ".join(str(conference_slug).split()).casefold()
    if slug in CONFERENCE_NAMES:
        return CONFERENCE_NAMES[slug]

    name = " ".join(str(conference).split())
    if name.casefold().rstrip("!") not in {"", "welcome", "home"}:
        return name

    match = re.fullmatch(r"([a-z][a-z0-9-]*?)-(20\d{2})", slug)
    if match:
        acronym = match.group(1).replace("-", " ").upper()
        return f"{acronym} {match.group(2)}"
    return name or str(conference_slug)


def classify_track(track: str, source_url: str = "") -> str:
    """Map conference-specific track wording to a shared primary category."""
    name = " ".join(str(track).split())
    context = f"{name} {source_url}".casefold()

    # Order matters for compound names (for example, Industry Challenge).
    if _contains(context, r"\b(?:industry|industrial|seip)\b"):
        return "Industry"
    if _contains(context, r"\b(?:student research competition|\bsrc\b)"):
        return "Student Research Competition"
    if _contains(context, r"\b(?:student volunteers?|student mentoring|shadow research|junior pc)\b"):
        return "Mentoring & Community"
    if _contains(context, r"\bdoctoral\b|\bidoese\b|\bdecs\b"):
        return "Doctoral Symposium"
    if _contains(context, r"\bartifact"):
        return "Artifacts"
    if _contains(context, r"\bjournal[ -]?first\b"):
        return "Journal First"
    if _contains(context, r"\bregistered reports?\b|\brr track\b"):
        return "Registered Reports"
    if _contains(context, r"\b(?:new ideas|emerging results|vision|early research achievement|\bnier\b|re@next)\b"):
        return "New Ideas & Emerging Results"
    if _contains(context, r"\b(?:reflection|negative results?|reproducibility studies|\brene\b)\b"):
        return "Reflections & Negative Results"
    if _contains(context, r"\b(?:education|training|\bseet\b)\b"):
        return "Education"
    if _contains(context, r"\b(?:society|\bseis\b)\b"):
        return "Society"
    if _contains(context, r"\bworkshops?\b") and _contains(context, r"\btutorials?\b"):
        return "Workshops & Tutorials"
    if _contains(context, r"\bworkshops?\b"):
        return "Workshops"
    if _contains(context, r"\btutorials?\b|technical briefings?"):
        return "Tutorials"
    if _contains(context, r"\b(?:tool|demo|demonstration|data showcase|open data)\b"):
        return "Tools, Demos & Data"
    if _contains(context, r"\b(?:short papers?|posters?)\b"):
        return "Short Papers & Posters"
    if _contains(context, r"\b(?:competition|challenge|hackathon)\b"):
        return "Competitions & Challenges"
    if _contains(context, r"\b(?:research|technical (?:papers?|track)|papers?|call for papers|agentic ai4se)\b"):
        return "Research"
    # A single-track conference often uses the conference title as its track.
    if "/home/" in context:
        return "Research"
    return "Other"


def clean_deadline_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with normalized text and a deterministic deadline_type."""
    result = frame.copy()
    if "deadline_time" not in result.columns:
        result["deadline_time"] = ""
    for column in (
        "conference",
        "conference_slug",
        "track",
        "label",
        "source_url",
        "deadline_time",
    ):
        if column in result.columns:
            result[column] = result[column].fillna("").astype(str).str.strip()

    if "label" not in result.columns:
        return result

    if {"conference", "conference_slug"}.issubset(result.columns):
        result["conference"] = [
            normalize_conference_name(name, slug)
            for name, slug in zip(result["conference"], result["conference_slug"])
        ]

    tracks = result["track"] if "track" in result.columns else pd.Series("", index=result.index)
    source_urls = (
        result["source_url"]
        if "source_url" in result.columns
        else pd.Series("", index=result.index)
    )
    result["track_original"] = tracks
    result["track_category"] = [
        classify_track(track, source_url)
        for track, source_url in zip(tracks, source_urls)
    ]

    result["deadline_type"] = [
        classify_deadline(label, track)
        for label, track in zip(result["label"], tracks)
    ]
    return result
