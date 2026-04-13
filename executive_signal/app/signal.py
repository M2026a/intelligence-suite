
from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.score import strength_from_score

MODULE_TARGET_FALLBACK = {
    "company": "Key Companies",
    "price": "Market Prices",
    "supply_chain": "Supply Chain",
    "failure": "Incidents",
    "earnings": "Earnings",
    "geo_risk": "Policy / Geo",
    "talent": "Talent Market",
    "product": "Product Launches",
}


def infer_target(article: dict[str, Any]) -> str:
    entities = list(dict.fromkeys(article.get("entity_hits") or []))
    targets = list(dict.fromkeys(article.get("target_hits") or []))
    module = article.get("module")
    if module == "company" and entities:
        return entities[0]
    if entities:
        return entities[0]
    if targets:
        return targets[0]
    return MODULE_TARGET_FALLBACK.get(module, str(module).title())


def infer_direction(group: list[dict[str, Any]]) -> tuple[str, float]:
    positive = sum(len(a.get("positive_hits", [])) + (a.get("score", 0) / 10.0) for a in group)
    negative = sum(len(a.get("negative_hits", [])) + (a.get("score", 0) / 10.0) for a in group if a.get("negative_hits"))
    delta = positive - negative
    if delta > 1.2:
        return "UP", delta
    if delta < -1.2:
        return "DOWN", delta
    return "NEUTRAL", delta


def build_signals(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        key = (article["module"], infer_target(article))
        buckets[key].append(article)

    signals: list[dict[str, Any]] = []
    for (module, target), group in buckets.items():
        group = sorted(group, key=lambda x: x.get("score", 0), reverse=True)
        direction, delta = infer_direction(group)
        avg_score = sum(x.get("score", 0) for x in group) / max(1, len(group))
        top = group[0]
        reasons = []
        for key in ["positive_hits", "negative_hits", "entity_hits", "target_hits"]:
            reasons.extend(top.get(key, []))
        if not reasons:
            reasons.append(top.get("title", ""))
        signals.append(
            {
                "module": module,
                "type": module,
                "target": target,
                "direction": direction,
                "strength": strength_from_score(avg_score + abs(delta)),
                "reason": ", ".join(dict.fromkeys(reasons))[:240],
                "score": round(avg_score, 2),
                "article_count": len(group),
            }
        )
    return sorted(signals, key=lambda x: (-x["strength"], -x["score"], x["module"], x["target"]))
