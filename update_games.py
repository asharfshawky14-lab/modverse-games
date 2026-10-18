import json
import os
import re
import html
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone

DATABASE = "games.json"
TIMEOUT = 25
MAX_LINKS_PER_SOURCE = 150
MAX_GAMES_PER_SOURCE = 60

USER_AGENT = (
"Mozilla/5.0 (Linux; Android 12) "
"AppleWebKit/537.36 Chrome/130.0.0.0 Mobile Safari/537.36"
)

# صفحات ليست ألعابًا، حتى لو كانت موجودة داخل موقع الألعاب

BLOCKED_PATHS = {
"games", "game", "appss", "apps", "app", "applications",
"top-100", "blog", "request", "disclaimer", "contact",
"about", "privacy-policy", "terms", "windows", "pc",
"download-app", "categories", "category", "search",
"login", "register", "account", "author", "tag", "page",
"files-downloaders", "ld-player-apk"
}

BLOCKED_WORDS = (
"emulator", "ld-player", "bluestacks", "windows",
"pc-game", "pc-games", "computer", "vpn", "browser",
"launcher", "keyboard", "cleaner", "file-manager",
"downloader", "video-downloader", "music-player",
"photo-editor", "video-editor", "screen-recorder",
"quran", "moslay", "athan", "tarteel",
"whatsapp", "telegram", "instagram", "facebook",
"tiktok", "netflix", "spotify", "youtube",
"wallpaper", "themes", "fonts", "antivirus",
"calculator", "weather", "keyboard-app"
)

# إشارات تساعد على تمييز صفحات الألعاب الحقيقية.

GAME_WORDS = (
"game", "games", "لعبة", "العاب", "ألعاب", "لعبه",
"simulator", "simulation", "racing", "racer",
"football", "soccer", "basketball", "cricket",
"fighting", "fight", "warrior", "battle",
"survival", "zombie", "ninja", "adventure",
"minecraft", "roblox", "free-fire", "pubg",
"clash-of-clans", "clash-royale", "subway-surfers",
"shadow-fight", "gta", "grand-theft-auto",
"dream-league", "efootball", "pes", "fifa",
"asphalt", "need-for-speed", "hill-climb",
"angry-birds", "temple-run", "stumble-guys",
"among-us", "plants-vs-zombies", "geometry-dash",
"the-sims", "sims-", "bus-simulator", "truck-simulator",
"car-parking", "mobile-legends", "call-of-duty",
"free-fire", "brawl-stars", "hay-day", "pokemon",
"mortal-kombat", "tekken", "terraria", "stardew",
"among-us", "hayday"
)

VERSION_RE = re.compile(
r"\b(?:version|ver.?|الإصدار|نسخة)\s*[:-]?\s*"
r"([0-9]+(?:.[0-9A-Za-z]+){1,5})",
re.IGNORECASE
)
SIZE_RE = re.compile(r"\b(\d+(?:.\d+)?)\s*(KB|MB|GB)\b", re.I)
ANDROID_RE = re.compile(
r"(?:Android|أندرويد)\s*(\d+(?:.\d+)?(?:+)?)", re.I
)

def now_utc():
return datetime.now(timezone.utc).isoformat()

def clean_text(value):
value = html.unescape(str(value or ""))
return re.sub(r"\s+", " ", value).strip()

def fetch_url(url):
request = urllib.request.Request(
url,
headers={
"User-Agent": USER_AGENT,
"Accept": "text/html,application/xhtml+xml,/;q=0.8",
"Accept-Language": "en-US,en;q=0.8,ar;q=0.6"
}
)
with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
final_url = response.geturl()
content_type = response.headers.get("Content-Type", "")
if "text/html" not in content_type and "application/xhtml" not in content_type:
raise ValueError("Not an HTML page")
return response.read().decode("utf-8", errors="replace"), final_url

class PageParser(HTMLParser):
def init(self):
super().init(convert_charrefs=True)
self.title = ""
self.title_depth = 0
self.meta = {}
self.anchors = []
self.current_anchor = None
self.text_parts = []
self.skip_depth = 0

