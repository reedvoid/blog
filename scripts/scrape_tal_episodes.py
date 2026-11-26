"""
Shared helpers for fetching and parsing thisamericanlife.org episode pages,
used by update_tal_episodes.py.

Running this file re-fetches every episode, the same as:

    .venv/bin/python scripts/update_tal_episodes.py --refresh-all

(It used to walk /archive?year=YYYY pages, which omit episodes; see
update_tal_episodes.py.)
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

OUTPUT_PATH = os.path.join("content", "assets", "tal-episodes.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
# Seconds to wait between episode page requests.
EPISODE_DELAY = 1.5
# On 403, 429, or 5xx, retry this many times with exponential backoff starting at this many seconds.
RETRY_COUNT = 4
RETRY_BASE_DELAY = 30.0
RETRY_STATUSES = (403, 429, 500, 502, 503, 504)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_with_retry(url):
    delay = RETRY_BASE_DELAY
    for attempt in range(RETRY_COUNT + 1):
        try:
            return fetch(url)
        except urllib.error.HTTPError as e:
            if e.code in RETRY_STATUSES and attempt < RETRY_COUNT:
                print(f"    {e.code} — waiting {delay:.0f}s before retry {attempt + 1}/{RETRY_COUNT}...")
                time.sleep(delay)
                delay = delay * 2
            else:
                raise


def extract_episode_slugs(html):
    """
    Returns a list of episode URL paths like '/393/infidelity' or '/blackjack'
    from a listing page (the /archive landing or the homepage).
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


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from update_tal_episodes import main as update_main
    return update_main(["--refresh-all"] + sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
