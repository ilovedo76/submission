// Shared-db writes of the MVP page: runs/{runId} (+ series docs from web_entry.split_for_db).
// store = {db, user} from store-read.openStore(), or null (signed out / db unavailable).
// Status values: "saved" | "no_permission" (write rejected) | "offline" (no store) | "error" (transient).

const DENIED = new Set([
  "not_granted", "permission_denied", "invalid_argument", "unauthenticated",
  "revoked", "capability_disabled", "capability_removed",
]);

export const EMPTY_NOTES = Object.freeze({ issues: "", plan: [] });

/** Rejection -> status. A well-formed write rejected invalid_argument means a level below the rule. */
export function writeStatus(err) {
  return err && DENIED.has(err.code) ? "no_permission" : "error";
}

function hasDb(store) {
  return !!(store && store.db && typeof store.db.doc === "function");
}

async function getData(db, path) {
  const snap = await db.doc(path).get();
  return snap && snap.exists ? snap.data() ?? null : null;
}

/** runs/{runId} document or null (missing, unreadable, or no store). */
export async function findRun(store, runId) {
  if (!hasDb(store) || !runId) return null;
  try {
    return await getData(store.db, `runs/${runId}`);
  } catch {
    return null;
  }
}

async function uploaderId(user) {
  try {
    return user && typeof user.id === "function" ? (await user.id()) ?? null : null;
  } catch {
    return null;
  }
}

/**
 * Save an analysis: result = {run, docs: [[path, doc], ...]} (docs from split_for_db, run doc first).
 * The run doc is written first, then each series doc, one at a time; series are skipped when the
 * stored run already holds the same series (same ruleHash and seriesParts, fully saved).
 */
export async function saveRun(store, result, { kind, fileNames, notes } = {}) {
  if (!hasDb(store)) return "offline";
  const { db, user } = store;
  const [[runPath, runDoc], ...series] = result.docs;
  try {
    const existing = await getData(db, runPath).catch(() => null);
    const now = new Date().toISOString();
    const sameSeries = !!existing && existing.seriesSaved === true
      && existing.ruleHash === runDoc.ruleHash
      && JSON.stringify(existing.seriesParts) === JSON.stringify(runDoc.seriesParts);
    const doc = {
      ...runDoc,
      kind: kind ?? runDoc.kind,
      fileNames: fileNames ?? runDoc.fileNames,
      uploader: await uploaderId(user),
      createdAt: existing?.createdAt ?? now,
      updatedAt: now,
      notes: existing?.notes ?? notes ?? { ...EMPTY_NOTES, plan: [] },
      seriesSaved: sameSeries,
    };
    await db.doc(runPath).set(doc);
    if (!sameSeries) {
      for (const [path, data] of series) await db.doc(path).set(data);
      await db.doc(runPath).update({ seriesSaved: true });
    }
    return "saved";
  } catch (err) {
    console.warn("saveRun failed", err);
    return writeStatus(err);
  }
}

/** Update the notes of runs/{runId} (the caller debounces). */
export async function saveNotes(store, runId, notes) {
  if (!hasDb(store)) return "offline";
  try {
    await store.db.doc(`runs/${runId}`).update({ notes, updatedAt: new Date().toISOString() });
    return "saved";
  } catch (err) {
    console.warn("saveNotes failed", err);
    return writeStatus(err);
  }
}
