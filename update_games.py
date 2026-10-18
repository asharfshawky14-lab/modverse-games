
import html
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urldefrag

DATABASE = "games.json"
TIMEOUT = 20
MAX_LINKS_PER_SOURCE = 100
MAX_GAMES_PER_SOURCE = 60

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)

BLOCKED_SEGMENTS = {
    "games", "game", "apps", "app", "appss", "applications",
    "top-100", "blog", "request", "disclaimer", "contact", "about",
    "privacy-policy", "terms", "windows", "pc", "categories",
    "category", "search", "login", "register", "account", "author",
    "tag", "page", "files-downloaders", "ld-player-apk"
}

BLOCKED_WORDS = (
    "emulator", "ld-player", "bluestacks", "windows", "pc-game",
    "vpn", "browser", "launcher", "keyboard", "cleaner",
    "file-manager", "downloader", "video-downloader", "music-player",
    "photo-editor", "video-editor", "screen-recorder", "quran",
    "moslay", "athan", "tarteel", "whatsapp", "telegram",
    "instagram", "facebook", "tiktok", "netflix", "spotify",
    "youtube", "wallpaper", "themes", "fonts", "antivirus",
    "calculator", "weather"
)

GAME_WORDS = (
    "game", "لعبة", "العاب", "ألعاب", "لعبه", "simulator",
    "simulation", "racing", "racer", "football", "soccer",
    "basketball", "cricket", "fighting", "fight", "warrior",
    "battle", "survival", "zombie", "ninja", "adventure",
    "minecraft", "roblox", "free-fire", "pubg", "clash-of-clans",
    "clash-royale", "subway-surfers", "shadow-fight", "gta",
    "grand-theft-auto", "dream-league", "efootball", "pes", "fifa",
    "asphalt", "need-for-speed", "hill-climb", "angry-birds",
    "temple-run", "stumble-guys", "among-us", "plants-vs-zombies",
    "geometry-dash", "the-sims", "sims-", "bus-simulator",
    "truck-simulator", "car-parking", "mobile-legends",
    "call-of-duty", "brawl-stars", "hay-day", "pokemon",
    "mortal-kombat", "tekken", "terraria", "stardew", "hayday",
    "mod apk", "mod-apk"
)

VERSION_RE = re.compile(
    r"\b(?:version|ver\.?|الإصدار|نسخة)\s*[:-]?\s*"
    r"([0-9]+(?:\.[0-9A-Za-z]+){1,5})",
    re.IGNORECASE,
)

SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(KB|MB|GB)\b", re.I)

ANDROID_RE = re.compile(
    r"(?:Android|أندرويد)\s*(\d+(?:\.\d+)?(?:\+)?)", re.I
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
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8,ar;q=0.6",
        },
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        content_type = response.headers.get("Content-Type", "").lower()

        if content_type and "html" not in content_type and "xhtml" not in content_type:
            raise ValueError("URL did not return an HTML page")

        return response.read().decode("utf-8", errors="replace"), response.geturl()


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.title_depth = 0
        self.meta = {}
        self.anchors = []
        self.current_anchor = None
        self.text_parts = []
        self.skip_tags = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()

        if tag in ("script", "style", "noscript", "svg"):
            self.skip_tags.append(tag)

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
                "title": attrs.get("title", "").strip(),
            }

        if tag == "img":
            src = (
                attrs.get("data-src")
                or attrs.get("data-lazy-src")
                or attrs.get("src")
                or ""
            ).strip()

            if src:
                self.meta.setdefault("images", []).append(
                    {"src": src, "alt": attrs.get("alt", "")}
                )

    def handle_data(self, data):
        if self.skip_tags:
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

        if tag in ("script", "style", "noscript", "svg"):
            if tag in self.skip_tags:
                self.skip_tags.remove(tag)

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
    candidates = []

    for key in (
        "og:image",
        "twitter:image",
        "twitter:image:src",
        "og:image:url",
    ):
        value = parser.meta.get(key)
        if value:
            candidates.append(value)

    for item in parser.meta.get("images", []):
        src = item.get("src", "").strip()
        if src:
            candidates.append(src)

    is_happymod = normalized_host(page_url) == "happymod.to"

    for candidate in candidates:
        image_url = urljoin(page_url, candidate.strip())

        parsed = urlparse(image_url)
        if parsed.scheme not in ("http", "https"):
            continue

        image_path = parsed.path.lower()
        filename = image_path.rsplit("/", 1)[-1]

        if is_happymod:
            blocked_image_names = (
                "logo.png",
                "favicon",
                "placeholder",
                "default-avatar",
                "site-logo",
            )

            if any(name in filename for name in blocked_image_names):
                continue

            if "/static/img/logo" in image_path:
                continue

        if filename.endswith((
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"
        )):
            return image_url

    return ""


def normalized_host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_same_site(url, base_url):
    return normalized_host(url) == normalized_host(base_url)


