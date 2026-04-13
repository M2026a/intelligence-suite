from __future__ import annotations

SOURCE_TYPE_WEIGHT = {
    "rss": 3,
    "html": 2,
}

RECENCY_WEIGHT_RULES = [
    (1, 6),
    (3, 5),
    (7, 4),
    (14, 3),
    (30, 2),
    (60, 1),
]

SIGNAL_STRENGTH_THRESHOLDS = {
    "very_high": 18,
    "high": 13,
    "medium": 8,
    "low": 4,
}
