"""
Tests for update_tal_episodes.py against a fake thisamericanlife.org.

Run: .venv/bin/python -m unittest discover -s scripts
"""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

import update_tal_episodes as upd

EXCLUDED_REAL = dict(upd.EXCLUDED_EPISODES)


FULL_EPISODE_BYTES = 30_000_000
PROMO_BYTES = 1_469_036  # ep 888's promo clip, 2026-11


def audio_url(number, token="new"):
    return f"https://tal.test/audio/{token}/{number}.mp3"


def page_html(data):
    return f'<html><script id="playlist-data" type="application/json">{json.dumps(data)}</script></html>'


def listing_html(numbers):
    links = []
    for n in numbers:
        links.append(f'<a href="/{n}/slug-{n}">ep</a>')
    return "<html>" + "".join(links) + "</html>"


def record(number, token="old"):
    return {
        "episode": str(number),
        "title": f"{number}: Title",
        "url": audio_url(number, token),
        "simplecast_url": audio_url(number, token),
        "stream_url": f"https://stream.test/{number}.m3u8",
        "chapters": [],
    }


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "err", {}, None)


class FakeTAL:
    """
    pages: number -> playlist-data dict, raw page html (str), or an int HTTP
    status to fail with.
    Numbers absent from pages 404. live_audio: URLs whose HEAD returns 200
    with a full-episode Content-Length. sizes: URL -> Content-Length (or
    None for no header) overriding that, for promo clips and stubs.
    """

    def __init__(self, listed, pages, live_audio, failing_listings=None, sizes=None):
        self.listed = listed
        self.pages = pages
        self.live_audio = set(live_audio)
        self.sizes = sizes or {}
        # listing URL -> HTTP status that page fails with
        self.failing_listings = failing_listings or {}
        self.fetched = []

    def fetch(self, url):
        self.fetched.append(url)
        if url in (upd.ARCHIVE_LANDING_URL, upd.HOMEPAGE_URL):
            if url in self.failing_listings:
                raise http_error(url, self.failing_listings[url])
            return listing_html(self.listed)
        prefix = upd.EPISODE_BY_NUMBER_URL.format(number="")
        if url.startswith(prefix):
            number = int(url[len(prefix):])
            page = self.pages.get(number)
            if page is None:
                raise http_error(url, 404)
            if isinstance(page, int):
                raise http_error(url, page)
            if isinstance(page, str):
                return page
            return page_html(page)
        raise AssertionError(f"unexpected fetch {url}")

    def head(self, url):
        if url in self.sizes:
            return 200, self.sizes[url]
        if url in self.live_audio:
            return 200, FULL_EPISODE_BYTES
        return 404, None

    def episode_fetches(self):
        prefix = upd.EPISODE_BY_NUMBER_URL.format(number="")
        numbers = []
        for url in self.fetched:
            if url.startswith(prefix):
                numbers.append(int(url[len(prefix):]))
        return numbers


