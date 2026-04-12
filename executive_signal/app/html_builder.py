from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from html import escape
from string import Template
from zoneinfo import ZoneInfo

MODULE_LABELS = {
    "company": "Company",
    "price": "Price",
    "supply_chain": "Supply Chain",
    "failure": "Failure",
    "earnings": "Earnings",
    "geo_risk": "Geo Risk",
    "talent": "Talent",
    "product": "Product",
}


def fmt_dt(value: str) -> str:
    return (value or "")[:16] if value else "-"


def arrow(direction: str) -> str:
    d = (direction or "").lower()
    if "up" in d or "bull" in d or "positive" in d:
        return "↑"
    if "down" in d or "bear" in d or "negative" in d:
        return "↓"
    return "→"


def build_cards(articles: list[dict]) -> str:
    parts: list[str] = []
    for a in articles:
        display_title = a.get("translated_title_ja") or a.get("title", "")
        original_title = a.get("title", "")
        display_summary = a.get("translated_summary_ja") or a.get("summary") or ""
        original_summary = a.get("summary") or ""
        module = a.get("module", "")
        region = "domestic" if a.get("region") == "domestic" else "global"
        tags: list[str] = []
        if a.get("impact_label"):
            tags.append(f"<span class='tag impact'>{escape(a['impact_label'])}</span>")
        tags.append("<span class='tag'>国内</span>" if region == "domestic" else "<span class='tag'>海外</span>")
        parts.append(f"""
<article class="card article-card" data-module="{escape(module)}" data-region="{region}">
  <div class="card-header">
    <div class="eyebrow">{escape(a.get('source', '-'))}</div>
    <div class="score">Score {a.get('score', 0):.1f}</div>
  </div>
  <a class="title" href="{escape(a.get('url', '#'))}" target="_blank" rel="noopener"
     data-original-title="{escape(original_title)}"
     data-ja-title="{escape(display_title)}">{escape(original_title)}</a>
  <p class="summary"
     data-original-summary="{escape(original_summary)}"
     data-ja-summary="{escape(display_summary)}">{escape(original_summary[:260])}</p>
  <div class="meta-row">
    <div class="meta-left">{escape(MODULE_LABELS.get(module, module or '-'))} ・ {escape(fmt_dt(a.get('published', '')))}</div>
    <div class="tags">{''.join(tags)}</div>
  </div>
</article>
""")
    return "\n".join(parts)


def build_signal_cards(signals: list[dict]) -> str:
    parts: list[str] = []
    for s in signals:
        direction = s.get("direction", "Neutral")
        module = s.get('module', '')
        parts.append(f"""
<article class="card signal-card" data-module="{escape(module)}">
  <div class="signal-top">
    <div class="signal-pills"><span class="signal-module-pill">{escape(MODULE_LABELS.get(module, module or '-'))}</span></div>
    <div class="signal-strength">Strength {int(s.get('strength', 0))}/5</div>
  </div>
  <div class="signal-target">{escape(s.get('target', '-'))}</div>
  <div class="signal-direction {escape(direction.lower())}">{arrow(direction)} {escape(direction)}</div>
  <div class="signal-reason">{escape(s.get('reason', '') or 'No keyword reason')}</div>
  <div class="signal-meta">Articles {int(s.get('article_count', 0))} / Avg Score {float(s.get('score', 0)):.1f}</div>
</article>
""")
    return "\n".join(parts)


