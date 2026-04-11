from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from config.weights import RECENCY_WEIGHT_RULES, SIGNAL_STRENGTH_THRESHOLDS, SOURCE_TYPE_WEIGHT


def recency_weight(published_iso: str) -> int:
    try:
        published = datetime.fromisoformat(published_iso.replace("Z", "+00:00"))
    except Exception:
        return 0
    now = datetime.now(timezone.utc)
    days = max(0, (now - published.astimezone(timezone.utc)).days)
    for day_limit, weight in RECENCY_WEIGHT_RULES:
        if days <= day_limit:
            return weight
    return 0


def score_article(article: dict[str, Any]) -> dict[str, Any]:
    source_type = article.get("source_type", "html")
    base = article.get("source_weight", 0) + SOURCE_TYPE_WEIGHT.get(source_type, 0)
    pos = len(article.get("positive_hits", []))
    neg = len(article.get("negative_hits", []))
    ent = len(article.get("entity_hits", []))
    tgt = len(article.get("target_hits", []))
    hit_bonus = pos + neg + ent + tgt
    freshness = recency_weight(article.get("published", ""))
    impact_bonus = {"High": 5, "Medium": 3, "Low": 1}.get(article.get("impact_label", "Low"), 0)
    balance_penalty = 1 if pos > 0 and neg > 0 else 0
    article["score"] = float(base + hit_bonus + freshness + impact_bonus - balance_penalty)
    return article


def strength_from_score(score: float) -> int:
    if score >= SIGNAL_STRENGTH_THRESHOLDS["very_high"]:
        return 5
    if score >= SIGNAL_STRENGTH_THRESHOLDS["high"]:
        return 4
    if score >= SIGNAL_STRENGTH_THRESHOLDS["medium"]:
        return 3
    if score >= SIGNAL_STRENGTH_THRESHOLDS["low"]:
        return 2
    return 1
