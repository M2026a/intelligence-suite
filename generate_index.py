from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT_DIR / "index.html"

SITE_TITLE = "Intelligence Suite"
SITE_SUBTITLE = "意思決定支援・情報収集ダッシュボード"


def get_groups() -> List[Dict[str, Any]]:
    return [
        {
            "name": "■ 業務 / IT / 戦略",
            "items": [
                {
                    "title": "AI News +",
                    "subtitle": "AI情報収集",
                    "icon": "🤖",
                    "href": "ai_plus/output/index.html",
                },
                {
                    "title": "IT News +",
                    "subtitle": "IT業界ニュース",
                    "icon": "🖥️",
                    "href": "it_plus/output/index.html",
                },
                {
                    "title": "Idea Trend Engine",
                    "subtitle": "改善アイデア",
                    "icon": "💡",
                    "href": "IdeaTrendEngine/output/index_im.html",
                },
                {
                    "title": "Strategic IT Suite",
                    "subtitle": "リスク / 戦略",
                    "icon": "🧭",
                    "href": "Strategic_IT_Suite/output/index.html",
                },
                {
                    "title": "Executive Signal",
                    "subtitle": "経営シグナル分析",
                    "icon": "📊",
                    "href": "executive_signal/output/index.html",
                },
            ],
        },
        {
            "name": "■ 市場 / 業界",
            "items": [
                {
                    "title": "Smart News Viewer",
                    "subtitle": "総合ニュース",
                    "icon": "📰",
                    "href": "smart_news_viewer/output/index.html",
                },
                {
                    "title": "Market +",
                    "subtitle": "市場分析",
                    "icon": "📈",
                    "href": "market_plus/output/index.html",
                },
                {
                    "title": "Auto Industry",
                    "subtitle": "自動車",
                    "icon": "🚗",
                    "href": "auto_industry_suite/output/index.html",
                },
                {
                    "title": "Memory Market +",
                    "subtitle": "半導体",
                    "icon": "🧠",
                    "href": "memory_market_plus/output/index.html",
                },
                {
                    "title": "PC Industry",
                    "subtitle": "PC / GPU",
                    "icon": "💻",
                    "href": "pc_industry_suite/output/index.html",
                },
                {
                    "title": "Camera Industry",
                    "subtitle": "カメラ",
                    "icon": "📷",
                    "href": "camera_industry_suite/output/index.html",
                },
            ],
        },
        {
            "name": "■ 娯楽 / 生活",
            "items": [
                {
                    "title": "Spot Navigator",
                    "subtitle": "スポット検索",
                    "icon": "📍",
                    "href": "Spot_Selection_Navigator/output/index.html",
                },
                {
                    "title": "Travel Search +",
                    "subtitle": "旅行",
                    "icon": "✈️",
                    "href": "travel_search_plus/output/index.html",
                },
                {
                    "title": "Dog Info",
                    "subtitle": "犬情報",
                    "icon": "🐶",
                    "href": "dog_information_suite/output/index.html",
                },
                {
                    "title": "Plot Engine",
                    "subtitle": "構想 / 文章プロット",
                    "icon": "✍️",
                    "href": "plot_engine/output/index.html",
                },
                {
                    "title": "Photo Finder",
                    "subtitle": "撮影スポット探索",
                    "icon": "📸",
                    "href": "Photo_Finder/output/index.html",
                },
                {
                    "title": "Watch Hub +",
                    "subtitle": "映画・配信・アニメ・ゲーム・芸能",
                    "icon": "🎥",
                    "href": "watch_hub_plus/output/index.html",
                },
                {
                    "title": "Entertainment +",
                    "subtitle": "エンタメ情報",
                    "icon": "🎬",
                    "href": "entertainment_plus/output/index.html",
                },
                {
                    "title": "Gadget +",
                    "subtitle": "ガジェット",
                    "icon": "📱",
                    "href": "gadget_plus/output/index.html",
                },
                {
                    "title": "Sports +",
                    "subtitle": "スポーツ",
                    "icon": "⚽",
                    "href": "sports_plus/output/index.html",
                },
                {
                    "title": "Takarazuka",
                    "subtitle": "宝塚",
                    "icon": "🎭",
                    "href": "takarazuka_information_suite/output/index.html",
                },
                {
                    "title": "Takarazuka B",
                    "subtitle": "宝塚_旧版",
                    "icon": "🎟️",
                    "href": "takarazuka_info_B/output/index.html",
                },
            ],
        },
    ]