def handle_starttag(self, tag, attrs):
    attrs = dict(attrs)
    tag = tag.lower()

    if tag in ("script", "style", "noscript", "svg"):
        self.skip_depth += 1

    if tag == "title":
        self.title_depth += 1

    if tag == "meta":
        key = (
            attrs.get("property")
            or attrs.get("name")
            or attrs.get("itemprop")
            or ""
        ).lower()
        value = attrs.get("content", "").strip()
        if key and value:
            self.meta[key] = value

    if tag == "a" and attrs.get("href"):
        self.current_anchor = {
            "href": attrs["href"].strip(),
            "text": "",
            "title": attrs.get("title", "").strip()
        }

    if tag == "img":
        src = (
            attrs.get("data-src")
            or attrs.get("data-lazy-src")
            or attrs.get("src")
            or ""
        ).strip()
        if src:
            self.meta.setdefault("images", [])
            self.meta["images"].append({
                "src": src,
                "alt": attrs.get("alt", "")
            })

def handle_data(self, data):
    if self.skip_depth:
        return
    value = data.strip()
    if not value:
        return
    self.text_parts.append(value)
    if self.title_depth:
        self.title += " " + value
    if self.current_anchor is not None:
        self.current_anchor["text"] += " " + value

def handle_endtag(self, tag):
    tag = tag.lower()
    if tag in ("script", "style", "noscript", "svg") and self.skip_depth:
        self.skip_depth -= 1
    if tag == "title" and self.title_depth:
        self.title_depth -= 1
    if tag == "a" and self.current_anchor is not None:
        self.current_anchor["text"] = clean_text(
            self.current_anchor["text"]
        )
        self.anchors.append(self.current_anchor)
        self.current_anchor = None

def get_title(parser):
return clean_text(
parser.meta.get("og:title")
or parser.meta.get("twitter:title")
or parser.title
)

def get_description(parser):
return clean_text(
parser.meta.get("description")
or parser.meta.get("og:description")
or parser.meta.get("twitter:description")
)

def get_image(parser, page_url):
image = (
parser.meta.get("og:image")
or parser.meta.get("twitter:image")
or parser.meta.get("twitter:image:src")
)
if not image:
for item in parser.meta.get("images", []):
if item.get("src"):
image = item["src"]
break
return urljoin(page_url, image) if image else ""

def is_same_site(url, base_url):
return (
urlparse(url).netloc.lower().removeprefix("www.")
== urlparse(base_url).netloc.lower().removeprefix("www.")
)

def is_listing_or_blocked(url):
parsed = urlparse(url)
path = parsed.path.lower().strip("/")
parts = [part for part in path.split("/") if part]

if not parts:
    return True

if any(part in BLOCKED_PATHS for part in parts):
    return True

if any(word in path for word in BLOCKED_WORDS):
    return True

if any(path.endswith(ext) for ext in (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".css",
    ".js", ".xml", ".pdf", ".zip", ".apk", ".mp4"
)):
    return True

# استبعاد تصنيفات HappyMod مثل /games/action/
if len(parts) >= 2 and parts[0] in ("games", "game", "category"):
    return True

return False

def looks_like_game(title, url, description):
title_lower = clean_text(title).lower()
path = urlparse(url).path.lower()
combined = title_lower + " " + path

if not title_lower or len(title_lower) < 2:
    return False

if is_listing_or_blocked(url):
    return False

# استبعاد صفحات التطبيقات والمحاكيات حتى لو ذكرت APK.
if any(word in combined for word in BLOCKED_WORDS):
    return False

# لازم تظهر علامة واضحة على أن الصفحة تخص لعبة.
if not any(word in combined for word in GAME_WORDS):
    return False

# صفحات التصنيفات والعناوين العامة ليست ألعابًا.
generic_titles = {
    "games", "android games", "apps", "applications",
    "home", "happymod", "traidmod", "traid mod",
    "تحميل العاب مهكرة", "ألعاب أندرويد"
}
if title_lower.strip(" -|") in generic_titles:
    return False

return True

def extract_game(parser, page_url, source_name):
title = get_title(parser)
description = get_description(parser)
full_text = clean_text(" ".join(parser.text_parts))

if not looks_like_game(title, page_url, description):
    return None

# رفض الصفحات التي لا تحتوي إلا على عنوان أو وصف عام.
if len(description) < 15 and len(full_text) < 100:
    return None

version = ""
size = ""
android = ""

match = VERSION_RE.search(title + " " + description + " " + full_text)
if match:
    version = match.group(1)

