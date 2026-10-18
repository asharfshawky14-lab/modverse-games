
import json
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DATABASE = "games.json"
TIMEOUT = 25


def load_json(path, default):
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def fetch_url(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "MODVERSE-GameCatalog/1.0"
        }
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
        "id": str(link).strip(),
        "title": str(title).strip(),
        "description": str(
            item.get("description", "")
        ).strip(),
        "version": str(item.get("version", "")),
        "image": (
            item.get("image")
            or item.get("image_url")
            or item.get("icon")
            or ""
        ),
        "url": str(link).strip(),
        "source": source_name,
        "updated_at": datetime.now(
            timezone.utc
        ).isoformat()
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

    return [
        game
        for item in items
        if (game := normalize_game(item, source["name"]))
    ]


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
            "description": fields.get("description")
                or fields.get("summary", ""),
            "url": fields.get("link")
                or fields.get("id")
        }, source["name"])

        if game:
            results.append(game)

    return results


def main():
    database = load_json(
        DATABASE,
        {"site": "MODVERSE", "sources": [], "games": []}
    )

    sources = database.get("sources", [])
    old_games = database.get("games", [])
    collected = {}

    for game in old_games:
        if isinstance(game, dict) and game.get("url"):
            collected[game["url"]] = game

    for source in sources:
        name = source.get("name", "Unknown source")
        url = source.get("url", "")
        feed_type = source.get("type", "json").lower()

        if not url.startswith("https://"):
            print(f"Skipping invalid HTTPS source: {name}")
            continue

        try:
            data = fetch_url(url)

            if feed_type == "rss":
                games = read_rss_feed(data, source)
            elif feed_type == "json":
                games = read_json_feed(data, source)
            else:
                print(f"Unsupported feed type: {feed_type}")
                continue

            for game in games:
                collected[game["url"]] = game

            print(f"{name}: collected {len(games)} entries")

        except Exception as error:
            print(f"Source failed: {name}: {error}")

    database["games"] = list(collected.values())
    database["last_checked"] = datetime.now(
        timezone.utc
    ).isoformat()

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
