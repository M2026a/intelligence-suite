from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")
updated = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

template = (APP_DIR / "template.html").read_text(encoding="utf-8")
html = template.replace("__UPDATED_AT__", updated)
(OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
print("Generated:", OUTPUT_DIR / "index.html")
