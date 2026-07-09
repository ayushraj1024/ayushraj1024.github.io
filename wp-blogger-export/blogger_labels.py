"""Helpers for Blogger-compatible post labels."""

from __future__ import annotations


def unique_labels(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for label in labels:
        cleaned = label.strip()
        if not cleaned or len(cleaned) > 140:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def sanitize_blogger_labels(labels: list[str], *, max_labels: int = 20) -> list[str]:
    """Drop longer labels that extend a shorter label (e.g. git/git repository)."""
    labels = unique_labels(labels)
    if not labels:
        return []

    remove: set[int] = set()
    for short_index, shorter in enumerate(labels):
        shorter_lower = shorter.casefold()
        for long_index, longer in enumerate(labels):
            if short_index == long_index:
                continue
            longer_lower = longer.casefold()
            if longer_lower.startswith(shorter_lower + " ") and len(longer) > len(shorter):
                remove.add(long_index)

    sanitized = [label for index, label in enumerate(labels) if index not in remove]
    return sanitized[:max_labels]
