"""
Incremental TAL episode index update.

For a normal site update, run this instead of scrape_tal_episodes.py:

    .venv/bin/python scripts/update_tal_episodes.py

It does two things:

a) Validates every existing audio URL (parallel HEAD requests). The audio
   files live on static paths that don't enforce the same per-IP rate limit
   as the dynamic archive pages, so this can run with high concurrency.

b) Discovers new episodes by fetching just two pages — the /archive landing
   page (newest-first list) and the / homepage. The /archive?year=YYYY
   endpoint is unreliable for the most recent episodes (during 2026-06
   testing it was missing eps 887 and 888 even though they exist), so we
   rely on the two newest-first surfaces instead. Only slugs whose episode
   number exceeds the current max in the JSON are considered new, and only
   those episode pages get fetched.

This trades completeness for speed: it will NOT pick up URL changes on
existing episodes (use scrape_tal_episodes.py for that). It will surface
broken existing URLs but does not auto-repair them.

Typical wall time: ~30s vs ~30 minutes for the full rescrape.
"""

import json
import os
import re
import sys
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_tal_episodes import (
    EPISODE_BASE,
    OUTPUT_PATH,
    EPISODE_DELAY,
    fetch_with_retry,
    head_status,
    extract_episode_slugs,
    extract_playlist_data,
    build_episode_record,
)

ARCHIVE_LANDING_URL = "https://www.thisamericanlife.org/archive"
HOMEPAGE_URL = "https://www.thisamericanlife.org/"

VALIDATION_WORKERS = 10
# Per-URL HEAD retries when a worker hits a transient rate-limit response.
HEAD_RETRY_ATTEMPTS = 3
HEAD_RETRY_BASE_DELAY = 10.0
HEAD_RETRY_STATUSES = (429, 503)
# Pause between the two discovery-landing fetches.
DISCOVERY_DELAY = 2.0


def head_status_with_retry(url):
    delay = HEAD_RETRY_BASE_DELAY
    for attempt in range(HEAD_RETRY_ATTEMPTS):
        status = head_status(url)
        if status in HEAD_RETRY_STATUSES and attempt < HEAD_RETRY_ATTEMPTS - 1:
            time.sleep(delay)
            delay = delay * 2
            continue
        return status
    return status


def validate_parallel(episodes):
    total = len(episodes)
    print(f"Validating {total} audio URLs with {VALIDATION_WORKERS} workers...")
    broken = []
    completed = 0

    def check(ep):
        return ep, head_status_with_retry(ep["url"])

    with ThreadPoolExecutor(max_workers=VALIDATION_WORKERS) as executor:
        futures = [executor.submit(check, ep) for ep in episodes]
        for future in as_completed(futures):
            ep, status = future.result()
            completed += 1
            if status != 200:
                broken.append((ep, status))
                print(f"  BROKEN ep {ep['episode']}: {ep['title']} — {status}")

    print(f"Validation: {total - len(broken)} ok, {len(broken)} broken.")
    return broken


def discover_new_slugs(known_max):
    print(f"Discovering new episodes (known max: ep {known_max})...")
    all_slugs = set()
    for i, url in enumerate([ARCHIVE_LANDING_URL, HOMEPAGE_URL]):
        if i > 0:
            time.sleep(DISCOVERY_DELAY)
        print(f"  fetching {url}")
        try:
            html = fetch_with_retry(url)
        except urllib.error.HTTPError as e:
            print(f"    ERROR: HTTP {e.code} — skipping")
            continue
        except Exception as e:
            print(f"    ERROR: {e} — skipping")
            continue
        slugs = extract_episode_slugs(html)
        print(f"    {len(slugs)} numbered slugs found")
        all_slugs.update(slugs)

    new_slugs = []
    for slug in all_slugs:
        match = re.match(r"^/(\d+)/", slug)
        if match and int(match.group(1)) > known_max:
            new_slugs.append(slug)

    new_slugs.sort()
    print(f"  {len(new_slugs)} slug(s) newer than ep {known_max}: {new_slugs}")
    return new_slugs


def fetch_new_episodes(slugs):
    records = []
    for i, slug in enumerate(slugs):
        url = EPISODE_BASE + slug
        print(f"  [{i + 1}/{len(slugs)}] {slug}")
        if i > 0:
            time.sleep(EPISODE_DELAY)
        try:
            html = fetch_with_retry(url)
        except Exception as e:
            print(f"    ERROR: {e}")
            continue
        data = extract_playlist_data(html)
        if data is None:
            print(f"    no audio data")
            continue
        record = build_episode_record(data)
        if not record["url"]:
            print(f"    no audio URL")
            continue
        records.append(record)
        print(f"    ok — ep {record['episode']}: {record['title']}")
    return records


def episode_sort_key(record):
    try:
        return int(record["episode"])
    except ValueError:
        return 0


def main():
    if not os.path.exists(OUTPUT_PATH):
        print(f"ERROR: {OUTPUT_PATH} not found. Run scrape_tal_episodes.py for the initial build.")
        sys.exit(1)

    with open(OUTPUT_PATH, encoding="utf-8") as f:
        episodes = json.load(f)
    print(f"Loaded {len(episodes)} existing episodes.\n")

    print("--- Step 1: Validate existing audio URLs (parallel) ---")
    t0 = time.time()
    broken = validate_parallel(episodes)
    print(f"  ({time.time() - t0:.1f}s)")

    print("\n--- Step 2: Discover new episodes ---")
    numbered = []
    for ep in episodes:
        if ep["episode"].isdigit():
            numbered.append(int(ep["episode"]))
    known_max = max(numbered) if numbered else 0
    t0 = time.time()
    new_slugs = discover_new_slugs(known_max)
    print(f"  ({time.time() - t0:.1f}s)")

    print("\n--- Step 3: Fetch new episode pages ---")
    t0 = time.time()
    new_records = fetch_new_episodes(new_slugs)
    print(f"  ({time.time() - t0:.1f}s)")

    if new_records:
        by_number = {}
        for ep in episodes:
            by_number[ep["episode"]] = ep
        for record in new_records:
            by_number[record["episode"]] = record
        merged = list(by_number.values())
        merged.sort(key=episode_sort_key)

        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        print(f"\nWrote {len(merged)} episodes to {OUTPUT_PATH} ({len(new_records)} new).")
    else:
        print("\nNo new episodes found — JSON unchanged.")

    if broken:
        print(f"\nBroken audio URLs ({len(broken)}):")
        for ep, status in broken:
            print(f"  ep {ep['episode']} ({status}): {ep['title']}")
            print(f"    {ep['url']}")
        print("\nThese existed before this run. To attempt a refresh, run scrape_tal_episodes.py.")


if __name__ == "__main__":
    main()
