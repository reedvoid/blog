// Tests for the saved-queue restore in generator/templates/tal_player.html.
// Run: node --test scripts/test_tal_player.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const template = readFileSync(new URL('../generator/templates/tal_player.html', import.meta.url), 'utf8');
// Source of a top-level function in the player's script, by name. The
// source is this repo's own template, not outside input.
function extract(name) {
    const m = template.match(new RegExp('\\n    function ' + name + '\\([\\s\\S]*?\\n    }\\n'));
    assert.ok(m, `${name} not found in tal_player.html`);
    return m[0];
}
const refreshSavedQueue = new Function(extract('refreshSavedQueue') + '\nreturn refreshSavedQueue;')();

// loadState with the player's closure variables and a stub localStorage.
function makePlayer(stored, episodeList) {
    const storage = {
        data: { 'tal-queue': stored },
        getItem(k) { return Object.prototype.hasOwnProperty.call(this.data, k) ? this.data[k] : null; },
        setItem(k, v) { this.data[k] = v; },
    };
    const src = extract('writeState') + extract('refreshSavedQueue') + extract('loadState');
    const player = new Function('localStorage', 'episodeList', `
        var STORAGE_KEY = 'tal-queue';
        var queue = [];
        var queueIndex = -1;
        var restoredPosition = 0;
        var episodes = episodeList;
        ${src}
        return { loadState, state: function () { return { queue, queueIndex, restoredPosition }; } };
    `)(storage, episodeList);
    return { player, storage };
}

function ep(number, token = 'new') {
    return {
        episode: String(number),
        title: `${number}: Title`,
        url: `https://tal.test/${token}/${number}.mp3`,
        chapters: [],
    };
}

test('repaired URL replaces the saved copy; position dropped for the new file', () => {
    // The 206 case: saved with a URL that now 404s.
    const fresh = [ep(1), ep(206)];
    const r = refreshSavedQueue([ep(1), ep(206, 'old')], 1, fresh);
    assert.equal(r.queue[1].url, 'https://tal.test/new/206.mp3');
    assert.equal(r.queue[1], fresh[1]);
    assert.equal(r.queueIndex, 1);
    assert.equal(r.currentUnchanged, false);
});

test('unchanged current episode keeps its position', () => {
    const fresh = [ep(1), ep(2)];
    const r = refreshSavedQueue([ep(1), ep(2)], 0, fresh);
    assert.deepEqual(r.queue, fresh);
    assert.equal(r.queueIndex, 0);
    assert.equal(r.currentUnchanged, true);
});

test('fresh title and chapters replace saved ones', () => {
    const saved = ep(220);
    saved.title = '220: Testosterone';
    const current = ep(220);
    current.title = '220: Testosterone (2002)';
    current.chapters = [{ name: 'Prologue', timestamp: 0 }];
    const r = refreshSavedQueue([saved], 0, [current]);
    assert.equal(r.queue[0].title, '220: Testosterone (2002)');
    assert.deepEqual(r.queue[0].chapters, [{ name: 'Prologue', timestamp: 0 }]);
    assert.equal(r.currentUnchanged, true);
});

test('dropped entry before the current one shifts the index down', () => {
    const r = refreshSavedQueue([ep(454), ep(1), ep(2)], 2, [ep(1), ep(2)]);
    assert.deepEqual(r.queue.map(e => e.episode), ['1', '2']);
    assert.equal(r.queueIndex, 1);
    assert.equal(r.currentUnchanged, true);
});

test('dropped current episode moves to the next kept one', () => {
    const r = refreshSavedQueue([ep(1), ep(454), ep(2)], 1, [ep(1), ep(2)]);
    assert.deepEqual(r.queue.map(e => e.episode), ['1', '2']);
    assert.equal(r.queue[r.queueIndex].episode, '2');
    assert.equal(r.currentUnchanged, false);
});

test('dropped current episode at the end moves to the last kept one', () => {
    const r = refreshSavedQueue([ep(1), ep(2), ep(454)], 2, [ep(1), ep(2)]);
    assert.equal(r.queue[r.queueIndex].episode, '2');
    assert.equal(r.currentUnchanged, false);
});

