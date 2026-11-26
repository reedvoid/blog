"""
Incremental TAL episode index update.

For a normal site update:

    .venv/bin/python scripts/update_tal_episodes.py

To re-fetch every episode (refresh titles and chapters, or build the index
from scratch; ~25 minutes at 1.5s per page):

    .venv/bin/python scripts/update_tal_episodes.py --refresh-all

Every episode is fetched by number through /episode/N, which redirects to the
episode's current page (including renamed pages such as /220/testosterone ->
/220/testosterone-2002) and returns 404 for a number that doesn't exist yet.
No listing page is trusted to be complete: in 2026 testing the 2025 year page
omitted 862, the 2026 year page omitted 881-885 and 900-902, and the /archive
landing only shows the ~50 most recently aired episodes (reruns included).

Steps:

1. Validate every existing audio URL (parallel HEAD requests). The audio
   files live on static paths without the per-IP rate limit of the pages.
   A URL counts as broken unless it returns 200 with a Content-Length of at
   least MIN_AUDIO_BYTES: a pre-release page serves a short promo clip that
   returns 200 (ep 888 was indexed in 2026-06 with a 1.4 MB promo).
2. Repair each broken URL by re-fetching /episode/N and taking its current
   audio URL. TAL moves files over time (e.g. to /audio/upload/archive/N.mp3,
   or to a new token under /audio/N/).
3. Find the newest episode: the highest number on the /archive landing and
   the / homepage, then probe /episode/N+1 upward until a 404.
4. Fetch every episode number from 1 to the newest that the index lacks
   (with --refresh-all: every episode number, skipping steps 1 and 2).
5. Check that the index holds exactly episodes 1..newest minus
   EXCLUDED_EPISODES, each with an audio URL that passed the check above
   during this run.

EXCLUDED_EPISODES lists episodes TAL no longer serves full audio for, each
with its reason. They are dropped from the index and never fetched.

Any failure (a fetch error, a page without full audio, a gap) fails the
whole run: the JSON is left untouched and the
exit status is 1. Pass --allow-failures to write the episodes that did
succeed anyway (e.g. when TAL has genuinely pulled an episode). The JSON is
written to a temp file and renamed into place, so a crash can't truncate it.

Typical wall time: ~30s plus ~1.5s per episode page fetched.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scrape_tal_episodes import (
    OUTPUT_PATH,
    EPISODE_DELAY,
    USER_AGENT,
    fetch_with_retry,
    extract_episode_slugs,
    extract_playlist_data,
    build_episode_record,
)

ARCHIVE_LANDING_URL = "https://www.thisamericanlife.org/archive"
HOMEPAGE_URL = "https://www.thisamericanlife.org/"
EPISODE_BY_NUMBER_URL = "https://www.thisamericanlife.org/episode/{number}"

VALIDATION_WORKERS = 10
# Per-URL HEAD retries when a worker hits a transient rate-limit response.
HEAD_RETRY_ATTEMPTS = 3
HEAD_RETRY_BASE_DELAY = 10.0
HEAD_RETRY_STATUSES = (429, 503)
# Pause between the two discovery-landing fetches.
DISCOVERY_DELAY = 2.0
# Smallest audio file accepted as a full episode. Measured 2026-11: the
# smallest of 900 full episodes is over 10 MB; promo clips are 240 KB-1.4 MB
# and a removed episode's archive URL serves a 242-byte stub.
MIN_AUDIO_BYTES = 5_000_000

EXCLUDED_EPISODES = {
    454: "retracted by TAL in 2012 (Mike Daisey's China story was fabricated); "
         "TAL removed the audio and the page serves only its pre-air preview clip",
}


class EpisodeNotFound(Exception):
    """/episode/N returned 404: no such episode (yet)."""


def head_audio(url):
    """
    (status, content_length) for a HEAD request. status is None on a network
    error; content_length is None when the header is absent.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            length = resp.headers.get("Content-Length")
            if length is None:
                return resp.status, None
            return resp.status, int(length)
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return None, None


def audio_problem(url):
    """None if url serves a full episode, else the reason it doesn't."""
    delay = HEAD_RETRY_BASE_DELAY
    for attempt in range(HEAD_RETRY_ATTEMPTS):
        status, length = head_audio(url)
        if status in HEAD_RETRY_STATUSES and attempt < HEAD_RETRY_ATTEMPTS - 1:
            time.sleep(delay)
            delay = delay * 2
            continue
        break
    if status is None:
        return "network error"
    if status != 200:
        return f"HTTP {status}"
    if length is None:
        return "no Content-Length"
    if length < MIN_AUDIO_BYTES:
        return f"only {length} bytes (promo or stub, not a full episode)"
    return None


