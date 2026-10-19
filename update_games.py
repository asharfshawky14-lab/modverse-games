
import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import (
    urljoin, urlparse, urldefrag, parse_qs, urlunparse
)

DATABASE = "games.json"
TIMEOUT = 20
MAX_LISTING_PAGES_PER_SOURCE = 12
MAX_LINKS_PER_SOURCE = 350
MAX_GAMES_PER_SOURCE = 100
MAX_VERSION_CHECKS_PER_RUN = 50
DELAY_SECONDS = 0.25

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)

VERSION_RE = re.compile(
    r"\b(?:version|ver\.?|v|الإصدار|نسخة)\s*[:-]?\s*"
    r"([0-9]+(?:\.[0-9A-Za-z]+){1,5})",
    re.IGNORECASE,
)
SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(KB|MB|GB)\b", re.I)
ANDROID_RE = re.compile(
    r"(?:Android|أندرويد)\s*(\d+(?:\.\d+)?(?:\+)?)", re.I
)

BLOCKED_WORDS = (
    "emulator", "ld-player", "bluestacks", "windows", "vpn",
    "browser", "launcher", "keyboard", "cleaner", "downloader",
    "video-downloader", "music-player", "photo-editor",
    "video-editor", "screen-recorder", "whatsapp", "telegram",
    "instagram", "facebook", "tiktok", "netflix", "spotify",
    "youtube", "wallpaper", "themes", "fonts", "antivirus",
    "calculator", "weather",
)

GAME_WORDS = (
    "game", "لعبة", "العاب", "ألعاب", "لعبه", "simulator",
    "simulation", "racing", "racer", "football", "soccer",
    "basketball", "cricket", "fighting", "warrior", "battle",
    "survival", "zombie", "ninja", "adventure", "minecraft",
    "roblox", "free-fire", "pubg", "clash-of-clans",
    "clash-royale", "subway-surfers", "shadow-fight", "gta",
    "grand-theft-auto", "dream-league", "efootball", "pes",
    "fifa", "asphalt", "need-for-speed", "hill-climb",
    "angry-birds", "temple-run", "stumble-guys", "among-us",
    "plants-vs-zombies", "geometry-dash", "the-sims",
    "bus-simulator", "truck-simulator", "car-parking",
    "mobile-legends", "call-of-duty", "brawl-stars",
    "hay-day", "pokemon", "mortal-kombat", "tekken",
    "terraria", "stardew", "mod apk", "mod-apk",
)

ARTICLE_WORDS = (
    "how to", "how do i", "how can i", "how do you",
    "guide", "tutorial", "faq", "tips and tricks",
    "find your username", "forgot password",
    "sign in", "sign up", "log in", "login",
    "register", "registration", "account settings",
    "help center", "contact us", "privacy policy",
    "terms of service", "what is happymod",
    "كيفية", "كيف تجد", "كيفيه", "طريقة التسجيل",
    "كيفية التسجيل", "تسجيل الدخول", "نسيت كلمة المرور",
    "استعادة الحساب", "شرح", "دليل", "أسئلة شائعة",
    "كيفيه تسجيل", "طريقة تسجيل",
)