def build_source_list(module_articles: dict[str, list[dict]], source_health: list[dict]) -> str:
    health_map = {(x.get('module', ''), x.get('source', '')): x for x in source_health}
    parts: list[str] = []
    for module in MODULE_LABELS:
        names = sorted({a.get('source', '') for a in module_articles.get(module, []) if a.get('source')} | {x.get('source', '') for x in source_health if x.get('module') == module and x.get('source')})
        items = []
        for name in names:
            info = health_map.get((module, name), {})
            status = info.get('status', 'unknown')
            count = info.get('count', 0)
            items.append(f"<li><span>{escape(name)}</span><span class='status-pill status-{escape(status)}'>{escape(str(status).upper())} · {count}</span></li>")
        if not items:
            items.append("<li><span>No source</span><span class='status-pill status-unknown'>UNKNOWN · 0</span></li>")
        parts.append(f"<section class='source-block' data-module='{escape(module)}'><h3>{escape(MODULE_LABELS[module])}</h3><ul>{''.join(items)}</ul></section>")
    return "\n".join(parts)


def build_tag_summary(module_articles: dict[str, list[dict]]) -> str:
    rows = []
    for module in MODULE_LABELS:
        articles = module_articles.get(module, [])
        count = len(articles)
        high = sum(1 for a in articles if a.get('impact_label') == 'High')
        avg = (sum(float(a.get('score', 0)) for a in articles) / count) if count else 0.0
        jp = sum(1 for a in articles if a.get('region') == 'domestic')
        high_rate = (high / count * 100.0) if count else 0.0
        rows.append(
            f"<tr data-module='{escape(module)}'><td>{escape(MODULE_LABELS[module])}</td><td>{count}</td><td>{jp}</td><td>{high}</td><td>{high_rate:.1f}%</td><td>{avg:.1f}</td></tr>"
        )
    return "\n".join(rows)


def build_health_summary(health_summary: dict, warnings: list[str]) -> str:
    counter = health_summary.get('status_counter', {}) if isinstance(health_summary, dict) else {}
    weakest = health_summary.get('weakest_sources', []) if isinstance(health_summary, dict) else []
    weakest_rows = ''.join(
        f"<tr><td>{escape(MODULE_LABELS.get(x.get('module', ''), x.get('module', '-')))}</td><td>{escape(x.get('source', '-'))}</td><td>{escape(x.get('status', '-'))}</td><td>{int(x.get('count', 0))}</td></tr>"
        for x in weakest
    ) or "<tr><td colspan='4'>データなし</td></tr>"
    warn_list = ''.join(f"<li>{escape(w)}</li>" for w in warnings[:12]) or '<li>警告なし</li>'
    return f"""
<div class="metric-value">{counter.get('ok', 0)}</div></div>
  <div class="metric"><div class="metric-label">0件ソース</div><div class="metric-value">{counter.get('zero', 0)}</div></div>
  <div class="metric"><div class="metric-label">エラーソース</div><div class="metric-value">{counter.get('error', 0)}</div></div>
  <div class="metric"><div class="metric-label">警告件数</div><div class="metric-value">{len(warnings)}</div></div>
</div>
<div class="analysis-grid two-col-balanced">
  <div class="card"><h3>弱いソース</h3><table><thead><tr><th>モジュール</th><th>ソース</th><th>状態</th><th>件数</th></tr></thead><tbody>{weakest_rows}</tbody></table></div>
  <div class="card"><h3>最新警告</h3><ul class="warn-list">{warn_list}</ul></div>
</div>
"""


def build_analysis_rows(grouped_articles: dict[str, list[dict]], grouped_signals: dict[str, list[dict]]) -> str:
    rows = []
    for module in MODULE_LABELS:
        mod_articles = grouped_articles.get(module, [])
        mod_signals = grouped_signals.get(module, [])
        avg = (sum(float(a.get('score', 0)) for a in mod_articles) / len(mod_articles)) if mod_articles else 0.0
        strongest = mod_signals[0].get('target', '-') if mod_signals else '-'
        rows.append(f"<tr data-module='{escape(module)}'><td>{escape(MODULE_LABELS[module])}</td><td>{len(mod_articles)}</td><td>{len(mod_signals)}</td><td>{avg:.1f}</td><td>{escape(strongest)}</td></tr>")
    return ''.join(rows)