def validate_parallel(episodes):
    total = len(episodes)
    print(f"Validating {total} audio URLs with {VALIDATION_WORKERS} workers...")
    broken = []

    def check(ep):
        return ep, audio_problem(ep["url"])

    with ThreadPoolExecutor(max_workers=VALIDATION_WORKERS) as executor:
        futures = [executor.submit(check, ep) for ep in episodes]
        for future in as_completed(futures):
            ep, problem = future.result()
            if problem is not None:
                broken.append((ep, problem))
                print(f"  BROKEN ep {ep['episode']}: {ep['title']} — {problem}")

    broken.sort(key=lambda item: episode_number(item[0]))
    print(f"Validation: {total - len(broken)} ok, {len(broken)} broken.")
    return broken


def episode_number(record):
    return int(record["episode"])


def fetch_episode(number):
    """
    Fetches /episode/N and returns its record, with an audio URL that just
    passed audio_problem. The page's `archive` URL is tried first, then its
    `audio` URL. Raises EpisodeNotFound on a 404 page; raises RuntimeError
    with a reason for every other failure.
    """
    url = EPISODE_BY_NUMBER_URL.format(number=number)
    try:
        html = fetch_with_retry(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise EpisodeNotFound(url)
        raise RuntimeError(f"HTTP {e.code} fetching {url}")
    except Exception as e:
        raise RuntimeError(f"error fetching {url}: {e}")

    data = extract_playlist_data(html)
    if data is None:
        raise RuntimeError(f"no playlist-data on {url}")
    record = build_episode_record(data)
    if record["episode"] != str(number):
        raise RuntimeError(f"{url} is episode {record['episode']!r}, expected {number}")
    if not record["url"]:
        raise RuntimeError(f"no audio URL on {url}")
    candidates = [record["url"]]
    if record["simplecast_url"] and record["simplecast_url"] != record["url"]:
        candidates.append(record["simplecast_url"])
    problems = []
    for candidate in candidates:
        problem = audio_problem(candidate)
        if problem is None:
            record["url"] = candidate
            return record
        problems.append(f"{problem}: {candidate}")
    raise RuntimeError("no full-episode audio — " + "; ".join(problems))


def newest_listed_number():
    """
    Highest episode number linked from the /archive landing and the homepage
    (both newest-first). Returns (number or None, errors): each page that
    can't be read is an error, since "no new episodes" must never be the
    result of a failed fetch.
    """
    numbers = []
    errors = []
    for i, url in enumerate([ARCHIVE_LANDING_URL, HOMEPAGE_URL]):
        if i > 0:
            time.sleep(DISCOVERY_DELAY)
        print(f"  fetching {url}")
        try:
            html = fetch_with_retry(url)
        except Exception as e:
            errors.append(f"could not read {url}: {e}")
            print(f"    FAILED: {e}")
            continue
        for slug in extract_episode_slugs(html):
            match = re.match(r"^/(\d+)/", slug)
            if match:
                numbers.append(int(match.group(1)))
    if not numbers:
        errors.append("no numbered episode links on the /archive landing or the homepage")
        return None, errors
    return max(numbers), errors


def write_index(records):
    """
    Writes to a temp file beside OUTPUT_PATH, then renames it over the
    original, so a crash mid-write leaves the old index intact.
    """
    tmp_path = OUTPUT_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, OUTPUT_PATH)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Incremental TAL episode index update.")
    parser.add_argument(
        "--allow-failures",
        action="store_true",
        help="write the episodes that succeeded even if some failed",
    )
    parser.add_argument(
        "--refresh-all",
        action="store_true",
        help="re-fetch every episode (titles, chapters, URLs); also builds the index from scratch",
    )
    args = parser.parse_args(argv)

    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            episodes = json.load(f)
    elif args.refresh_all:
        episodes = []
    else:
        print(f"ERROR: {OUTPUT_PATH} not found. Run with --refresh-all to build it.")
        return 1
    print(f"Loaded {len(episodes)} existing episodes.\n")

    by_number = {}
    for ep in episodes:
        by_number[episode_number(ep)] = ep
    known_max = max(by_number) if by_number else 0
    removed = []
    for number in sorted(EXCLUDED_EPISODES):
        if number in by_number:
            del by_number[number]
            removed.append(number)
            print(f"Dropping excluded ep {number}: {EXCLUDED_EPISODES[number]}")
    # Failures as (episode number or None, reason).
    failures = []
    repaired = []
    added = []
    refreshed = []
    fetch_count = 0

    def fetch_paced(number):
        nonlocal fetch_count
        if fetch_count > 0:
            time.sleep(EPISODE_DELAY)
        fetch_count += 1
        return fetch_episode(number)

    print("--- Step 1: Validate existing audio URLs (parallel) ---")
    if args.refresh_all:
        # Every episode is re-fetched and its audio checked in step 4.
        print("  skipped (--refresh-all)")
        broken = []
    else:
        t0 = time.time()
        broken = validate_parallel(list(by_number.values()))
        print(f"  ({time.time() - t0:.1f}s)")

    print("\n--- Step 2: Repair broken audio URLs ---")
    if not broken:
        print("  nothing to repair")
    for ep, problem in broken:
        number = episode_number(ep)
        print(f"  ep {number} ({problem})")
        try:
            record = fetch_paced(number)
        except (EpisodeNotFound, RuntimeError) as e:
            failures.append((number, f"repair failed: {e}"))
            print(f"    FAILED: {e}")
            continue
        by_number[number] = record
        repaired.append(number)
        print(f"    ok — {ep['url']}\n      -> {record['url']}")

    print("\n--- Step 3: Find the newest episode ---")
    newest = known_max
    listed, discovery_errors = newest_listed_number()
    for error in discovery_errors:
        failures.append((None, f"discovery failed: {error}"))
    if listed is not None:
        print(f"  newest listed: ep {listed}")
        newest = max(newest, listed)
    # Listing pages can lag behind a release; probe upward until a 404.
    while True:
        probe = newest + 1
        print(f"  probing ep {probe}")
        try:
            record = fetch_paced(probe)
        except EpisodeNotFound:
            print(f"    404 — newest is ep {newest}")
            break
        except RuntimeError as e:
            failures.append((probe, f"probe failed: {e}"))
            print(f"    FAILED: {e}")
            break
        by_number[probe] = record
        added.append(probe)
        newest = probe
        print(f"    ok — {record['title']}")

    missing = []
    for number in range(1, newest + 1):
        if number in EXCLUDED_EPISODES:
            continue
        # Probed episodes were fetched moments ago in step 3.
        if args.refresh_all and number not in added:
            missing.append(number)
        elif number not in by_number:
            missing.append(number)
    if args.refresh_all:
        print(f"\n--- Step 4: Re-fetch every episode 1..{newest} ---")
        print(f"  {len(missing)} to fetch")
    else:
        print(f"\n--- Step 4: Fetch episodes missing from 1..{newest} ---")
        print(f"  {len(missing)} missing: {missing}")
    for number in missing:
        print(f"  ep {number}")
        try:
            record = fetch_paced(number)
        except (EpisodeNotFound, RuntimeError) as e:
            failures.append((number, f"fetch failed: {e}"))
            print(f"    FAILED: {e}")
            continue
        if number in by_number:
            refreshed.append(number)
        else:
            added.append(number)
        by_number[number] = record
        print(f"    ok — {record['title']}")

    print("\n--- Step 5: Completeness check ---")
    still_missing = []
    for number in range(1, newest + 1):
        if number not in by_number and number not in EXCLUDED_EPISODES:
            still_missing.append(number)
    if still_missing:
        failures.append((None, f"index lacks episodes {still_missing}"))
    print(f"  index holds {len(by_number)} episodes; expected 1..{newest} "
          f"minus excluded {sorted(EXCLUDED_EPISODES)}; lacking {still_missing}")

    merged = []
    for number in sorted(by_number):
        merged.append(by_number[number])

    print(f"\nRepaired {len(repaired)}: {sorted(repaired)}")
    print(f"Added {len(added)}: {sorted(added)}")
    if args.refresh_all:
        print(f"Re-fetched {len(refreshed)} existing episodes")
    print(f"Removed (excluded) {len(removed)}: {removed}")

    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for number, reason in failures:
            if number is None:
                print(f"  {reason}")
            else:
                print(f"  ep {number}: {reason}")

    if failures and not args.allow_failures:
        print(f"\n{OUTPUT_PATH} NOT written. Recovery options:")
        print("  - rerun (rate limits and network errors are often transient)")
        print("  - open the failing /episode/N pages to see what TAL changed")
        print("  - rerun with --allow-failures to write the episodes that succeeded")
        return 1

    if merged == episodes:
        print(f"\nNothing changed — {OUTPUT_PATH} unchanged.")
    else:
        write_index(merged)
        print(f"\nWrote {len(merged)} episodes to {OUTPUT_PATH}.")

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