def playlist(number, token="new", archive=True):
    data = {"episode": str(number), "title": f"{number}: Title", "stream": f"https://stream.test/{number}.m3u8",
            "audio": audio_url(number, "simplecast"), "acts": [{"name": "Prologue", "timestamp": 0}]}
    if archive:
        data["archive"] = audio_url(number, token)
    return data


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "tal-episodes.json")
        for target, value in [("OUTPUT_PATH", self.path), ("time.sleep", lambda s: None),
                              ("EXCLUDED_EPISODES", {})]:
            patcher = mock.patch(f"update_tal_episodes.{target}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def write_index(self, records):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(records, f)
        with open(self.path, "rb") as f:
            return f.read()

    def read_index(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def run_update(self, site, argv=()):
        out = io.StringIO()
        with mock.patch("update_tal_episodes.fetch_with_retry", site.fetch), \
                mock.patch("update_tal_episodes.head_audio", site.head), \
                redirect_stdout(out):
            code = upd.main(list(argv))
        return code, out.getvalue()

    def index_bytes(self):
        with open(self.path, "rb") as f:
            return f.read()

    # --- happy paths ---

    def test_nothing_new_leaves_file_byte_identical(self):
        before = self.write_index([record(1), record(2)])
        site = FakeTAL(listed=[2, 1], pages={}, live_audio=[audio_url(1, "old"), audio_url(2, "old")])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.index_bytes(), before)
        # Only the probe of ep 3 hits an episode page.
        self.assertEqual(site.episode_fetches(), [3])

    def test_repairs_broken_url_from_current_page(self):
        self.write_index([record(1), record(2)])
        site = FakeTAL(listed=[2], pages={2: playlist(2, "moved")},
                       live_audio=[audio_url(1, "old"), audio_url(2, "moved")])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        index = self.read_index()
        self.assertEqual(index[1]["url"], audio_url(2, "moved"))
        self.assertEqual(index[0]["url"], audio_url(1, "old"))

    def test_prefers_archive_over_audio_field(self):
        # The 489 case: `audio` is a temporary "extended" URL, `archive` is stable.
        self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={1: playlist(1, "stable")},
                       live_audio=[audio_url(1, "stable"), audio_url(1, "simplecast")])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_index()[0]["url"], audio_url(1, "stable"))

    def test_fetches_episodes_the_listing_omits(self):
        # The listing jumps 1 -> 4; 2 and 3 must still be fetched by number.
        self.write_index([record(1)])
        pages = {2: playlist(2), 3: playlist(3), 4: playlist(4)}
        live = [audio_url(1, "old"), audio_url(2), audio_url(3), audio_url(4)]
        site = FakeTAL(listed=[4, 1], pages=pages, live_audio=live)
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["episode"] for r in self.read_index()], ["1", "2", "3", "4"])

    def test_probes_past_a_lagging_listing(self):
        # The listing still shows only ep 1, but 2 and 3 are released.
        self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={2: playlist(2), 3: playlist(3)},
                       live_audio=[audio_url(1, "old"), audio_url(2), audio_url(3)])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["episode"] for r in self.read_index()], ["1", "2", "3"])
        self.assertEqual(site.episode_fetches(), [2, 3, 4])

    def test_fills_hole_below_known_max(self):
        self.write_index([record(1), record(3)])
        site = FakeTAL(listed=[3], pages={2: playlist(2)},
                       live_audio=[audio_url(1, "old"), audio_url(3, "old"), audio_url(2)])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["episode"] for r in self.read_index()], ["1", "2", "3"])

    def test_output_sorted_numerically(self):
        self.write_index([record(1)])
        pages = {}
        live = [audio_url(1, "old")]
        for n in range(2, 12):
            pages[n] = playlist(n)
            live.append(audio_url(n))
        site = FakeTAL(listed=[11], pages=pages, live_audio=live)
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["episode"] for r in self.read_index()], [str(n) for n in range(1, 12)])

    # --- failures: JSON untouched, exit 1 ---

    def assert_failed_untouched(self, before, code, out):
        self.assertEqual(code, 1, out)
        self.assertIn("NOT written", out)
        self.assertEqual(self.index_bytes(), before)

    def test_repair_fails_when_page_url_also_dead(self):
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={1: playlist(1, "dead")}, live_audio=[])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("ep 1: repair failed: no full-episode audio — HTTP 404", out)

    def test_repair_fails_when_page_gone(self):
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={}, live_audio=[])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)

    def test_page_for_wrong_episode_fails(self):
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[2], pages={2: playlist(7)}, live_audio=[audio_url(1, "old"), audio_url(7)])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("is episode '7', expected 2", out)

    def test_page_without_playlist_data_fails(self):
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[2], pages={2: "<html>no data</html>"}, live_audio=[audio_url(1, "old")])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("ep 2: fetch failed: no playlist-data", out)

    def test_page_without_audio_url_fails(self):
        before = self.write_index([record(1)])
        data = playlist(2, archive=False)
        del data["audio"]
        site = FakeTAL(listed=[2], pages={2: data}, live_audio=[audio_url(1, "old")])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("ep 2: fetch failed: no audio URL", out)

    def test_falls_back_to_audio_field_without_archive(self):
        self.write_index([record(1)])
        site = FakeTAL(listed=[2], pages={2: playlist(2, archive=False)},
                       live_audio=[audio_url(1, "old"), audio_url(2, "simplecast")])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_index()[1]["url"], audio_url(2, "simplecast"))

    def test_listed_episode_that_404s_fails_as_gap(self):
        # Listing says ep 3 exists but /episode/2 404s: a hole, not "done".
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[3], pages={3: playlist(3)}, live_audio=[audio_url(1, "old"), audio_url(3)])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("index lacks episodes [2]", out)

    def test_both_listing_pages_failing_is_a_failure(self):
        before = self.write_index([record(1)])
        failing = {upd.ARCHIVE_LANDING_URL: 503, upd.HOMEPAGE_URL: 503}
        site = FakeTAL(listed=[], pages={}, live_audio=[audio_url(1, "old")], failing_listings=failing)
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("discovery failed: no numbered episode links", out)

    def test_one_listing_page_failing_is_a_failure(self):
        # The other page still works and nothing is missing, but a partial
        # discovery must not pass as a clean run.
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={}, live_audio=[audio_url(1, "old")],
                       failing_listings={upd.HOMEPAGE_URL: 503})
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("discovery failed: could not read https://www.thisamericanlife.org/", out)

    def test_listing_with_only_older_numbers_does_not_lower_newest(self):
        # The listing shows reruns of old episodes only; known max stays 3.
        before = self.write_index([record(1), record(2), record(3)])
        live = [audio_url(1, "old"), audio_url(2, "old"), audio_url(3, "old")]
        site = FakeTAL(listed=[1], pages={}, live_audio=live)
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.index_bytes(), before)
        self.assertEqual(site.episode_fetches(), [4])

    def test_empty_index_fetches_every_episode_by_number(self):
        self.write_index([])
        site = FakeTAL(listed=[2], pages={1: playlist(1), 2: playlist(2)},
                       live_audio=[audio_url(1), audio_url(2)])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["episode"] for r in self.read_index()], ["1", "2"])

    def test_probe_error_other_than_404_is_a_failure(self):
        # A 500 on the probe must not be read as "no newer episode".
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={2: 500}, live_audio=[audio_url(1, "old")])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("ep 2: probe failed: HTTP 500", out)

    def test_one_failure_blocks_otherwise_good_updates(self):
        before = self.write_index([record(1), record(2)])
        site = FakeTAL(listed=[3], pages={2: playlist(2, "dead"), 3: playlist(3)},
                       live_audio=[audio_url(1, "old"), audio_url(3)])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)

    # --- full-episode size check ---

    def test_promo_sized_url_is_repaired(self):
        # The 888 case: the stored promo returns 200 but isn't the episode.
        self.write_index([record(1, "promo")])
        site = FakeTAL(listed=[1], pages={1: playlist(1, "full")}, live_audio=[audio_url(1, "full")],
                       sizes={audio_url(1, "promo"): PROMO_BYTES})
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_index()[0]["url"], audio_url(1, "full"))

    def test_size_boundary(self):
        at_min = audio_url(1, "atmin")
        below = audio_url(2, "below")
        self.write_index([record(1, "atmin"), record(2, "below")])
        site = FakeTAL(listed=[2], pages={}, live_audio=[],
                       sizes={at_min: upd.MIN_AUDIO_BYTES, below: upd.MIN_AUDIO_BYTES - 1})
        code, out = self.run_update(site)
        self.assertEqual(code, 1, out)
        self.assertNotIn("BROKEN ep 1", out)
        self.assertIn(f"BROKEN ep 2: 2: Title — only {upd.MIN_AUDIO_BYTES - 1} bytes", out)

    def test_missing_content_length_is_broken(self):
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[1], pages={1: playlist(1, "old")}, live_audio=[],
                       sizes={audio_url(1, "old"): None, audio_url(1, "simplecast"): None})
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("no Content-Length", out)

    def test_falls_back_to_audio_field_when_archive_is_a_stub(self):
        self.write_index([record(1)])
        site = FakeTAL(listed=[2], pages={2: playlist(2, "stub")},
                       live_audio=[audio_url(1, "old"), audio_url(2, "simplecast")],
                       sizes={audio_url(2, "stub"): 242})
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.read_index()[1]["url"], audio_url(2, "simplecast"))

    def test_page_with_only_promo_audio_fails(self):
        # The 454 case without an exclusion: archive is a stub, audio a promo.
        before = self.write_index([record(1)])
        site = FakeTAL(listed=[2], pages={2: playlist(2, "stub")}, live_audio=[audio_url(1, "old")],
                       sizes={audio_url(2, "stub"): 242, audio_url(2, "simplecast"): PROMO_BYTES})
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("ep 2: fetch failed: no full-episode audio", out)
        self.assertIn("only 242 bytes", out)
        self.assertIn(f"only {PROMO_BYTES} bytes", out)

    def test_rate_limited_head_is_retried(self):
        responses = [(429, None), (200, FULL_EPISODE_BYTES)]
        with mock.patch("update_tal_episodes.head_audio", lambda url: responses.pop(0)):
            self.assertIsNone(upd.audio_problem("https://tal.test/x.mp3"))
        self.assertEqual(responses, [])

    def test_persistent_rate_limit_is_broken(self):
        calls = []

        def always_429(url):
            calls.append(url)
            return 429, None

        with mock.patch("update_tal_episodes.head_audio", always_429):
            self.assertEqual(upd.audio_problem("https://tal.test/x.mp3"), "HTTP 429")
        self.assertEqual(len(calls), upd.HEAD_RETRY_ATTEMPTS)

    # --- EXCLUDED_EPISODES ---

    def exclude(self, numbers):
        excluded = {}
        for n in numbers:
            excluded[n] = "test exclusion"
        patcher = mock.patch("update_tal_episodes.EXCLUDED_EPISODES", excluded)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_excluded_episode_is_dropped_and_not_checked(self):
        self.exclude([2])
        self.write_index([record(1), record(2, "promo"), record(3)])
        site = FakeTAL(listed=[3], pages={}, live_audio=[audio_url(1, "old"), audio_url(3, "old")],
                       sizes={audio_url(2, "promo"): PROMO_BYTES})
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual([r["episode"] for r in self.read_index()], ["1", "3"])
        self.assertNotIn("BROKEN", out)
        self.assertNotIn(2, site.episode_fetches())

    def test_excluded_gap_is_not_fetched_or_flagged(self):
        self.exclude([2])
        before = self.write_index([record(1), record(3)])
        site = FakeTAL(listed=[3], pages={}, live_audio=[audio_url(1, "old"), audio_url(3, "old")])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.index_bytes(), before)
        self.assertEqual(site.episode_fetches(), [4])

    def test_exclusion_does_not_hide_another_gap(self):
        self.exclude([2])
        before = self.write_index([record(1), record(4)])
        site = FakeTAL(listed=[4], pages={}, live_audio=[audio_url(1, "old"), audio_url(4, "old")])
        code, out = self.run_update(site)
        self.assert_failed_untouched(before, code, out)
        self.assertIn("index lacks episodes [3]", out)

    def test_excluding_the_highest_episode_keeps_newest(self):
        # Dropping the top record must not make the script think newest is
        # lower and re-probe from there.
        self.exclude([3])
        self.write_index([record(1), record(2), record(3)])
        site = FakeTAL(listed=[2], pages={}, live_audio=[audio_url(1, "old"), audio_url(2, "old")])
        code, out = self.run_update(site)
        self.assertEqual(code, 0, out)
        self.assertEqual(site.episode_fetches(), [4])

    def test_real_exclusion_list_names_454_with_reason(self):
        self.assertIn(454, EXCLUDED_REAL)
        self.assertIn("retracted", EXCLUDED_REAL[454])

    # --- --allow-failures ---

    def test_allow_failures_writes_successes_and_still_exits_1(self):
        self.write_index([record(1), record(2)])
        site = FakeTAL(listed=[3], pages={2: playlist(2, "dead"), 3: playlist(3)},
                       live_audio=[audio_url(1, "old"), audio_url(3)])
        code, out = self.run_update(site, ["--allow-failures"])
        self.assertEqual(code, 1, out)
        index = self.read_index()
        self.assertEqual([r["episode"] for r in index], ["1", "2", "3"])
        # The unrepaired record is kept as it was.
        self.assertEqual(index[1]["url"], audio_url(2, "old"))


if __name__ == "__main__":
    unittest.main()