test('nothing surviving returns null', () => {
    assert.equal(refreshSavedQueue([ep(454)], 0, [ep(1)]), null);
});

test('malformed saved entries are dropped', () => {
    const r = refreshSavedQueue([null, {}, { episode: '__proto__' }, ep(1)], 3, [ep(1)]);
    assert.deepEqual(r.queue.map(e => e.episode), ['1']);
    assert.equal(r.queueIndex, 0);
    assert.equal(r.currentUnchanged, true);
});

test('an episode queued twice stays queued twice', () => {
    const r = refreshSavedQueue([ep(1), ep(2), ep(1)], 2, [ep(1), ep(2)]);
    assert.deepEqual(r.queue.map(e => e.episode), ['1', '2', '1']);
    assert.equal(r.queueIndex, 2);
});

test('out-of-range saved index lands on the last kept entry', () => {
    const r = refreshSavedQueue([ep(1), ep(2)], 5, [ep(1), ep(2)]);
    assert.equal(r.queueIndex, 1);
    assert.equal(r.currentUnchanged, false);
});

test('the real episode list round-trips a queue built from it', () => {
    const real = JSON.parse(readFileSync(new URL('../content/assets/tal-episodes.json', import.meta.url), 'utf8'));
    const saved = [real[0], real[205], real[real.length - 1]];
    const r = refreshSavedQueue(JSON.parse(JSON.stringify(saved)), 1, real);
    assert.deepEqual(r.queue, saved);
    assert.equal(r.currentUnchanged, true);
    assert.ok(!real.some(e => e.episode === '454'));
});

test('loadState restores position for an unchanged episode and writes the rebuilt queue', () => {
    const fresh = [ep(1), ep(2)];
    const stored = JSON.stringify({ queue: [ep(454), ep(1), ep(2)], queueIndex: 2, position: 812.5 });
    const { player, storage } = makePlayer(stored, fresh);
    assert.equal(player.loadState(), true);
    const st = player.state();
    assert.deepEqual(st.queue, fresh);
    assert.equal(st.queueIndex, 1);
    assert.equal(st.restoredPosition, 812.5);
    assert.deepEqual(JSON.parse(storage.data['tal-queue']), { queue: fresh, queueIndex: 1, position: 812.5 });
});

test('loadState drops the position when the current episode has a new URL', () => {
    const stored = JSON.stringify({ queue: [ep(206, 'old')], queueIndex: 0, position: 300 });
    const { player, storage } = makePlayer(stored, [ep(206)]);
    assert.equal(player.loadState(), true);
    assert.equal(player.state().restoredPosition, 0);
    assert.equal(player.state().queue[0].url, 'https://tal.test/new/206.mp3');
    assert.equal(JSON.parse(storage.data['tal-queue']).position, 0);
});

test('loadState clamps a saved index past the end before rebuilding', () => {
    const stored = JSON.stringify({ queue: [ep(1), ep(2)], queueIndex: 9, position: 40 });
    const { player } = makePlayer(stored, [ep(1), ep(2)]);
    assert.equal(player.loadState(), true);
    assert.equal(player.state().queueIndex, 1);
    assert.equal(player.state().restoredPosition, 40);
});

test('loadState returns false and leaves storage alone when nothing survives', () => {
    const stored = JSON.stringify({ queue: [ep(454)], queueIndex: 0, position: 10 });
    const { player, storage } = makePlayer(stored, [ep(1)]);
    assert.equal(player.loadState(), false);
    assert.equal(storage.data['tal-queue'], stored);
});

test('loadState returns false on corrupt storage', () => {
    for (const stored of ['{not json', JSON.stringify({ queue: [], queueIndex: 0 }),
                          JSON.stringify({ queue: [ep(1)] })]) {
        const { player } = makePlayer(stored, [ep(1)]);
        assert.equal(player.loadState(), false, stored);
    }
});
