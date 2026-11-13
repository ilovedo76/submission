// Archives the artifact host will not serve (.whl .zip .docx .xlsx) are published by build.py as
// base64 text parts; manifest.binaries = {"<original path>": ["<path>.b64.0.txt", ...]} (relative to
// the publish root). This module turns them back into bytes: fetchPublished() for one file, and
// installFetchShim() so Pyodide's own fetches (python_stdlib.zip, package wheels) and every other
// fetch of an original path get the decoded bytes. Everything else falls through to the real fetch.

export function b64ToBytes(s) {
  s = s.replace(/\s+/g, "");
  if (typeof Uint8Array.fromBase64 === "function") return Uint8Array.fromBase64(s);
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

const strip = (href) => href.split("#")[0].split("?")[0];

/** Map absolute href of each original path -> absolute hrefs of its parts. */
export function binaryMap(manifest, base) {
  const map = new Map();
  for (const [orig, parts] of Object.entries((manifest && manifest.binaries) || {})) {
    map.set(strip(new URL(orig, base).href), parts.map((p) => new URL(p, base).href));
  }
  return map;
}

async function joinParts(parts, fetchFn) {
  const chunks = await Promise.all(parts.map(async (u) => {
    const res = await fetchFn(u);
    if (!res.ok) throw new Error(`${u}: HTTP ${res.status}`);
    return b64ToBytes(await res.text());
  }));
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}

/** Bytes of a published file by its original relative path (decoded when it was encoded). */
export async function fetchPublished(path, manifest, base, fetchFn = (u) => fetch(u)) {
  const href = new URL(path, base).href;
  const parts = binaryMap(manifest, base).get(strip(href));
  if (parts) return joinParts(parts, fetchFn);
  const res = await fetchFn(href);
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

/** Wrap target.fetch so requests for encoded originals return their decoded bytes. Returns the real fetch. */
export function installFetchShim(manifest, base, target = globalThis) {
  const real = target.fetch.__realFetch || target.fetch.bind(target);
  const map = binaryMap(manifest, base);
  const shim = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input && input.url;
    let parts = null;
    try { parts = map.get(strip(new URL(url, base).href)); } catch { parts = null; }
    if (!parts) return real(input, init);
    const bytes = await joinParts(parts, real);
    return new Response(bytes, { status: 200, headers: { "Content-Type": "application/octet-stream" } });
  };
  shim.__realFetch = real;
  target.fetch = shim;
  return real;
}
