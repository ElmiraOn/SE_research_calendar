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


def clean_deadline_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with normalized text and a deterministic deadline_type."""
    result = frame.copy()
    for column in ("conference", "conference_slug", "track", "label"):
        if column in result.columns:
            result[column] = result[column].fillna("").astype(str).str.strip()

    if "label" not in result.columns:
        return result

    tracks = result["track"] if "track" in result.columns else pd.Series("", index=result.index)
    result["deadline_type"] = [
        classify_deadline(label, track)
        for label, track in zip(result["label"], tracks)
    ]
    return result
