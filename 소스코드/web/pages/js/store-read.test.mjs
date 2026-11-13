import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { openStore, listRuns, loadSeries, userNames } from "./store-read.js";
import { fakeClaude } from "../../tools/fake_claude.js";

const fixture = JSON.parse(readFileSync(new URL("../../tools/dashboard_fixture.json", import.meta.url), "utf8"));

test("openStore: {db, user}; null when db is null or no answer in time", async () => {
  const c = fakeClaude(fixture);
  const s = await openStore(c);
  assert.equal(s.db, c.db);
  assert.ok(s.user);
  assert.equal(await openStore(fakeClaude(fixture, { mode: "nodb" })), null);
  assert.equal(await openStore(fakeClaude(fixture, { mode: "hang" }), 30), null);
  assert.equal(await openStore(undefined), null);
});

test("listRuns returns run docs only, each with its id", async () => {
  const runs = await listRuns(await openStore(fakeClaude(fixture)));
  const want = Object.keys(fixture).filter((p) => p.split("/").length === 2).map((p) => p.slice(5)).sort();
  assert.deepEqual(runs.map((r) => r.id).sort(), want);
  for (const r of runs) assert.equal(r.id, r.runId);
});

test("loadSeries merges __k chunks in k order (fixture run split at 8 KiB)", async () => {
  const store = await openStore(fakeClaude(fixture));
  const [run] = (await listRuns(store)).filter((r) => Object.values(r.seriesParts).some((n) => n > 1));
  assert.ok(run, "fixture has a chunked run");
  const series = await loadSeries(store, run.id);
  for (const [name, n] of Object.entries(run.seriesParts)) {
    const docs = n === 1 ? [fixture[`runs/${run.id}/series/${name}`]]
      : Array.from({ length: n }, (_, k) => fixture[`runs/${run.id}/series/${name}__${k}`]);
    const want = {};
    for (const d of docs) for (const [m, rows] of Object.entries(d.data)) want[m] = (want[m] || []).concat(rows);
    assert.deepEqual(series[name], want, name);
  }
  assert.deepEqual(Object.keys(series["market"]), ["전체", ...run.models]);
  const months = series.market["전체"].map((r) => r.m);
  assert.deepEqual(months, [...months].sort());
});

test("loadSeries: chunks merged by part even if stored out of order; missing run meta tolerated", async () => {
  const docs = {
    "runs/x_2025-12": { seriesParts: { pareto: 2 } },
    "runs/x_2025-12/series/pareto__1": { part: 1, parts: 2, data: { "전체": [{ type: "B" }], "A": [{ type: "b" }] } },
    "runs/x_2025-12/series/pareto__0": { part: 0, parts: 2, data: { "전체": [{ type: "A" }] } },
  };
  const store = await openStore(fakeClaude(docs));
  const s = await loadSeries(store, "x_2025-12");
  assert.deepEqual(s.pareto, { "전체": [{ type: "A" }, { type: "B" }], "A": [{ type: "b" }] });
  const s2 = await loadSeries(store, "y_2025-12");   // no run doc, nothing stored
  assert.deepEqual(s2, {});
});

test("userNames resolves profiles; empty name -> 알 수 없음", async () => {
  const store = await openStore(fakeClaude(fixture, { names: { u_alice: "앨리스" } }));
  assert.deepEqual(await userNames(store, ["u_alice", "u_bob", "u_alice", ""]),
    { u_alice: "앨리스", u_bob: "알 수 없음" });
  assert.deepEqual(await userNames({ db: {}, user: null }, ["u_a"]), { u_a: "알 수 없음" });
});
