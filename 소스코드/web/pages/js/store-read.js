// Read side of the shared db (dashboard + MVP). Layout from web_entry.split_for_db:
//   runs/{runId}                       run summary (+ MVP fields uploader, createdAt, updatedAt, notes, kind)
//   runs/{runId}/series/{name}         {runId, name, part, parts, data: {model: rows}}
//   runs/{runId}/series/{name}__{k}    the same, split in parts k = 0..parts-1 (rows concatenated in k order)

export const LOAD_TIMEOUT_MS = 10_000;
export const SERIES_NAMES = ["market", "pchart", "heatmap", "heatmapBaseline", "pareto", "modelType"];

/** {db, user} or null (signed out / not granted / no answer within 10 s). user may be null. */
export async function openStore(claude = globalThis.claude, timeoutMs = LOAD_TIMEOUT_MS) {
  let timer, poll;
  const timeout = new Promise((resolve) => { timer = setTimeout(() => resolve(null), timeoutMs); });
  // window.claude is normally there at once; if not, wait for it within the same 10 s budget
  const host = claude?.use ? Promise.resolve(claude) : new Promise((resolve) => {
    poll = setInterval(() => { if (globalThis.claude?.use) resolve(globalThis.claude); }, 100);
  });
  try {
    const both = host.then((c) => Promise.all([c.use("db"), Promise.resolve(c.use("user")).catch(() => null)]));
    const got = await Promise.race([both, timeout]);
    if (!got) return null;
    const [db, user] = got;
    return db ? { db, user: user ?? null } : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
    clearInterval(poll);
  }
}

/** All run documents, each with its document id as `id`. */
export async function listRuns(store) {
  const snap = await store.db.collection("runs").get();
  return snap.docs.filter((d) => d.exists !== false).map((d) => ({ ...d.data(), id: d.id }));
}

function mergeInto(out, data) {
  for (const [scope, rows] of Object.entries(data || {})) {
    out[scope] = (out[scope] || []).concat(rows || []);
  }
}

async function readDoc(db, path) {
  const snap = await db.doc(path).get();
  return snap.exists ? snap.data() : null;
}

/** {name: {model: rows}} for one run; chunked series (name__k) merged in k order.
 *  Part counts come from the run's seriesParts; without it, a whole doc is tried first, then parts. */
export async function loadSeries(store, runId, run = null) {
  const { db } = store;
  const base = `runs/${runId}/series`;
  const meta = run ?? (await readDoc(db, `runs/${runId}`)) ?? {};
  const parts = meta.seriesParts || {};
  const names = Object.keys(parts).length ? Object.keys(parts) : SERIES_NAMES;
  const out = {};
  await Promise.all(names.map(async (name) => {
    const merged = {};
    let n = parts[name];
    if (n === 1 || n === undefined) {
      const doc = await readDoc(db, `${base}/${name}`);
      if (doc) { mergeInto(merged, doc.data); out[name] = merged; return; }
      if (n === 1) return;
      const first = await readDoc(db, `${base}/${name}__0`);
      if (!first) return;
      n = first.parts ?? 1;
    }
    const docs = await Promise.all(Array.from({ length: n }, (_, k) => readDoc(db, `${base}/${name}__${k}`)));
    docs.sort((a, b) => (a?.part ?? 0) - (b?.part ?? 0));
    for (const d of docs) if (d) mergeInto(merged, d.data);
    out[name] = merged;
  }));
  return out;
}

/** {id: display name} for uploader ids; unresolved or no user capability -> "알 수 없음". */
export async function userNames(store, ids) {
  const uniq = [...new Set((ids || []).filter(Boolean))];
  const out = Object.fromEntries(uniq.map((id) => [id, "알 수 없음"]));
  if (!uniq.length || !store?.user?.profiles) return out;
  try {
    const ps = await store.user.profiles(uniq);
    for (const id of uniq) out[id] = ps?.[id]?.name || "알 수 없음";
  } catch { /* names are a convenience */ }
  return out;
}
