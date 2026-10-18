
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DATABASE = "games.json"
TIMEOUT = 40


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default):
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def fetch_url(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "MODVERSE-GameCatalog/1.1"}
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


def normalize_game(item, source_name):
    if not isinstance(item, dict):
        return None

    title = (
        item.get("title")
        or item.get("name")
        or item.get("app_name")
    )

    link = (
        item.get("url")
        or item.get("link")
        or item.get("download_url")
        or item.get("homepage")
    )

    if not title or not link:
        return None

    return {
        "id": str(item.get("id") or link).strip(),
        "title": str(title).strip(),
        "description": str(
            item.get("description", "")
        ).strip(),
        "version": str(item.get("version", "")),
        "image": str(
            item.get("image")
            or item.get("image_url")
            or item.get("icon")
            or ""
        ),
        "url": str(link).strip(),
        "source": source_name,
        "category": str(
            item.get("category") or item.get("genre") or "أخرى"
        ),
        "updated_at": now_utc()
    }


def read_json_feed(data, source):
    parsed = json.loads(data.decode("utf-8"))
    path = source.get("items_path", "games")

    if isinstance(parsed, list):
        items = parsed
    else:
        items = parsed

        for key in path.split("."):
            if isinstance(items, dict):
                items = items.get(key, [])
            else:
                items = []
                break

    if not isinstance(items, list):
        return []

    result = []

    for item in items:
        game = normalize_game(item, source["name"])
        if game:
            result.append(game)

    return result


def read_rss_feed(data, source):
    root = ET.fromstring(data)
    results = []

    for item in root.iter():
        tag = item.tag.split("}")[-1].lower()

        if tag not in ("item", "entry"):
            continue

        fields = {}

        for child in item:
            key = child.tag.split("}")[-1].lower()
            value = (child.text or "").strip()

            if key == "link":
                value = child.attrib.get("href", value)

            fields[key] = value

        game = normalize_game({
            "title": fields.get("title"),
            "description": (
                fields.get("description")
                or fields.get("summary", "")
            ),
            "url": fields.get("link") or fields.get("id"),
            "category": fields.get("category", "أخرى")
        }, source["name"])

        if game:
            results.append(game)

    return results


def read_fdroid_games(data, source):
    """
    Read the official F-Droid index-v1 JSON.
    Only include apps explicitly categorized as Games.
    """
    parsed = json.loads(data.decode("utf-8"))
    apps = parsed.get("apps", [])

    if not isinstance(apps, list):
        raise ValueError("F-Droid index has no valid apps list")

    results = []

    for app in apps:
        if not isinstance(app, dict):
            continue

        categories = app.get("categories", [])

        if isinstance(categories, str):
            categories = [
                value.strip()
                for value in categories.split(",")
            ]

        if not isinstance(categories, list):
            continue

        if not any(
            str(category).strip().lower() == "games"
            for category in categories
        ):
            continue

        package_name = app.get("packageName", "")
        if not package_name:
            continue

        localized = app.get("localized", {})
        english = localized.get("en-US", {}) if isinstance(
            localized, dict
        ) else {}

        title = (
            app.get("name")
            or english.get("name")
            or english.get("title")
            or package_name
        )

        description = (
            app.get("summary")
            or english.get("short_description")
            or english.get("summary")
            or ""
        )

        version = (
            app.get("suggestedVersionName")
            or app.get("marketversion")
            or ""
        )

        game = normalize_game({
            "id": package_name,
            "title": title,
            "description": description,
            "version": version,
            "url": "https://f-droid.org/packages/"
                   + package_name + "/",
            "category": "ألعاب",
            "image": ""
        }, source.get("name", "F-Droid"))

        if game:
            results.append(game)

    return results


def main():
    database = load_json(
        DATABASE,
        {
            "site": "MODVERSE",
            "version": 1,
            "sources": [],
            "games": []
        }
    )

    sources = database.get("sources", [])
    old_games = database.get("games", [])
    collected = {}

    for game in old_games:
        if isinstance(game, dict) and game.get("url"):
            collected[game["url"]] = game

    successful_sources = 0

    for source in sources:
        name = source.get("name", "Unknown source")
        url = source.get("url", "")
        feed_type = source.get("type", "json").lower()

        if not url.startswith("https://"):
            print(f"Skipping non-HTTPS source: {name}")
            continue

        try:
            data = fetch_url(url)

            if feed_type == "rss":
                games = read_rss_feed(data, source)
            elif feed_type == "json":
                games = read_json_feed(data, source)
            elif feed_type == "fdroid":
                games = read_fdroid_games(data, source)
            else:
                print(f"Unsupported feed type: {feed_type}")
                continue

            successful_sources += 1

            for game in games:
                collected[game["url"]] = game

            print(f"{name}: collected {len(games)} games")

        except Exception as error:
            print(f"Source failed: {name}: {error}")

    if sources and successful_sources == 0:
        raise RuntimeError(
            "All sources failed. Keeping games.json unchanged."
        )

    database["games"] = list(collected.values())
    database["last_checked"] = now_utc()

    with open(DATABASE, "w", encoding="utf-8") as file:
        json.dump(
            database,
            file,
            ensure_ascii=False,
            indent=2
        )

    print("MODVERSE collection finished.")
    print(f"Total catalog entries: {len(collected)}")


if __name__ == "__main__":
    main()
