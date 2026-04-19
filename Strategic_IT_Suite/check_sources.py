"""
sources.json 疎通確認スクリプト
usage:
  python check_sources.py                        # カレントの shared/sources.json
  python check_sources.py path/to/sources.json   # パス指定
"""
import sys
import json
import requests
import feedparser
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
HEADERS = {"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
TIMEOUT = 10

# sources.json のパス解決
if len(sys.argv) > 1:
    sources_path = Path(sys.argv[1])
else:
    sources_path = Path("shared/sources.json")

if not sources_path.exists():
    print(f"❌ ファイルが見つかりません: {sources_path}")
    input("\nEnterキーで閉じる...")
    sys.exit(1)

with open(sources_path, encoding="utf-8") as f:
    sources = json.load(f)

print("=" * 65)
print(f"  ソース疎通確認  ({sources_path})")
print(f"  対象: {len(sources)}本")
print("=" * 65)

def check(src):
    name  = src.get("name", "?")
    url   = src.get("url", "")
    stype = src.get("type", "rss")
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        status = r.status_code
        if status == 200 and stype == "rss":
            feed = feedparser.parse(r.content)
            count = len(feed.entries)
            icon = "✅" if count > 0 else "⚠️ "
            return (icon, name, status, f"{count}件", url)
        elif status == 200:
            return ("✅", name, status, "HTML OK", url)
        else:
            return ("❌", name, status, "-", url)
    except Exception as e:
        return ("💀", name, "-", str(e)[:40], url)

results = []
with ThreadPoolExecutor(max_workers=8) as ex:
    futures = {ex.submit(check, s): s for s in sources}
    for f in as_completed(futures):
        results.append(f.result())

results.sort(key=lambda x: (x[0] != "✅", x[0] != "⚠️ ", x[0]))

for icon, name, status, detail, url in results:
    print(f"  {icon}  {name:<30} [{status}] {detail}")

print("=" * 65)
ok  = sum(1 for r in results if r[0] == "✅")
ng  = sum(1 for r in results if r[0] in ("❌", "💀"))
wrn = sum(1 for r in results if r[0] == "⚠️ ")
print(f"  結果: ✅ {ok}本  ⚠️  {wrn}本  ❌/💀 {ng}本  (計{len(results)}本)")
print("=" * 65)
input("\nEnterキーで閉じる...")
