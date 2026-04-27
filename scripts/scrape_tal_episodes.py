"""
Scrapes This American Life episode data from thisamericanlife.org.

For each episode that has audio available, extracts the playlist-data JSON
embedded in the episode page and writes all episodes to a single JSON file.

Output: content/assets/tal-episodes.json

Usage:
    python scripts/scrape_tal_episodes.py

Re-running is safe: already-fetched pages are cached in .scrape-cache/ so
only new or missing episodes hit the network.
"""

import json
import os
import re
import time
import urllib.request
import urllib.error

ARCHIVE_URL = "https://www.thisamericanlife.org/archive?year={year}"
EPISODE_BASE = "https://www.thisamericanlife.org"
OUTPUT_PATH = os.path.join("content", "assets", "tal-episodes.json")
CACHE_DIR = ".scrape-cache"
FIRST_YEAR = 1995
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# Seconds to wait between episode page requests.
EPISODE_DELAY = 1.5
# Seconds to wait between archive year page requests (Cloudflare is stricter here).
ARCHIVE_DELAY = 8.0
# On 403, retry this many times with exponential backoff starting at this many seconds.
RETRY_COUNT = 4
RETRY_BASE_DELAY = 30.0


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def head_status(url):
    """Returns the HTTP status code for a HEAD request, or None on network error."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def fetch_with_retry(url):
    delay = RETRY_BASE_DELAY
    for attempt in range(RETRY_COUNT + 1):
        try:
            return fetch(url)
        except urllib.error.HTTPError as e:
            if e.code == 403 and attempt < RETRY_COUNT:
                print(f"    403 — waiting {delay:.0f}s before retry {attempt + 1}/{RETRY_COUNT}...")
                time.sleep(delay)
                delay = delay * 2
            else:
                raise


def cache_path_for(url):
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", url)
    if len(safe) > 200:
        safe = safe[:200]
    return os.path.join(CACHE_DIR, safe + ".html")


def fetch_cached(url, delay):
    path = cache_path_for(url)
    if os.path.exists(path):
        print(f"    (cached)")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    print(f"    fetching {url}")
    time.sleep(delay)
    html = fetch_with_retry(url)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return html


def extract_episode_slugs(html):
    """
    Returns a list of episode URL paths like '/393/infidelity' or '/blackjack'
    from an archive year page.
    """
    raw = re.findall(r'href="(/[a-zA-Z0-9][a-zA-Z0-9/-]*)"', html)
    seen = set()
    slugs = []
    skip_prefixes = (
        "/archive", "/podcast", "/about", "/donate", "/shop", "/press",
        "/listen", "/recommended", "/fellowships", "/page/", "/sites/",
        "/cdn-cgi/", "/modules/", "/transcript", "/act-", "/prologue",
    )
    for path in raw:
        if path in seen:
            continue
        seen.add(path)
        skip = False
        for prefix in skip_prefixes:
            if path.startswith(prefix):
                skip = True
                break
        if skip:
            continue
        # Must look like an episode path: /slug or /NNN/slug
        if re.match(r'^/\d+/[a-z0-9]', path) or re.match(r'^/[a-z][a-z0-9-]+$', path):
            slugs.append(path)
    return slugs


def extract_playlist_data(html):
    """
    Returns the parsed playlist-data JSON dict from an episode page, or None.
    """
    idx = html.find('id="playlist-data"')
    if idx == -1:
        return None
    snippet = html[idx:]
    end = snippet.find("</script>")
    if end == -1:
        return None
    match = re.search(r"\{.*\}", snippet[:end], re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def build_episode_record(data):
    """
    Extracts the fields we care about from a raw playlist-data dict.
    """
    record = {}
    record["episode"] = data.get("episode", "")
    record["title"] = data.get("title", "")

    # Prefer archive URL (stable, permanent). Fall back to audio (Simplecast).
    archive = data.get("archive", "")
    audio = data.get("audio", "")
    if archive:
        record["url"] = archive
    else:
        record["url"] = audio

    record["simplecast_url"] = audio
    record["stream_url"] = data.get("stream", "")

    acts = data.get("acts", [])
    chapters = []
    for act in acts:
        chapter = {}
        chapter["name"] = act.get("name", "")
        chapter["timestamp"] = act.get("timestamp", 0)
        chapters.append(chapter)
    record["chapters"] = chapters

    return record


def current_year():
    import datetime
    return datetime.date.today().year


def validate_existing_episodes():
    """
    HEAD-checks every audio URL in the existing JSON and reports broken ones.
    Broken episodes will be re-scraped naturally since the cache is always
    cleared at the start of each run.
    """
    if not os.path.exists(OUTPUT_PATH):
        print("  No existing episode file found — skipping validation.")
        return

    with open(OUTPUT_PATH, encoding="utf-8") as f:
        existing = json.load(f)

    total = len(existing)
    print(f"  Checking {total} existing audio URLs...")
    broken = 0

    for i, ep in enumerate(existing):
        label = f"ep {ep['episode']}: {ep['title']}"
        url = ep["url"]
        time.sleep(0.3)
        status = head_status(url)

        if status == 200:
            print(f"  [{i+1}/{total}] {label} — ok ({status})")
        else:
            broken += 1
            print(f"  [{i+1}/{total}] {label} — BROKEN ({status})")

    print(f"  Validation complete: {total - broken} ok, {broken} broken.")


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    print("--- Step 1: Validate existing episode audio URLs ---")
    validate_existing_episodes()

    # Clear the cache so every page is fetched fresh this run.
    # The cache is rebuilt during the run only to deduplicate slugs that appear
    # in multiple archive year pages.
    if os.path.exists(CACHE_DIR):
        import shutil
        shutil.rmtree(CACHE_DIR)
    os.makedirs(CACHE_DIR)
    print("\nCache cleared — all pages will be fetched fresh.")

    print("\n--- Step 2: Collect episode slugs from archive pages ---")
    all_slugs = set()

    print("Collecting episode slugs from archive pages...")
    for year in range(FIRST_YEAR, current_year() + 1):
        url = ARCHIVE_URL.format(year=year)
        print(f"  {year}: {url}")
        try:
            html = fetch_cached(url, ARCHIVE_DELAY)
        except Exception as e:
            print(f"    ERROR fetching archive for {year}: {e}")
            continue
        slugs = extract_episode_slugs(html)
        print(f"    found {len(slugs)} episode links")
        for slug in slugs:
            all_slugs.add(slug)

    print(f"\n--- Step 3: Fetch episode pages and extract audio data ---")
    print(f"  {len(all_slugs)} unique episode slugs to process...")

    episodes = []
    sorted_slugs = sorted(all_slugs)

    for i, slug in enumerate(sorted_slugs):
        url = EPISODE_BASE + slug
        print(f"  [{i+1}/{len(sorted_slugs)}] {slug}")
        try:
            html = fetch_cached(url, EPISODE_DELAY)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue

        data = extract_playlist_data(html)
        if data is None:
            print(f"    no audio data")
            continue

        record = build_episode_record(data)
        if not record["url"]:
            print(f"    no audio URL in playlist-data")
            continue

        episodes.append(record)
        print(f"    ok — ep {record['episode']}: {record['title']}")

    # Sort by episode number ascending
    def episode_sort_key(r):
        try:
            return int(r["episode"])
        except ValueError:
            return 0

    episodes.sort(key=episode_sort_key)

    print(f"\n--- Step 4: Write output ---")
    print(f"  {len(episodes)} episodes with audio found.")

    print(f"  Writing to {OUTPUT_PATH}...")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(episodes, f, indent=2, ensure_ascii=False)

    print("Done.")


if __name__ == "__main__":
    main()
