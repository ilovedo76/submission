import test from "node:test";
import assert from "node:assert/strict";
import { saveRun, findRun, saveNotes, writeStatus } from "./store-write.js";

// Fake db: doc(path).get/set/update; records every write in order.
function fakeDb({ existing = {}, reject = null } = {}) {
  const docs = structuredClone(existing);
  const writes = [];
  const fail = () => {
    if (reject) throw { code: reject, message: "rejected" };
  };
  return {
    docs,
    writes,
    doc(path) {
      return {
        path,
        async get() {
          const d = docs[path];
          return { id: path.split("/").pop(), exists: d !== undefined, data: () => (d === undefined ? undefined : structuredClone(d)) };
        },
        async set(data) {
          fail();
          writes.push(["set", path, structuredClone(data)]);
          docs[path] = structuredClone(data);
        },
        async update(data) {
          fail();
          if (docs[path] === undefined) throw { code: "not_found", message: "no doc" };
          writes.push(["update", path, structuredClone(data)]);
          docs[path] = { ...docs[path], ...structuredClone(data) };
        },
      };
    },
  };
}

const RID = "abcdef0123456789_2025-12";
function result() {
  return {
    run: { runId: RID, inputHash: "abcdef0123456789ffff", baseMonth: "2025-12", kind: "user" },
    docs: [
      [`runs/${RID}`, { runId: RID, inputHash: "abcdef0123456789ffff", baseMonth: "2025-12", kind: "user",
        fileNames: ["a.xlsx"], ruleHash: "h1", seriesParts: { market: 1, pareto: 1 } }],
      [`runs/${RID}/series/market`, { runId: RID, name: "market", part: 0, parts: 1, data: { 전체: [] } }],
      [`runs/${RID}/series/pareto`, { runId: RID, name: "pareto", part: 0, parts: 1, data: { 전체: [] } }],
    ],
  };
}
const user = { id: async () => "u-1" };

test("saveRun writes the run doc first with uploader/createdAt/updatedAt/kind/notes, then series", async () => {
  const db = fakeDb();
  const status = await saveRun({ db, user }, result(), { kind: "sample", fileNames: ["s.xlsx"] });
  assert.equal(status, "saved");
  const sets = db.writes.filter((w) => w[0] === "set");
  assert.equal(sets[0][1], `runs/${RID}`);
  assert.deepEqual(sets.slice(1).map((w) => w[1]), [`runs/${RID}/series/market`, `runs/${RID}/series/pareto`]);
  const run = db.docs[`runs/${RID}`];
  assert.equal(run.uploader, "u-1");
  assert.equal(run.kind, "sample");
  assert.deepEqual(run.fileNames, ["s.xlsx"]);
  assert.deepEqual(run.notes, { issues: "", plan: [] });
  assert.ok(!Number.isNaN(Date.parse(run.createdAt)));
  assert.equal(run.createdAt, run.updatedAt);
  assert.equal(run.seriesSaved, true);
});

test("saveRun preserves createdAt and notes on update and skips unchanged series", async () => {
  const notes = { issues: "기존 이슈", plan: [{ 담당: "김", 일정: "1월", 대책: "교체" }] };
  const existing = {
    [`runs/${RID}`]: { runId: RID, createdAt: "2025-01-01T00:00:00.000Z", updatedAt: "2025-01-01T00:00:00.000Z",
      notes, ruleHash: "h1", seriesParts: { market: 1, pareto: 1 }, seriesSaved: true, uploader: "u-0" },
  };
  const db = fakeDb({ existing });
  assert.equal(await saveRun({ db, user }, result(), { kind: "user" }), "saved");
  const run = db.docs[`runs/${RID}`];
  assert.equal(run.createdAt, "2025-01-01T00:00:00.000Z");
  assert.notEqual(run.updatedAt, run.createdAt);
  assert.deepEqual(run.notes, notes);
  assert.equal(db.writes.length, 1, "series unchanged -> only the run doc is written");
});

test("saveRun uses form notes when no notes are stored yet", async () => {
  const db = fakeDb();
  const notes = { issues: "새 이슈", plan: [] };
  await saveRun({ db, user }, result(), { kind: "user", notes });
  assert.deepEqual(db.docs[`runs/${RID}`].notes, notes);
});

test("saveRun returns no_permission when the write is rejected", async () => {
  for (const code of ["not_granted", "invalid_argument", "permission_denied"]) {
    const db = fakeDb({ reject: code });
    assert.equal(await saveRun({ db, user }, result(), { kind: "user" }), "no_permission");
  }
});

test("saveRun returns error on a transient failure, offline when the store is null", async () => {
  assert.equal(await saveRun({ db: fakeDb({ reject: "unavailable" }), user }, result(), { kind: "user" }), "error");
  assert.equal(await saveRun(null, result(), { kind: "user" }), "offline");
  assert.equal(await saveRun({ db: null, user: null }, result(), { kind: "user" }), "offline");
});

test("saveRun without a user stores uploader null", async () => {
  const db = fakeDb();
  await saveRun({ db, user: null }, result(), { kind: "user" });
  assert.equal(db.docs[`runs/${RID}`].uploader, null);
});

test("findRun returns the run or null", async () => {
  const db = fakeDb({ existing: { [`runs/${RID}`]: { runId: RID, notes: { issues: "x", plan: [] } } } });
  assert.equal((await findRun({ db, user }, RID)).notes.issues, "x");
  assert.equal(await findRun({ db, user }, "nope_2025-01"), null);
  assert.equal(await findRun(null, RID), null);
});

test("saveNotes updates notes + updatedAt only", async () => {
  const db = fakeDb({ existing: { [`runs/${RID}`]: { runId: RID, createdAt: "c" } } });
  const notes = { issues: "i", plan: [] };
  assert.equal(await saveNotes({ db, user }, RID, notes), "saved");
  assert.deepEqual(Object.keys(db.writes[0][2]).sort(), ["notes", "updatedAt"]);
  assert.equal(db.docs[`runs/${RID}`].createdAt, "c");
  assert.equal(await saveNotes(null, RID, notes), "offline");
  assert.equal(await saveNotes({ db: fakeDb({ reject: "not_granted" }), user }, RID, notes), "no_permission");
});

test("writeStatus maps error codes", () => {
  assert.equal(writeStatus({ code: "not_granted" }), "no_permission");
  assert.equal(writeStatus({ code: "revoked" }), "no_permission");
  assert.equal(writeStatus({ code: "unavailable" }), "error");
  assert.equal(writeStatus(new Error("x")), "error");
});
