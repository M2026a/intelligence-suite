from __future__ import annotations

from typing import Any

from config.keywords import MODULE_KEYWORDS


def _count_hits(blob: str, keywords: dict[str, list[str]]) -> tuple[int, int, int, int]:
    pos = len([w for w in keywords.get("positive", []) if w.lower() in blob])
    neg = len([w for w in keywords.get("negative", []) if w.lower() in blob])
    ent = len([w for w in keywords.get("entities", []) if w.lower() in blob])
    tgt = len([w for w in keywords.get("targets", []) if w.lower() in blob])
    return pos, neg, ent, tgt


def infer_best_module(article: dict[str, Any]) -> str:
    blob = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    current = article.get("module", "")
    best_module = current
    best_score = -1
    current_score = -1
    for module, keywords in MODULE_KEYWORDS.items():
        pos, neg, ent, tgt = _count_hits(blob, keywords)
        score = (pos + neg) + (ent * 2) + (tgt * 2)
        if module == current:
            current_score = score
        if score > best_score:
            best_score = score
            best_module = module
    if best_score <= 0:
        return current
    if current_score >= 0 and current_score + 1 >= best_score:
        return current
    return best_module


def detect_language(text: str) -> str:
    for ch in text:
        code = ord(ch)
        if 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF:
            return "ja"
    return "en"


def determine_impact(article: dict[str, Any]) -> str:
    pos = len(article.get("positive_hits", []))
    neg = len(article.get("negative_hits", []))
    ent = len(article.get("entity_hits", []))
    tgt = len(article.get("target_hits", []))
    hit_count = pos + neg + ent + tgt
    if article.get("source_weight", 0) >= 5 and hit_count >= 3:
        return "High"
    if article.get("source_weight", 0) >= 4 and hit_count >= 2:
        return "High"
    if hit_count >= 2:
        return "Medium"
    if hit_count >= 1:
        return "Low"
    return "Low"


def tag_article(article: dict[str, Any]) -> dict[str, Any]:
    module = article["module"]
    keywords = MODULE_KEYWORDS.get(module, {})
    blob = f"{article.get('title', '')} {article.get('summary', '')}".lower()

    positive_hits = [w for w in keywords.get("positive", []) if w.lower() in blob]
    negative_hits = [w for w in keywords.get("negative", []) if w.lower() in blob]
    entity_hits = [w for w in keywords.get("entities", []) if w.lower() in blob]
    target_hits = [w for w in keywords.get("targets", []) if w.lower() in blob]

    article["lang_detected"] = detect_language(blob)
    article["positive_hits"] = positive_hits
    article["negative_hits"] = negative_hits
    article["entity_hits"] = entity_hits
    article["target_hits"] = target_hits
    article["impact_label"] = determine_impact(article)
    return article