match = SIZE_RE.search(description + " " + full_text)
if match:
    size = match.group(0)

match = ANDROID_RE.search(description + " " + full_text)
if match:
    android = match.group(1)

# رابط الصفحة نفسها هو الرابط الأساسي. لا نستخدم رابط تنزيل
# عشوائيًا من صفحة أخرى باعتباره صفحة اللعبة.
return {
    "id": page_url,
    "title": title,
    "name": title,
    "description": description,
    "image": get_image(parser, page_url),
    "version": version,
    "size": size,
    "android": android,
    "url": page_url,
    "detail_url": page_url,
    "download_url": page_url,
    "source": source_name,
    "category": "ألعاب أندرويد",
    "versions": [],
    "updated_at": now_utc()
}

def read_html_source(source):
listing_url = source["url"]
source_name = source["name"]

listing_html, final_listing_url = fetch_url(listing_url)
listing_parser = PageParser()
listing_parser.feed(listing_html)

candidates = []
seen = set()

for anchor in listing_parser.anchors:
    url = urljoin(final_listing_url, anchor["href"])
    parsed = urlparse(url)

    if parsed.scheme != "https":
        continue
    if not is_same_site(url, final_listing_url):
        continue
    if is_listing_or_blocked(url):
        continue

    # إزالة معاملات التتبع و #fragment
    url = parsed._replace(query="", fragment="").geturl()

    if url not in seen:
        seen.add(url)
        candidates.append(url)

print(f"{source_name}: {len(candidates)} candidate links")

results = []
for detail_url in candidates[:MAX_LINKS_PER_SOURCE]:
    if len(results) >= MAX_GAMES_PER_SOURCE:
        break

    try:
        page_html, final_url = fetch_url(detail_url)

        if not is_same_site(final_url, final_listing_url):
            continue
        if is_listing_or_blocked(final_url):
            continue

        parser = PageParser()
        parser.feed(page_html)

        game = extract_game(parser, final_url, source_name)
        if game:
            results.append(game)
            print("ACCEPTED:", game["title"], final_url)
        else:
            print("SKIPPED:", final_url)

    except Exception as error:
        print(f"Could not read {detail_url}: {error}")

unique = {game["url"]: game for game in results}
print(f"{source_name}: accepted {len(unique)} probable game pages")
return list(unique.values())

def main():
if not os.path.exists(DATABASE):
raise FileNotFoundError(
"games.json is missing. Existing database was not changed."
)

with open(DATABASE, "r", encoding="utf-8") as file:
    database = json.load(file)

sources = database.get("sources", [])
if not isinstance(sources, list) or not sources:
    raise RuntimeError("No configured sources. Database was not changed.")

collected = {}
successful_sources = 0

for source in sources:
    name = source.get("name", "Unknown source")
    source_type = str(source.get("type", "")).lower()
    url = source.get("url", "")

    if not url.startswith("https://"):
        print(f"Skipping non-HTTPS source: {name}")
        continue

    try:
        if source_type not in ("happymod_html", "traidmod_html"):
            print(f"Unsupported source type: {source_type} ({name})")
            continue

        games = read_html_source(source)

        # لا نعتبر المصدر ناجحًا إلا إذا جمع صفحات ألعاب محتملة.
        if games:
            successful_sources += 1
            for game in games:
                collected[game["url"]] = game
        else:
            print(f"WARNING: {name} returned zero accepted games.")

    except Exception as error:
        print(f"Source failed: {name}: {error}")

# حماية قاعدة البيانات القديمة من الاستبدال بنتائج فارغة أو قليلة جدًا.
if successful_sources == 0 or len(collected) < 5:
    raise RuntimeError(
        f"Only {len(collected)} probable games were collected. "
        "Keeping the existing games.json unchanged. "
        "Check source pages and logs before trying again."
    )

database["games"] = list(collected.values())
database["last_checked"] = now_utc()

temp_path = DATABASE + ".tmp"
with open(temp_path, "w", encoding="utf-8") as file:
    json.dump(database, file, ensure_ascii=False, indent=2)

os.replace(temp_path, DATABASE)

print("MODVERSE update completed.")
print(f"Sources configured: {len(sources)}")
print(f"Total probable game pages: {len(collected)}")

if name == "main":
main()