BLOCKED_PATH_PARTS = (
    "/login", "/register", "/account", "/privacy",
    "/terms", "/contact", "/about", "/author/",
    "/tag/", "/category/", "/search", "/downloaders/",
    "/security",
)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value):
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def normalized_host(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_same_site(url, base_url):
    host = normalized_host(url)
    base_host = normalized_host(base_url)

    happymod_hosts = {"happymod.to", "ardown.happymod.to"}

    if host in happymod_hosts and base_host in happymod_hosts:
        return True

    return host == base_host


def canonical_url(url):
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    host = parsed.netloc.lower()

    if host in ("happymod.to", "www.happymod.to"):
        host = "ardown.happymod.to"

    return urlunparse((
        parsed.scheme.lower(), host, path, "", "", ""
    ))


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

        if content_type and not (
            "html" in content_type or "xhtml" in content_type
        ):
            raise ValueError("The URL did not return an HTML page.")

        body = response.read().decode("utf-8", errors="replace")
        return body, response.geturl()


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.title_depth = 0
        self.meta = {}
        self.anchors = []
        self.current_anchor = None
        self.text_parts = []
        self.skip_depth = 0
        self.images = []

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
                self.images.append({
                    "src": src,
                    "alt": attrs.get("alt", ""),
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

        if tag in ("script", "style", "noscript", "svg"):
            if self.skip_depth:
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
    candidates = [
        parser.meta.get("og:image", ""),
        parser.meta.get("twitter:image", ""),
        parser.meta.get("twitter:image:src", ""),
        parser.meta.get("og:image:url", ""),
    ]
    candidates.extend(item["src"] for item in parser.images)

    for candidate in candidates:
        if not candidate:
            continue

        image_url = urljoin(page_url, candidate.strip())
        parsed = urlparse(image_url)

        if parsed.scheme not in ("http", "https"):
            continue

        filename = parsed.path.lower().rsplit("/", 1)[-1]

        if any(word in filename for word in (
            "logo", "favicon", "placeholder", "default-avatar"
        )):
            continue

        if filename.endswith((
            ".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"
        )):
            return image_url

    return ""


def is_blocked_url(url):
    parsed = urlparse(url)
    path = parsed.path.lower().rstrip("/")

    if parsed.scheme not in ("https", "http"):
        return True

    if any(part in path for part in BLOCKED_PATH_PARTS):
        return True

    if re.fullmatch(r"/games/[^/]+", path):
        return True

    if path in (
        "/game-mod.html", "/app-mod.html", "/new-mod.html"
    ):
        return True

    if re.fullmatch(r"/[a-z0-9-]+/\d+", path):
        return True

    if path.endswith((
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".css",
        ".js", ".xml", ".pdf", ".zip", ".apk", ".mp4",
    )):
        return True

    return False


def looks_like_listing_url(url, anchor_text=""):
    parsed = urlparse(url)
    path = parsed.path.lower()
    text = clean_text(anchor_text).lower()
    query = parse_qs(parsed.query)

    if any(key.lower() in (
        "page", "paged", "p", "offset", "start"
    ) for key in query):
        return True

    if re.search(r"/(?:page|paged)/\d+/?$", path):
        return True

    if re.search(r"/\d+/?$", path) and (
        "next" in text or "older" in text or "التالي" in text
    ):
        return True

    if any(word in text for word in (
        "next", "older posts", "load more", "التالي",
        "المزيد", "الصفحة التالية",
    )):
        return True

    return False


def looks_like_game(title, url, description):
    title_lower = clean_text(title).lower()
    path = urlparse(url).path.lower()
    combined = title_lower + " " + path

    if len(title_lower) < 2 or is_blocked_url(url):
        return False

    # Reject article/help titles before considering game keywords.
    if any(word in title_lower for word in ARTICLE_WORDS):
        return False

    if any(word in combined for word in BLOCKED_WORDS):
        return False

    generic_titles = {
        "games", "android games", "apps", "applications",
        "home", "happymod", "traidmod", "traid mod",
        "ألعاب أندرويد",
    }

    if title_lower.strip(" -|") in generic_titles:
        return False

    if any(word in combined for word in GAME_WORDS):
        return True

    # Keep the original HappyMod fallback for possible game pages.
    return (
        normalized_host(url) in (
            "happymod.to", "ardown.happymod.to"
        )
        and len([part for part in path.split("/") if part]) >= 2
        and len(description) >= 25
    )


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

    game_url = canonical_url(page_url)

    return {
        "id": game_url,
        "title": title,
        "name": title,
        "description": description,
        "image": get_image(parser, page_url),
        "version": version_match.group(1) if version_match else "",
        "size": size_match.group(0) if size_match else "",
        "android": android_match.group(1) if android_match else "",
        "url": game_url,
        "detail_url": game_url,
        "download_url": game_url,
        "source": source_name,
        "category": "ألعاب أندرويد",
        "versions": [],
        "updated_at": now_utc(),
    }


def collect_listing_links(parser, listing_url, base_url):
    detail_urls = []
    listing_urls = []

    for anchor in parser.anchors:
        href = anchor["href"].strip()
        if not href:
            continue

        url = urljoin(listing_url, href)
        url, _fragment = urldefrag(url)

        if not is_same_site(url, base_url):
            continue

        if urlparse(url).scheme != "https":
            continue

        if looks_like_listing_url(url, anchor.get("text", "")):
            listing_urls.append(url)
        elif not is_blocked_url(url):
            detail_urls.append(url)

    return detail_urls, listing_urls


def read_html_source(source):
    source_url = source["url"]
    source_name = source.get("name", "Unknown source")

    listing_queue = [source_url]
    seen_listing = set()
    detail_queue = []
    seen_details = set()
    games = {}

    while listing_queue and len(seen_listing) < MAX_LISTING_PAGES_PER_SOURCE:
        listing_url = listing_queue.pop(0)
        listing_key = canonical_url(listing_url)

        if listing_key in seen_listing:
            continue

        seen_listing.add(listing_key)

        try:
            page_html, final_url = fetch_url(listing_url)

            if not is_same_site(final_url, source_url):
                continue

            parser = PageParser()
            parser.feed(page_html)

            details, more_listings = collect_listing_links(
                parser, final_url, source_url
            )

            for url in details:
                key = canonical_url(url)
                if key not in seen_details:
                    seen_details.add(key)
                    detail_queue.append(url)

            for url in more_listings:
                key = canonical_url(url)
                if key not in seen_listing and len(
                    listing_queue
                ) + len(seen_listing) < MAX_LISTING_PAGES_PER_SOURCE:
                    listing_queue.append(url)

            print(
                f"{source_name}: listing "
                f"{len(seen_listing)}/{MAX_LISTING_PAGES_PER_SOURCE}"
            )

        except Exception as error:
            print(f"Could not read listing {listing_url}: {error}")

    print(f"{source_name}: {len(detail_queue)} candidate detail links")

    for detail_url in detail_queue[:MAX_LINKS_PER_SOURCE]:
        if len(games) >= MAX_GAMES_PER_SOURCE:
            break

        try:
            page_html, final_url = fetch_url(detail_url)

            if not is_same_site(final_url, source_url):
                continue

            parser = PageParser()
            parser.feed(page_html)

            game = extract_game(parser, final_url, source_name)

            if game:
                games[game["url"]] = game
                print("FOUND:", game["title"], game["url"])

        except Exception as error:
            print(f"Could not read game page {detail_url}: {error}")

        time.sleep(DELAY_SECONDS)

    print(f"{source_name}: collected {len(games)} games")
    return list(games.values())


def game_key(game):
    for field in ("url", "detail_url", "id"):
        value = str(game.get(field, "")).strip()
        if value.startswith(("https://", "http://")):
            return canonical_url(value)

    title = clean_text(game.get("title") or game.get("name")).lower()
    source = clean_text(game.get("source")).lower()
    return f"title:{source}:{title}"


def merge_game(old_game, new_game):
    """Update fields without wiping previously saved information."""
    merged = dict(old_game)

    old_version = str(old_game.get("version") or "").strip()
    new_version = str(new_game.get("version") or "").strip()

    old_versions = old_game.get("versions", [])
    if not isinstance(old_versions, list):
        old_versions = []

    history = list(old_versions)

    if old_version and not any(
        isinstance(item, dict) and item.get("version") == old_version
        for item in history
    ):
        history.append({
            "version": old_version,
            "recorded_at": old_game.get("updated_at") or now_utc(),
        })

    if new_version and new_version != old_version:
        history.append({
            "version": new_version,
            "recorded_at": now_utc(),
        })

    for key, value in new_game.items():
        if value not in ("", None, [], {}):
            merged[key] = value
        elif key not in merged:
            merged[key] = value

    merged["versions"] = history
    merged["id"] = merged.get("id") or game_key(merged)
    merged["updated_at"] = now_utc()

    return merged


def check_existing_versions(games, start_index):
    """Check a rotating batch of old game pages each run."""
    if not games:
        return games, start_index

    count = min(MAX_VERSION_CHECKS_PER_RUN, len(games))
    checked = 0
    index = start_index % len(games)

    while checked < count:
        game = games[index]
        url = (
            game.get("detail_url")
            or game.get("url")
            or game.get("id")
            or ""
        )

        if isinstance(url, str) and url.startswith("https://"):
            try:
                page_html, final_url = fetch_url(url)
                parser = PageParser()
                parser.feed(page_html)

                refreshed = extract_game(
                    parser, final_url,
                    game.get("source", "Unknown source"),
                )

                if refreshed:
                    games[index] = merge_game(game, refreshed)
                    print(
                        "VERSION CHECKED:",
                        games[index].get("title", ""),
                        games[index].get("version", "unknown"),
                    )

            except Exception as error:
                print(
                    "Version check failed for",
                    game.get("title", url), ":", error,
                )

            time.sleep(DELAY_SECONDS)

        checked += 1
        index = (index + 1) % len(games)

    return games, index


def main():
    if not os.path.exists(DATABASE):
        raise FileNotFoundError(
            "games.json is missing; existing data was not changed."
        )

    with open(DATABASE, "r", encoding="utf-8") as file:
        database = json.load(file)

    if not isinstance(database, dict):
        raise ValueError("games.json must contain a JSON object.")

    old_games = database.get("games", [])
    if not isinstance(old_games, list):
        old_games = []

    # Preserve existing entries from every configured source.
    # Do not discard a game's saved record just because its source
    # is not HappyMod.
    cleaned_old_games = []

    for game in old_games:
        if not isinstance(game, dict):
            continue

        game = dict(game)
        game_url = (
            game.get("detail_url")
            or game.get("url")
            or game.get("id")
            or ""
        )

        if isinstance(game_url, str) and game_url.startswith(
            ("https://", "http://")
        ):
            for field in ("id", "url", "detail_url", "download_url"):
                value = game.get(field)
                if isinstance(value, str) and value.startswith(
                    ("https://", "http://")
                ):
                    game[field] = canonical_url(value)

        cleaned_old_games.append(game)

    old_games = cleaned_old_games
    merged_games = {}

    for game in old_games:
        key = game_key(game)
        if key in merged_games:
            merged_games[key] = merge_game(merged_games[key], game)
        else:
            merged_games[key] = game

    sources = database.get("sources", [])
    if not isinstance(sources, list) or not sources:
        raise RuntimeError(
            "No configured sources. Existing database was not changed."
        )

    collected_total = 0
    source_status = {}

    for source in sources:
        source_name = source.get("name", "Unknown source")
        source_type = str(source.get("type", "")).lower()

        if source_type not in ("happymod_html", "traidmod_html"):
            print("Skipping unsupported source:", source_name)
            source_status[source_name] = {
                "status": "skipped", "checked_at": now_utc()
            }
            continue

        if not str(source.get("url", "")).startswith("https://"):
            print("Skipping non-HTTPS source:", source_name)
            source_status[source_name] = {
                "status": "invalid_url", "checked_at": now_utc()
            }
            continue

        try:
            found_games = read_html_source(source)

            for new_game in found_games:
                key = game_key(new_game)

                if key in merged_games:
                    merged_games[key] = merge_game(
                        merged_games[key], new_game
                    )
                else:
                    merged_games[key] = new_game
                    print("NEW GAME ADDED:", new_game.get("title", ""))

                collected_total += 1

            source_status[source_name] = {
                "status": "success",
                "games_found": len(found_games),
                "checked_at": now_utc(),
            }

        except Exception as error:
            print(f"Source failed: {source_name}: {error}")
            source_status[source_name] = {
                "status": "failed",
                "error": str(error)[:300],
                "checked_at": now_utc(),
            }

    games = list(merged_games.values())

    cursor = database.get("version_check_cursor", 0)
    try:
        cursor = int(cursor)
    except (TypeError, ValueError):
        cursor = 0

    games, next_cursor = check_existing_versions(games, cursor)

    # Safety: do not replace a populated catalog with an empty list.
    if old_games and not games:
        raise RuntimeError(
            "Safety check failed: refusing to replace the catalog "
            "with an empty list."
        )

    database["games"] = games
    database["last_checked"] = now_utc()
    database["version_check_cursor"] = next_cursor
    database["source_status"] = source_status
    database["last_update_summary"] = {
        "previous_games": len(old_games),
        "total_saved_games": len(games),
        "pages_collected_this_run": collected_total,
        "version_checks_per_run": min(
            MAX_VERSION_CHECKS_PER_RUN, len(games)
        ),
        "checked_at": now_utc(),
    }

    temp_path = DATABASE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(database, file, ensure_ascii=False, indent=2)
        file.write("\n")

    os.replace(temp_path, DATABASE)

    print("MODVERSE update completed.")
    print("Previously saved games:", len(old_games))
    print("Games found this run:", collected_total)
    print("Total games preserved:", len(games))
    print("Next version-check cursor:", next_cursor)


if __name__ == "__main__":
    main()
