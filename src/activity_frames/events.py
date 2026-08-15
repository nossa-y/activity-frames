"""Minimal external event abstraction for deterministic segmentation.

Events are authoritative, externally supplied markers (e.g. git.merge,
pr.approved) that callers may pass to the sessionizer to force
deterministic boundaries. The class is intentionally minimal and
frozen to preserve immutability across the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass



@dataclass(frozen=True)
class Event:
    """An externally supplied, authoritative boundary marker.

    The caller decides what is meaningful; this package does not infer or
    classify such events from activity data.
    """

    event_type: str
    timestamp: float
    source: str
    priority: int = 0
