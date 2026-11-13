// Fake `window.claude` for tests and the local dashboard preview: db reads over a {docPath: doc}
// map (the shape web/tools/make_dashboard_fixture.py writes) and user.profiles over {id: name}.

function snap(path, docs) {
  const id = path.split("/").pop();
  const body = docs[path];
  return { id, exists: body !== undefined, data: () => (body === undefined ? undefined : structuredClone(body)) };
}

export function fakeDb(docs) {
  const reads = [];
  return {
    reads,
    doc(path) {
      return { id: path.split("/").pop(), get: async () => { reads.push(path); return snap(path, docs); } };
    },
    collection(path) {
      const depth = path.split("/").length + 1;
      return {
        get: async () => {
          reads.push(path + "/*");
          const ids = Object.keys(docs).filter((p) => p.startsWith(path + "/") && p.split("/").length === depth).sort();
          return { docs: ids.map((p) => snap(p, docs)) };
        },
      };
    },
  };
}

export function fakeUser(names = {}) {
  return {
    profiles: async (ids) => Object.fromEntries([].concat(ids).map((id) => [id, { id, name: names[id] ?? "" }])),
  };
}

/** mode: "ok" | "nodb" (use("db") -> null) | "hang" (never answers). */
export function fakeClaude(docs, { names = {}, mode = "ok" } = {}) {
  const db = fakeDb(docs);
  const user = fakeUser(names);
  return {
    db,
    use(name) {
      if (mode === "hang") return new Promise(() => {});
      if (name === "db") return Promise.resolve(mode === "nodb" ? null : db);
      if (name === "user") return Promise.resolve(user);
      return Promise.resolve(null);
    },
  };
}