def build_analysis_summary(grouped_articles: dict[str, list[dict]], grouped_signals: dict[str, list[dict]]) -> str:
    module_stats = []
    for module in MODULE_LABELS:
        mod_articles = grouped_articles.get(module, [])
        mod_signals = grouped_signals.get(module, [])
        article_count = len(mod_articles)
        avg = (sum(float(a.get('score', 0)) for a in mod_articles) / article_count) if article_count else 0.0
        high = sum(1 for a in mod_articles if a.get('impact_label') == 'High')
        high_rate = (high / article_count * 100.0) if article_count else 0.0
        module_stats.append({
            'module': module,
            'label': MODULE_LABELS[module],
            'articles': article_count,
            'signals': len(mod_signals),
            'avg': avg,
            'high_rate': high_rate,
        })

    top_avg = max(module_stats, key=lambda x: (x['avg'], x['signals'], x['articles'])) if module_stats else None
    top_high = max(module_stats, key=lambda x: (x['high_rate'], x['articles'])) if module_stats else None
    top_volume = max(module_stats, key=lambda x: (x['articles'], x['signals'])) if module_stats else None

    parts = []
    if top_avg:
        parts.append(f"最強シグナルは {top_avg['label']}（平均スコア {top_avg['avg']:.1f}）")
    if top_high:
        parts.append(f"高インパクト率トップは {top_high['label']}（{top_high['high_rate']:.1f}%）")
    if top_volume:
        parts.append(f"記事母数最大は {top_volume['label']}（{top_volume['articles']}件）")
    return " ｜ ".join(parts) if parts else "集計結果はまだありません。"


def build_module_summaries(grouped_articles: dict[str, list[dict]], grouped_signals: dict[str, list[dict]]) -> dict[str, str]:
    summaries: dict[str, str] = {}
    for module, label in MODULE_LABELS.items():
        mod_articles = grouped_articles.get(module, [])
        mod_signals = grouped_signals.get(module, [])
        article_count = len(mod_articles)
        if article_count == 0:
            summaries[module] = f"{label}：記事なし"
            continue
        avg = sum(float(a.get('score', 0)) for a in mod_articles) / article_count
        high = sum(1 for a in mod_articles if a.get('impact_label') == 'High')
        high_rate = high / article_count * 100.0
        top_signal = mod_signals[0] if mod_signals else None
        parts = [f"記事 {article_count}件 / 平均スコア {avg:.1f}"]
        if high_rate >= 5.0:
            parts.append(f"高インパクト {high_rate:.0f}%")
        if top_signal:
            direction = top_signal.get('direction', '')
            target = top_signal.get('target', '')
            arrow = '↑' if 'up' in direction.lower() else '↓' if 'down' in direction.lower() else '→'
            parts.append(f"主要シグナル：{target} {arrow}")
        summaries[module] = " ｜ ".join(parts)
    # allモジュール（Mainタブ）用
    all_articles = [a for arts in grouped_articles.values() for a in arts]
    all_count = len(all_articles)
    if all_count:
        all_avg = sum(float(a.get('score', 0)) for a in all_articles) / all_count
        top_mods = sorted(MODULE_LABELS.keys(), key=lambda m: len(grouped_signals.get(m, [])), reverse=True)[:2]
        top_labels = [MODULE_LABELS[m] for m in top_mods if grouped_signals.get(m)]
        if top_labels:
            summaries['all'] = f"全 {all_count}件 / 平均スコア {all_avg:.1f} ｜ シグナル上位：{' / '.join(top_labels)}"
        else:
            summaries['all'] = f"全 {all_count}件 / 平均スコア {all_avg:.1f}"
    else:
        summaries['all'] = "記事なし"
    return summaries