def exists_file(relative_path: str) -> bool:
    return (ROOT_DIR / relative_path).exists()


def build_card_html(item: Dict[str, str]) -> str:
    is_exists = exists_file(item["href"])
    extra_style = "" if is_exists else "opacity:0.45;pointer-events:none;"
    badge_html = "" if is_exists else "<div class=\"missing-badge\">未生成</div>"

    return f"""    <a class="card" href="{item['href']}" style="{extra_style}">
      <div class="card-title"><span class="card-icon">{item['icon']}</span>{item['title']}</div>
      <div class="card-sub">{item['subtitle']}</div>
      {badge_html}
    </a>"""


def build_section_html(group: Dict[str, Any]) -> str:
    cards_html = "\n\n".join(build_card_html(item) for item in group["items"])
    return f"""<div class="section">
  <h2>{group['name']}</h2>
  <div class="grid">
{cards_html}
  </div>
</div>"""


def build_html(groups: List[Dict[str, Any]], update_time: str) -> str:
    sections_html = "\n\n".join(build_section_html(group) for group in groups)

    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SITE_TITLE}</title>

<style>
body {{
  font-family: Arial, sans-serif;
  background: #111;
  color: #fff;
  margin: 0;
  padding: 20px;
  font-size: 16px;
}}

.header {{
  margin-bottom: 30px;
}}

.title {{
  font-size: 32px;
  font-weight: bold;
}}

.subtitle {{
  color: #aaa;
  margin-top: 5px;
  font-size: 14px;
}}

.update {{
  color: #777;
  font-size: 12px;
  margin-top: 5px;
}}

.section {{
  margin-bottom: 32px;
}}

.section h2 {{
  border-left: 4px solid #4da3ff;
  padding-left: 10px;
  font-size: 18px;
  margin-bottom: 14px;
}}

.grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 16px;
}}

.card {{
  background: #1a1a1a;
  padding: 16px;
  border-radius: 12px;
  text-decoration: none;
  color: #fff;
  border: 1px solid #222;
  transition: 0.2s;
  display: block;
}}

.card:hover {{
  transform: translateY(-4px);
  border-color: #4da3ff;
}}

.card-title {{
  font-size: 16px;
  font-weight: bold;
  line-height: 1.4;
  display: flex;
  align-items: center;
  gap: 8px;
}}

.card-icon {{
  font-size: 20px;
  line-height: 1;
}}

.card-sub {{
  font-size: 13px;
  color: #aaa;
  margin-top: 6px;
  line-height: 1.4;
}}

.missing-badge {{
  color: #ff6666;
  font-size: 11px;
  margin-top: 6px;
}}

@media (max-width: 768px) {{
  body {{ padding: 16px; font-size: 18px; }}
  .title {{ font-size: 36px; }}
  .subtitle {{ font-size: 16px; }}
  .update {{ font-size: 14px; }}
  .section h2 {{ font-size: 24px; }}
  .grid {{ grid-template-columns: 1fr; gap: 14px; }}
  .card {{ padding: 20px; }}
  .card-title {{ font-size: 22px; }}
  .card-icon {{ font-size: 24px; }}
  .card-sub {{ font-size: 16px; }}
  .missing-badge {{ font-size: 13px; }}
}}
</style>
</head>

<body>

<div class="header">
  <div class="title">{SITE_TITLE}</div>
  <div class="subtitle">{SITE_SUBTITLE}</div>
  <div class="update">更新日時：{update_time}</div>
</div>

{sections_html}

</body>
</html>
"""


def main() -> None:
    update_time = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M")
    groups = get_groups()
    html = build_html(groups, update_time)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()