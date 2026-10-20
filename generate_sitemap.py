
import json
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

BASE_URL = "https://asharfshawky14-lab.github.io/modverse-games/"
GAMES_FILE = Path("games.json")
SITEMAP_FILE = Path("sitemap.xml")


def main():
    if not GAMES_FILE.exists():
        raise FileNotFoundError("games.json was not found")

    data = json.loads(GAMES_FILE.read_text(encoding="utf-8"))

    if isinstance(data, list):
        games = data
    elif isinstance(data, dict):
        games = data.get("games", [])
    else:
        raise ValueError("Unsupported games.json format")

    if not isinstance(games, list):
        raise ValueError("The games field must be a list")

    urls = [
        BASE_URL,
        BASE_URL + "index.html",
    ]

    for game in games:
        if not isinstance(game, dict):
            continue

        game_id = (
            game.get("id")
            or game.get("url")
            or game.get("detail_url")
        )

        if game_id is None or not str(game_id).strip():
            continue

        encoded_id = quote(
            str(game_id).strip(),
            safe="~()*!.'-"
        )

        urls.append(f"{BASE_URL}game.html?id={encoded_id}")

    # إزالة الروابط المكررة مع الاحتفاظ بالترتيب
    urls = list(dict.fromkeys(urls))

    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    ]

    for url in urls:
        xml_lines.extend([
            "  <url>",
            f"    <loc>{escape(url)}</loc>",
            "  </url>"
        ])

    xml_lines.append("</urlset>")

    SITEMAP_FILE.write_text(
        "\n".join(xml_lines) + "\n",
        encoding="utf-8"
    )

    print(f"Generated sitemap.xml with {len(urls)} URLs")


if __name__ == "__main__":
    main()