def is_listing_or_blocked(url):
    parsed = urlparse(url)
    path = parsed.path.lower().strip("/")
    parts = [part for part in path.split("/") if part]

    if not parts:
        return True

    if any(part in BLOCKED_SEGMENTS for part in parts):
        return True

    if any(word in path for word in BLOCKED_WORDS):
        return True

    if path.endswith((
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".css",
        ".js", ".xml", ".pdf", ".zip", ".apk", ".mp4"
    )):
        return True

    if len(parts) >= 2 and parts[0] in ("games", "game", "category"):
        return True

    return False


def looks_like_game(title, url, description):
    title_lower = clean_text(title).lower()
    path = urlparse(url).path.lower()
    combined = title_lower + " " + path

    if len(title_lower) < 2 or is_listing_or_blocked(url):
        return False

    if any(word in combined for word in BLOCKED_WORDS):
        return False

    generic_titles = {
        "games", "android games", "apps", "applications", "home",
        "happymod", "traidmod", "traid mod", "ألعاب أندرويد",
        "تحميل العاب مهكرة 2026 للاندرويد apk مجاناً - ترايد مود",
    }

    if title_lower.strip(" -|") in generic_titles:
        return False

    if any(word in combined for word in GAME_WORDS):
        return True

    host = normalized_host(url)
    parts = [p for p in path.strip("/").split("/") if p]

    if host == "happymod.to" and len(parts) >= 2:
        return bool(description and len(description) >= 25)

    return False


def extract_game(parser, page_url, source_name):
    title = get_title(parser)
    description = get_description(parser)
    full_text = clean_text(" ".join(parser.text_parts))

    if not looks_like_game(title, page_url, description):
        return None

    if len(description) < 15 and len(full_text) < 100:
        return None

    combined_text = title + " " + description + " " + full_text

    version_match = VERSION_RE.search(combined_text)
    size_match = SIZE_RE.search(description + " " + full_text)
    android_match = ANDROID_RE.search(description + " " + full_text)

    return {
        "id": page_url,
        "title": title,
        "name": title,
        "description": description,
        "image": get_image(parser, page_url),
        "version": version_match.group(1) if version_match else "",
        "size": size_match.group(0) if size_match else "",
        "android": android_match.group(1) if android_match else "",
        "url": page_url,
        "detail_url": page_url,
        "download_url": page_url,
        "source": source_name,
        "category": "ألعاب أندرويد",
        "versions": [],
        "updated_at": now_utc(),
    }


def read_html_source(source):
    listing_html, listing_url = fetch_url(source["url"])
    listing_parser = PageParser()
    listing_parser.feed(listing_html)

    candidates = []
    seen = set()

    for anchor in listing_parser.anchors:
        url = urljoin(listing_url, anchor["href"])
        parsed = urlparse(url)

        if parsed.scheme != "https" or not is_same_site(url, listing_url):
            continue

        if is_listing_or_blocked(url):
            continue

        url = urldefrag(parsed._replace(query="").geturl())[0]

        if url not in seen:
            seen.add(url)
            candidates.append(url)

    print(f'{source["name"]}: found {len(candidates)} candidate links')

    results = []

    for detail_url in candidates[:MAX_LINKS_PER_SOURCE]:
        if len(results) >= MAX_GAMES_PER_SOURCE:
            break

        try:
            page_html, final_url = fetch_url(detail_url)

            if not is_same_site(final_url, listing_url):
                continue

            if is_listing_or_blocked(final_url):
                continue

            parser = PageParser()
            parser.feed(page_html)

            game = extract_game(parser, final_url, source["name"])

            if game:
                results.append(game)
                print("ACCEPTED:", game["title"], final_url)

        except Exception as error:
            print(f"Could not read {detail_url}: {error}")

    unique = {game["url"]: game for game in results}

    print(f'{source["name"]}: accepted {len(unique)} game pages')

    return list(unique.values())


def main():
    if not os.path.exists(DATABASE):
        raise FileNotFoundError(
            "games.json is missing; database unchanged."
        )

    with open(DATABASE, "r", encoding="utf-8") as file:
        database = json.load(file)

    sources = database.get("sources", [])

    if not isinstance(sources, list) or not sources:
        raise RuntimeError(
            "No configured sources; database unchanged."
        )

    collected = {}

    for source in sources:
        source_name = source.get("name", "Unknown source")
        source_type = str(source.get("type", "")).lower()

        if source_type not in ("happymod_html", "traidmod_html"):
            print(
                f"Skipping unsupported source type: "
                f"{source_type} ({source_name})"
            )
            continue

        if not str(source.get("url", "")).startswith("https://"):
            print(f"Skipping non-HTTPS source: {source_name}")
            continue

        try:
            for game in read_html_source(source):
                collected[game["url"]] = game

        except Exception as error:
            print(f"Source failed: {source_name}: {error}")

    if len(collected) < 5:
        raise RuntimeError(
            f"Only {len(collected)} game pages collected. "
            "Keeping the existing games.json unchanged."
        )

    database["games"] = list(collected.values())
    database["last_checked"] = now_utc()

    temp_path = DATABASE + ".tmp"

    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(database, file, ensure_ascii=False, indent=2)
        file.write("\n")

    os.replace(temp_path, DATABASE)

    print("MODVERSE update completed.")
    print(f"Total game pages collected: {len(collected)}")


if __name__ == "__main__":
    main()