def build_html(articles: list[dict], signals: list[dict], *, warnings: list[str], source_health: list[dict], health_summary: dict) -> str:
    jst_now = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M")
    articles_sorted = sorted(articles, key=lambda x: float(x.get('score', 0)), reverse=True)
    top_focus = articles_sorted[:12]

    # 各モジュール最低1件保証したtop_signalsを構築
    top_signals = signals[:12]
    represented = {s.get('module') for s in top_signals}
    missing_modules = [m for m in MODULE_LABELS if m not in represented]
    for module in missing_modules:
        mod_signals = grouped_signals.get(module, []) if 'grouped_signals' in dir() else []
        if mod_signals:
            top_signals = top_signals + [mod_signals[0]]

    grouped_articles: dict[str, list[dict]] = defaultdict(list)
    grouped_signals: dict[str, list[dict]] = defaultdict(list)
    for a in articles_sorted:
        grouped_articles[a.get('module', '')].append(a)
    for s in signals:
        grouped_signals[s.get('module', '')].append(s)

    # grouped_signals構築後に再計算
    top_signals = signals[:12]
    represented = {s.get('module') for s in top_signals}
    for module in MODULE_LABELS:
        if module not in represented:
            mod_signals = grouped_signals.get(module, [])
            if mod_signals:
                top_signals = top_signals + [mod_signals[0]]

    high_count = sum(1 for a in articles if a.get('impact_label') == 'High')
    domestic_count = sum(1 for a in articles if a.get('region') == 'domestic')
    global_count = sum(1 for a in articles if a.get('region') != 'domestic')

    nav_tabs = [('main', 'Main', 'all')] + [(f'mod-{k}', v, k) for k, v in MODULE_LABELS.items()] + [('analysis', 'Analysis', 'all')]
    nav = ''.join(
        f"<button type='button' class='nav-btn{' active' if i == 0 else ''}' data-tab='{tab}' data-module='{module}'>{label}</button>"
        for i, (tab, label, module) in enumerate(nav_tabs)
    )
    analysis_rows = build_analysis_rows(grouped_articles, grouped_signals)
    analysis_summary = build_analysis_summary(grouped_articles, grouped_signals)
    module_summaries = build_module_summaries(grouped_articles, grouped_signals)
    module_summaries_js = json.dumps(module_summaries, ensure_ascii=False)

    template = Template("""<!doctype html>
<html lang='ja'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>Executive Signal</title>
<style>
:root { --bg:#0b1020; --panel:#121a2a; --panel2:#162137; --line:#2c3952; --text:#eaf0fb; --sub:#9fb0c9; --accent:#72a8ff; --good:#3fb950; --bad:#ff7b72; --neutral:#d9b340; }
* { box-sizing:border-box; }
body { margin:0; font-family:Arial, 'Hiragino Kaku Gothic ProN', Meiryo, sans-serif; background:linear-gradient(180deg,#07101f 0%,#0a1120 100%); color:var(--text); }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }
.container { max-width:1200px; margin:0 auto; padding:0 16px 40px; }
.hero { padding:0 0 8px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
.brand { display:flex; align-items:center; gap:12px; flex-wrap:wrap; flex:1 1 auto; }
.hero-title { margin:0; font-size:22px; font-weight:800; line-height:1.2; }
.hero-title .subline { font-weight:400; color:var(--sub); font-size:16px; }
.updated { color:var(--text); font-size:12px; white-space:nowrap; }
.hero-controls { display:flex; align-items:center; gap:12px; flex-wrap:wrap; flex-shrink:0; }
.toolbar { margin-top:14px; padding:10px 14px; border:1px solid var(--line); border-radius:18px; background:rgba(6,15,35,.42); }
.toolbar-row { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.nav-group, .module-group, .mini-group { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.nav-btn, .translate-btn { border:1px solid #334565; background:#0f1728; color:var(--text); padding:8px 14px; border-radius:999px; cursor:pointer; font-size:14px; transition:.18s ease; }
.region-switch { gap:10px; }
.region-switch label { display:inline-flex; align-items:center; gap:5px; font-size:12px; color:var(--text); cursor:pointer; }
.region-switch input[type='radio'] { margin:0; accent-color: var(--accent); width:13px; height:13px; }
.nav-btn:hover, .translate-btn:hover { border-color:#46628e; background:#14203a; }
.nav-btn.active, .translate-btn.active { background:var(--accent); color:#08111d; border-color:var(--accent); font-weight:700; }
.filter-label { color:#d6e3f6; font-size:13px; margin-right:2px; }
.filter-label.mini { display:none; }
.tab-panel { display:none; padding-top:18px; }
.tab-panel.active { display:block; }
.metrics { display:none; display:grid; grid-template-columns:repeat(6, minmax(0, 1fr)); gap:12px; margin-bottom:18px; }
#tab-main .metrics { display:none; display:none !important; }
.metrics.compact { grid-template-columns:repeat(4, minmax(0, 1fr)); margin-bottom:0; }
.metric { border:1px solid rgba(47,69,99,.75); background:rgba(13,22,39,.82); border-radius:18px; padding:14px 16px; }
.metric-label { color:var(--sub); font-size:13px; margin-bottom:6px; }
.metric-value { font-size:28px; font-weight:800; line-height:1; }
.section-header { display:flex; justify-content:space-between; align-items:flex-end; gap:10px; margin:6px 0 12px; }
.section-header h2 { margin:0; font-size:22px; }
.section-note { color:var(--sub); font-size:13px; }

.analysis-summary {
  border:1px solid rgba(73,109,165,.55);
  background:linear-gradient(180deg, rgba(18,31,56,.95), rgba(12,20,36,.95));
  border-radius:16px;
  padding:14px 16px;
  margin-bottom:14px;
}
.analysis-summary-label {
  color:#a9c6ff;
  font-size:12px;
  font-weight:700;
  margin-bottom:6px;
}
.analysis-summary-text {
  color:#eef4ff;
  font-size:15px;
  font-weight:700;
  line-height:1.6;
}

.analysis-grid { display:grid; gap:18px; grid-template-columns:1fr 1fr; align-items:start; }
.analysis-grid.two-col-balanced { grid-template-columns:1fr 1fr; }
.signal-grid, .card-grid { display:grid; gap:12px; grid-template-columns:repeat(2, minmax(0, 1fr)); align-items:start; }
.card { border:1px solid rgba(49,73,108,.75); background:linear-gradient(180deg, rgba(17,26,45,.95), rgba(13,20,35,.95)); border-radius:18px; padding:16px 16px 14px; box-shadow:0 12px 28px rgba(0,0,0,.24); }
.card-header, .meta-row, .signal-top { display:flex; justify-content:space-between; gap:10px; align-items:flex-start; }
.eyebrow { color:#b7c7de; font-size:12px; }
.score, .signal-strength { color:#a9c6ff; font-weight:700; font-size:12px; }
.title { display:block; color:var(--text); font-size:19px; font-weight:800; line-height:1.45; margin:10px 0 10px; }
.summary, .signal-reason { color:#d5dfef; font-size:14px; line-height:1.7; margin:0; }
.meta-left { color:var(--sub); font-size:12px; }
.tags { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }
.tag, .signal-module-pill { display:inline-flex; align-items:center; padding:4px 9px; border-radius:999px; font-size:11px; border:1px solid #364a67; color:#c7d9f2; background:#0e1729; }
.tag.impact { border-color:#486eb8; color:#d9e8ff; }
.signal-target { font-size:20px; font-weight:800; margin:8px 0 8px; line-height:1.3; }
.signal-direction {
  display:inline-flex;
  align-items:center;
  gap:6px;
  padding:4px 10px;
  border-radius:999px;
  font-size:18px;
  font-weight:800;
  margin-bottom:8px;
  border:1px solid transparent;
  line-height:1.1;
}
.signal-direction.up, .signal-direction.positive, .signal-direction.bullish {
  border-left:3px solid #4da3ff;
  color:#9fd0ff;
  background:rgba(77,163,255,0.25);
  border-color:rgba(77,163,255,0.55);
  box-shadow:0 0 0 1px rgba(77,163,255,0.08) inset;
}
.signal-direction.down, .signal-direction.negative, .signal-direction.bearish {
  color:#ff6b6b;
  background:rgba(255,92,92,0.14);
  border-color:rgba(255,92,92,0.35);
  box-shadow:0 0 0 1px rgba(255,92,92,0.08) inset;
}
.signal-direction.neutral {
  color:#ffc857;
  background:rgba(255,200,87,0.14);
  border-color:rgba(255,200,87,0.35);
  box-shadow:0 0 0 1px rgba(255,200,87,0.08) inset;
}
.signal-meta { color:var(--sub); font-size:12px; margin-top:10px; }
.table-card { border:1px solid rgba(49,73,108,.75); background:rgba(11,20,38,.82); border-radius:18px; overflow:hidden; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th, td { text-align:left; padding:14px 12px; border-bottom:1px solid rgba(48,73,108,.55); }
th { color:#bdd0ea; font-size:13px; }
tbody tr:last-child td { border-bottom:none; }
.warn-list { margin:8px 0 0; padding-left:18px; color:#d7e3f2; }
.source-block { margin-top:16px; }
.source-block h3 { margin:0 0 10px; font-size:16px; }
.source-block ul { list-style:none; padding:0; margin:0; display:grid; gap:8px; }
.source-block li { display:flex; justify-content:space-between; gap:16px; padding:10px 12px; border:1px solid rgba(48,73,108,.55); border-radius:14px; background:rgba(11,20,38,.55); }
.status-pill { display:inline-flex; align-items:center; border-radius:999px; padding:4px 9px; border:1px solid #385579; font-size:12px; }
.status-ok { color:#90e19d; border-color:#3f8150; }
.status-zero, .status-unknown { color:#ffd9a1; border-color:#86622d; }
.status-error { color:#ffb0ab; border-color:#8a423d; }
.footer-note {
  max-width: 1200px;
  margin: 20px auto 0 auto;
  padding: 0 16px;
  box-sizing: border-box;
  width: 100%;
  position: static;
  left: auto;
  transform: none;
}
.stacked { display:grid; gap:18px; }
.filter-empty-note { display:none; margin:14px 0 0; color:var(--sub); }
@media (max-width: 1180px) { .metrics { display:none; grid-template-columns:repeat(3, minmax(0,1fr)); } .analysis-grid, .analysis-grid.two-col-balanced, .signal-grid, .card-grid { grid-template-columns:1fr; } }
@media (max-width: 760px) { .container { padding:0 16px 28px; } .hero-title { font-size:28px; } .hero-subtitle { font-size:16px; } .metrics, .metrics.compact { grid-template-columns:repeat(2, minmax(0,1fr)); } .title { font-size:22px; } .signal-target { font-size:19px; } .toolbar-row.top { align-items:flex-start; } .toolbar-left { gap:10px; } .signal-grid, .card-grid, .analysis-grid { grid-template-columns:1fr; } }


.top-summary {
  width: 100%;
  margin: 6px 0 14px 0;
  padding: 6px 2px;
  border-top: 1px solid rgba(120,160,220,0.25);
  border-bottom: 1px solid rgba(120,160,220,0.25);
  color: #cfe3ff;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.2px;
  background: none;
}
.top-summary .label {
  color: #7fb3ff;
  font-weight: 600;
}


</style>
</head>
<body>
<div class='container'>
  <header class='hero'>
    <div class='brand'>
      <div class='hero-title'>Executive Signal <span class='subline'>｜ 経営判断</span></div>
      <div class='updated'>更新日時：$updated_at</div>
    </div>
    <div class='hero-controls'>
      <div class='mini-group region-switch'>
        <label><input type='radio' name='regionSwitch' value='all' checked>全て</label>
        <label><input type='radio' name='regionSwitch' value='domestic'>国内</label>
        <label><input type='radio' name='regionSwitch' value='global'>海外</label>
      </div>
      <button type='button' id='pageTranslateBtn' class='translate-btn' data-mode='original'>🌐 翻訳 OFF</button>
    </div>
  </header>

  <div class='toolbar'>
    <div class='toolbar-row'>
      <div class='nav-group'>$nav</div>
    </div>
  </div>

  <section id='tab-main' class='tab-panel active'>

    <div class='analysis-grid'>
      <div>
        <div class='section-header'><h2>Top Signals</h2></div>
        <div class='top-summary'><span class='label'>現在：</span><span id='moduleSummaryText'></span></div>
        <div class='signal-grid' id='signalGrid'>$top_signals</div>
      </div>
      <div>
        <div class='section-header'><h2>今日見る記事</h2><div class='section-note'>現在の条件で表示</div></div>
        <div class='card-grid' id='topFocusGrid'>$top_focus</div>
        <div id='filterEmptyNote' class='filter-empty-note'>条件に合う記事がありません。</div>
      </div>
    </div>

    <div class='section-header'><h2>All Articles</h2><div class='section-note'>確認用の全件表示</div></div>
    <div class='card-grid' id='allArticlesGrid'>$all_cards</div>
    <div id='filterEmptyNoteAll' class='filter-empty-note'>条件に合う記事がありません。</div>
  </section>

  <section id='tab-analysis' class='tab-panel'>
    <div class='stacked'>
      <div>
        <div class='section-header'><h2>Executive Analysis</h2></div>
        <div class='analysis-summary'><div class='analysis-summary-label'>結論</div><div class='analysis-summary-text'>$analysis_summary</div></div>
        <div class='table-card'><table><thead><tr><th>モジュール</th><th>記事数</th><th>シグナル数</th><th>平均スコア</th><th>最上位ターゲット</th></tr></thead><tbody>$analysis_rows</tbody></table></div>
      </div>
      <div>
        <div class='section-header'><h2>Module Score Summary</h2></div>
        <div class='table-card'><table><thead><tr><th>モジュール</th><th>記事数</th><th>国内記事</th><th>高インパクト</th><th>高インパクト率</th><th>平均スコア</th></tr></thead><tbody>$tag_rows</tbody></table></div>
      </div>
      <div>
        <div class='section-header'><h2>Collection Health</h2><div class='section-note'>取得品質の確認用</div></div>
        $health_html
      </div>
      <div>
        
      </div>
      <div class='footer-note'>注記: シグナルはキーワード・ソース重み・鮮度を組み合わせた簡易スコアです。正式な投資判断・法務判断・経営決裁には、必ず一次情報本文と原文確認を併用してください。</div>
    </div>
  </section>
</div>
<script>
const navButtons = document.querySelectorAll('.nav-btn');
const moduleSummaries = $module_summaries_js;

function updateModuleSummary(module) {
  const el = document.getElementById('moduleSummaryText');
  if (el) el.textContent = moduleSummaries[module] || moduleSummaries['all'] || '';
}

const tabs = document.querySelectorAll('.tab-panel');
const regionRadios = document.querySelectorAll("input[name='regionSwitch']");
const pageTranslateBtn = document.getElementById('pageTranslateBtn');
let currentModule = 'all';
let currentRegion = 'all';
let currentTextMode = 'original';

function visibleCount(nodes) {
  return Array.from(nodes).filter((node) => node.style.display !== 'none').length;
}

function updateEmptyState() {
  const topCards = document.querySelectorAll('#topFocusGrid .article-card');
  const allCards = document.querySelectorAll('#allArticlesGrid .article-card');
  const topNote = document.getElementById('filterEmptyNote');
  const allNote = document.getElementById('filterEmptyNoteAll');
  const signalGrid = document.getElementById('signalGrid');
  if (topNote) topNote.style.display = visibleCount(topCards) ? 'none' : 'block';
  if (allNote) allNote.style.display = visibleCount(allCards) ? 'none' : 'block';
  if (signalGrid) signalGrid.style.display = visibleCount(allCards) ? '' : 'none';
}

function applyFilters() {
  document.querySelectorAll('.article-card').forEach((card) => {
    const module = card.dataset.module || '';
    const region = card.dataset.region || 'global';
    const moduleOk = currentModule === 'all' || module === currentModule;
    const regionOk = currentRegion === 'all' || region === currentRegion;
    card.style.display = moduleOk && regionOk ? '' : 'none';
  });
  document.querySelectorAll('.signal-card').forEach((card) => {
    const module = card.dataset.module || '';
    const moduleOk = currentModule === 'all' || module === currentModule;
    card.style.display = moduleOk ? '' : 'none';
  });
  updateEmptyState();
}

function setMainTab(module) {
  currentModule = module || 'all';
  tabs.forEach((x) => x.classList.remove('active'));
  const target = document.getElementById('tab-main');
  if (target) target.classList.add('active');
  updateModuleSummary(currentModule);
  applyFilters();
}

navButtons.forEach((btn) => {
  btn.addEventListener('click', () => {
    navButtons.forEach((x) => x.classList.remove('active'));
    btn.classList.add('active');
    const targetTab = btn.dataset.tab || 'main';
    if (targetTab === 'analysis') {
      currentModule = 'all';
      tabs.forEach((x) => x.classList.remove('active'));
      const target = document.getElementById('tab-analysis');
      if (target) target.classList.add('active');
    } else {
      setMainTab(btn.dataset.module || 'all');
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });
});

regionRadios.forEach((input) => {
  input.addEventListener('change', () => {
    currentRegion = input.value || 'domestic';
    applyFilters();
  });
});

function applyTextMode(mode) {
  currentTextMode = mode;
  if (pageTranslateBtn) {
    pageTranslateBtn.dataset.mode = mode;
    pageTranslateBtn.classList.toggle('active', mode === 'ja');
    pageTranslateBtn.textContent = mode === 'ja' ? '🌐 翻訳 ON' : '🌐 翻訳 OFF';
  }
  document.querySelectorAll('.article-card .title').forEach((node) => {
    const fallback = node.dataset.originalTitle || '';
    const value = mode === 'ja' ? (node.dataset.jaTitle || fallback) : fallback;
    node.textContent = value;
  });
  document.querySelectorAll('.article-card .summary').forEach((node) => {
    const fallback = node.dataset.originalSummary || '';
    const value = mode === 'ja' ? (node.dataset.jaSummary || fallback) : fallback;
    node.textContent = value.slice(0, 260);
  });
}

if (pageTranslateBtn) {
  pageTranslateBtn.addEventListener('click', () => {
    applyTextMode(currentTextMode === 'ja' ? 'original' : 'ja');
  });
}

applyFilters();
updateModuleSummary('all');
applyTextMode('original');
window.__ESS_DATA__ = $ess_data;
</script>
</body>
</html>""")
    return template.substitute(
        updated_at=jst_now,
        nav=nav,
        signal_count=len(signals),
        high_count=high_count,
        domestic_count=domestic_count,
        article_count=len(articles),
        global_count=global_count,
        module_count=len(MODULE_LABELS),
        top_signals=build_signal_cards(top_signals) or "<div class='empty-card'>シグナルがまだありません。</div>",
        top_focus=build_cards(top_focus) or "<div class='empty-card'>記事がまだありません。</div>",
        all_cards=build_cards(articles_sorted) or "<div class='empty-card'>記事がまだありません。</div>",
        health_html=build_health_summary(health_summary, warnings),
        tag_rows=build_tag_summary(grouped_articles),
        sources_html=build_source_list(grouped_articles, source_health),
        analysis_rows=analysis_rows,
        analysis_summary=analysis_summary,
        module_summaries_js=module_summaries_js,
        ess_data=json.dumps({'articles': articles_sorted[:60], 'signals': signals[:30], 'warnings': warnings[:20]}, ensure_ascii=False),
    )