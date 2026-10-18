
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
MAX_GAME_PAGES_PER_SOURCE = 60
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)

EXCLUDED_PATH_PARTS = (
    "/category/", "/tag/", "/author/", "/page/",
    "/search", "/privacy", "/contact", "/about",
    "/login", "/register", "/download-app"
)

DOWNLOAD_WORDS = (
    "download", "تحميل", "تنزيل", "apk", "get it"
)

VERSION_RE = re.compile(
    r"\b(?:version|ver\.?|الإصدار|نسخة)\s*[:\-]?\s*"
    r"([0-9]+(?:\.[0-9A-Za-z]+){1,5})",
    re.IGNORECASE
)

SIZE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(KB|MB|GB)\b",
    re.IGNORECASE
)

ANDROID_RE = re.compile(
    r"(?:Android|أندرويد)\s*(\d+(?:\.\d+)?(?:\+|以上)?)",
    re.IGNORECASE
)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def fetch_url(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8,ar;q=0.6"
        }
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.title_depth = 0
        self.meta = {}
        self.anchors = []
        self.current_anchor = None
        self.text_parts = []
        self.in_script_style = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.lower()

        if tag in ("script", "style", "noscript"):
            self.in_script_style += 1

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
            alt = attrs.get("alt", "").strip()
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
                    "alt": alt
                })

    def handle_data(self, data):
        if self.in_script_style:
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

        if tag in ("script", "style", "noscript") and self.in_script_style:
            self.in_script_style -= 1

        if tag == "title" and self.title_depth:
            self.title_depth -= 1

        if tag == "a" and self.current_anchor is not None:
            self.current_anchor["text"] = clean_text(
                self.current_anchor["text"]
            )
            self.anchors.append(self.current_anchor)
            self.current_anchor = None


def clean_text(value):
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def same_site(url, base_url):
    try:
        return urlparse(url).netloc.lower() == urlparse(base_url).netloc.lower()
    except Exception:
        return False


def is_probable_game_link(url, base_url, anchor_text=""):
    parsed = urlparse(url)
    path = parsed.path.lower()

    if not same_site(url, base_url):
        return False

    if not path or path == "/":
        return False

    if any(part in path for part in EXCLUDED_PATH_PARTS):
        return False

    if any(path.endswith(ext) for ext in (
        ".jpg", ".jpeg", ".png", ".webp", ".gif",
        ".css", ".js", ".xml", ".pdf", ".zip", ".apk"
    )):
        return False

    if any(word in path for word in (
        "privacy-policy", "terms-of-use", "wp-content",
        "wp-admin", "wp-json", "feed"
    )):
        return False

    text = clean_text(anchor_text).lower()
    if text in ("home", "next", "previous", "read more", "more"):
        return False

    return True


def page_title(parser):
    return clean_text(
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or parser.title
    )


def page_description(parser):
    return clean_text(
        parser.meta.get("description")
        or parser.meta.get("og:description")
        or parser.meta.get("twitter:description")
    )


def page_image(parser, base_url):
    image = (
        parser.meta.get("og:image")
        or parser.meta.get("twitter:image")
        or parser.meta.get("twitter:image:src")
    )
    if not image and parser.meta.get("images"):
        for item in parser.meta["images"]:
            if item.get("src"):
                image = item["src"]
                break
    return urljoin(base_url, image) if image else ""


def extract_details(parser, page_url):
    title = page_title(parser)
    description = page_description(parser)
    full_text = clean_text(" ".join(parser.text_parts))

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

    download_url = ""
    for anchor in parser.anchors:
        text = clean_text(
            anchor.get("text", "") + " " + anchor.get("title", "")
        ).lower()
        href = urljoin(page_url, anchor.get("href", ""))

        if href.startswith("https://") and any(
            word in text for word in DOWNLOAD_WORDS
        ):
            download_url = href
            break

    return {
        "title": title,
        "description": description,
        "image": page_image(parser, page_url),
        "version": version,
        "size": size,
        "android": android,
        "download_url": download_url
    }


def read_html_source(source):
    listing_url = source["url"]
    source_name = source["name"]

    listing_html = fetch_url(listing_url)
    listing_parser = PageParser()
    listing_parser.feed(listing_html)

    base_host = urlparse(listing_url).netloc.lower()
    game_urls = []

    for anchor in listing_parser.anchors:
        url = urljoin(listing_url, anchor["href"])
        if not url.startswith("https://"):
            continue
        if not is_probable_game_link(url, listing_url, anchor["text"]):
            continue
        if url not in game_urls:
            game_urls.append(url)

    # لو صفحة المصدر لم تعرض روابط ألعاب واضحة، لا نضيف بيانات مخمّنة.
    results = []

    for detail_url in game_urls[:MAX_GAME_PAGES_PER_SOURCE]:
        try:
            detail_html = fetch_url(detail_url)
            parser = PageParser()
            parser.feed(detail_html)
            details = extract_details(parser, detail_url)

            title = details["title"]
            if not title:
                continue

            # نستبعد الصفحات التي تبدو كتصنيفات أو صفحات عامة.
            title_lower = title.lower()
            if title_lower in (
                "home", "happymod", "traidmod", "traid mod",
                "games", "android games"
            ):
                continue

            results.append({
                "id": detail_url,
                "title": title,
                "name": title,
                "description": details["description"],
                "image": details["image"],
                "version": details["version"],
                "size": details["size"],
                "android": details["android"],
                "url": detail_url,
                "detail_url": detail_url,
                "download_url": details["download_url"] or detail_url,
                "source": source_name,
                "category": "ألعاب ومودات",
                "versions": [
                    {
                        "version": details["version"],
                        "size": details["size"],
                        "android": details["android"],
                        "url": details["download_url"] or detail_url
                    }
                ] if (
                    details["version"]
                    or details["size"]
                    or details["android"]
                    or details["download_url"]
                ) else [],
                "updated_at": now_utc()
            })

        except Exception as error:
            print(f"Could not read game page {detail_url}: {error}")

    # إزالة التكرار باستخدام رابط صفحة اللعبة.
    unique = {}
    for game in results:
        unique[game["url"]] = game

    print(
        f"{source_name}: found {len(game_urls)} links, "
        f"collected {len(unique)} game pages"
    )
    return list(unique.values())


def main():
    if not os.path.exists(DATABASE):
        raise FileNotFoundError(
            "games.json is missing. Database was not changed."
        )

    with open(DATABASE, "r", encoding="utf-8") as file:
        database = json.load(file)

    sources = database.get("sources", [])
    if not isinstance(sources, list) or not sources:
        raise RuntimeError(
            "No configured sources. Database was not changed."
        )

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
            if source_type in ("happymod_html", "traidmod_html"):
                games = read_html_source(source)
            else:
                print(f"Unsupported source type: {source_type} ({name})")
                continue

            successful_sources += 1
            for game in games:
                collected[game["url"]] = game

        except Exception as error:
            print(f"Source failed: {name}: {error}")

    if successful_sources == 0:
        raise RuntimeError(
            "All configured sources failed. Database was not changed."
        )

    if not collected:
        raise RuntimeError(
            "Sources responded, but no game pages were collected. "
            "Database was not changed."
        )

    # استبدال الكتالوج القديم بنتائج المصادر الجديدة فقط.
    database["games"] = list(collected.values())
    database["last_checked"] = now_utc()

    temp_path = DATABASE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(database, file, ensure_ascii=False, indent=2)

    os.replace(temp_path, DATABASE)

    print("MODVERSE update completed.")
    print(f"Sources configured: {len(sources)}")
    print(f"Total games collected: {len(collected)}")


if __name__ == "__main__":
    main()
