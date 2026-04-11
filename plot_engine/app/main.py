import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = ROOT / "app"
OUTPUT_DIR = ROOT / "output"
LOG_DIR = ROOT / "logs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG = json.loads((ROOT / "shared" / "config.json").read_text(encoding="utf-8"))
APP_NAME = CONFIG["app_name"]
APP_ICON = CONFIG["app_icon"]
SUBTITLE = CONFIG["subtitle"]

JST = ZoneInfo("Asia/Tokyo")
updated = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

template = (APP_DIR / "template.html").read_text(encoding="utf-8")
html = (
    template
    .replace("__APP_NAME__", APP_NAME)
    .replace("__APP_ICON__", APP_ICON)
    .replace("__SUBTITLE__", SUBTITLE)
    .replace("__UPDATED_AT__", updated)
)
(OUTPUT_DIR / "index.html").write_text(html, encoding="utf-8")
print("Generated:", OUTPUT_DIR / "index.html")
