// Presentation text may persist; learning state, answers and permissions do not.
const DB_NAME = 'graspmemoedu-content-v1';
const MAX_BYTES = 32 * 1024 * 1024, MAX_ENTRIES = 80;
const MAX_AGE = 7 * 24 * 60 * 60 * 1000;
const clone = value => structuredClone(value);

export function contentPath(path, method = 'GET') {
  const name = path.split('?')[0];
  if (method === 'POST') return ['choose', 'continue', 'select', 'reveal', 'skip', 'submit'].includes(name)
    || /^review\/(begin|sessions\/[^/]+\/(continue|reveal|submit))$/.test(name);
  if (method !== 'GET') return false;
  return ['state', 'guide', 'training/group', 'training/state', 'materials/content'].includes(name)
    || /^(tasks\/[^/]+\/answers|submissions\/[^/]+|review\/(sessions|submissions)\/[^/]+)$/.test(name);
}

export function contentKey(path) {
  const [name, raw] = path.split('?'), params = new URLSearchParams(raw);
  if (['state', 'choose', 'continue', 'select', 'reveal', 'skip', 'submit'].includes(name)
      || name.startsWith('submissions/') || name.startsWith('review/')) {
    const scope = new URLSearchParams();
    for (const key of ['subject_id', 'topic_id']) if (params.has(key)) scope.set(key, params.get(key));
    return (name.startsWith('review/') ? 'review' : 'state') + '?' + scope;
  }
  params.sort();
  return name + '?' + params;
}

export function readLifetime(path) {
  const name = path.split('?')[0];
  if (name === 'access') return 10000;
  if (['subjects', 'profile', 'catalog', 'training/catalog', 'training/bank', 'training/group', 'guide', 'graphs'].includes(name)) return 300000;
  if (name === 'dashboard') return 5000;
  return 0;
}

// Fresh server checks can reuse a complete response without moving it over the
// network again. These projections (including learning state) are memory only.
export function createValidationCache() {
  const records = new Map();
  let epoch = 0, bytes = 0;
  return {
    clear() { epoch++; records.clear(); bytes = 0; },
    prepare(key) { return {key, epoch, record: records.get(key)}; },
    decode(value, ticket, decodeContent, access) {
      const envelope = value?.$gme_state === 1 ? value : null;
      if (envelope && !Object.hasOwn(envelope, 'value')) {
        if (!ticket.record || ticket.record.revision !== envelope.revision) throw Error('state_cache_miss');
        value = clone(ticket.record.value);
        for (const [path, clock] of envelope.clocks || []) {
          let target = value;
          for (const key of path.slice(0, -1)) target = target[key];
          target[path.at(-1)] = clock;
        }
      } else {
        value = decodeContent(envelope ? envelope.value : value);
        const restore = item => {
          if (Array.isArray(item)) return item.map(restore);
          if (!item || typeof item !== 'object') return item;
          if (item.$gme_access) {
            if (item.$gme_access !== access?.access_revision) throw Error('access_cache_miss');
            return clone(access);
          }
          return Object.fromEntries(Object.entries(item).map(([key, child]) => [key, restore(child)]));
        };
        value = restore(value);
      }
      if (envelope && ticket.epoch === epoch) {
        const size = JSON.stringify(value).length * 2;
        bytes -= records.get(ticket.key)?.size || 0;
        records.delete(ticket.key);
        if (size <= MAX_BYTES) {
          records.set(ticket.key, {revision: envelope.revision, value: clone(value), size});
          bytes += size;
        }
        while (bytes > MAX_BYTES || records.size > MAX_ENTRIES) {
          const key = records.keys().next().value;
          bytes -= records.get(key).size; records.delete(key);
        }
      }
      return value;
    },
  };
}

export function createReadCache({now = Date.now} = {}) {
  const values = new Map(), pending = new Map();
  let epoch = 0;
  return {
    clear(...tags) {
      if (!tags.length) { epoch++; values.clear(); pending.clear(); return; }
      for (const [key, value] of values) if (tags.includes(value.tag)) values.delete(key);
      for (const [key, value] of pending) if (tags.includes(value.tag)) pending.delete(key);
    },
    async get(key, ttl, load, pendingTag = '', tag = '') {
      const saved = values.get(key);
      if (saved && now() - saved.at < ttl) return clone(saved.value);
      const flight = JSON.stringify([key, pendingTag]);
      if (pending.has(flight)) return clone(await pending.get(flight).task);
      const generation = epoch;
      const task = Promise.resolve().then(load);
      const entry = {task, tag};
      pending.set(flight, entry);
      try {
        const value = await task;
        if (generation === epoch && pending.get(flight) === entry) {
          if (ttl > 0) {
            values.delete(key);
            values.set(key, {at: now(), value: clone(value), tag});
            if (values.size > MAX_ENTRIES) values.delete(values.keys().next().value);
          }
        }
        return clone(value);
      } finally {
        if (pending.get(flight) === entry) pending.delete(flight);
      }
    },
  };
}

export function browserContentStore() {
  let connection;
  const open = () => connection ||= new Promise(resolve => {
    try {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore('content', {keyPath: 'key'});
      request.onsuccess = () => {
        request.result.onversionchange = () => { request.result.close(); connection = null; };
        resolve(request.result);
      };
      request.onerror = request.onblocked = () => resolve(null);
    } catch { resolve(null); }
  });
  async function transact(mode, work) {
    const db = await open();
    if (!db) return null;
    return new Promise(resolve => {
      try {
        const tx = db.transaction('content', mode), store = tx.objectStore('content');
        let result = null;
        tx.oncomplete = () => resolve(result);
        tx.onerror = tx.onabort = () => resolve(null);
        work(store, value => { result = value; });
      } catch { resolve(null); }
    });
  }
  return {
    read: key => transact('readonly', (store, done) => {
      const request = store.get(key); request.onsuccess = () => done(request.result);
    }),
    write: record => transact('readwrite', store => {
      store.put(record);
      const request = store.getAll();
      request.onsuccess = () => {
        const rows = request.result.sort((a, b) => b.at - a.at);
        let bytes = 0;
        rows.forEach((row, i) => {
          bytes += row.bytes;
          if (i >= MAX_ENTRIES || bytes > MAX_BYTES || Date.now() - row.at > MAX_AGE) store.delete(row.key);
        });
      };
    }),
    clear: () => transact('readwrite', store => store.clear()),
  };
}

export function createContentCache({store = browserContentStore(), now = Date.now} = {}) {
  const memory = new Map();
  let epoch = 0;
  // Storage can be blocked by browser settings or another tab. Never block a read.
  const bounded = async promise => {
    let timer;
    try { return await Promise.race([promise, new Promise(resolve => { timer = setTimeout(() => resolve(null), 100); })]); }
    catch { return null; } finally { clearTimeout(timer); }
  };
  function remember(key, record) {
    memory.delete(key); memory.set(key, record);
    while (memory.size > MAX_ENTRIES) memory.delete(memory.keys().next().value);
    let bytes = 0;
    for (const [id, item] of [...memory].reverse()) {
      bytes += item.bytes;
      if (bytes > MAX_BYTES) memory.delete(id);
    }
  }
  return {
    clear() { epoch++; memory.clear(); void store.clear().catch(() => {}); },
    async prepare(scope, path, persistent = true) {
      const key = JSON.stringify([scope, path]), generation = epoch;
      let record = memory.get(key);
      if (!record && persistent) {
        record = await bounded(store.read(key));
        // Disk data is disposable. Validate it before offering it as the basis
        // of a delta, especially when the next request commits a learning action.
        if (record) {
          try {
            if (!Array.isArray(record.texts) || !record.texts.every(text => typeof text === 'string')) record = null;
            else {
              const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(record.texts)));
              const revision = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
              if (revision !== record.revision) record = null;
            }
          } catch { record = null; }
        }
      }
      if (generation !== epoch) return {key, generation, persistent, record: null};
      if (record && (!Array.isArray(record.texts) || now() - record.at > MAX_AGE)) record = null;
      if (record) remember(key, record);
      return {key, generation, persistent, record};
    },
    decode(value, ticket) {
      if (value?.$gme_content !== 1) return value;
      const texts = value.texts === null
        ? (ticket.record?.revision === value.revision ? ticket.record.texts : null)
        : Array.isArray(value.texts) ? value.texts.map(text => {
          if (text && typeof text === 'object' && Object.keys(text).length === 1
              && Number.isInteger(text.$gme_previous)) return ticket.record?.texts?.[text.$gme_previous];
          return text;
        }) : null;
      if (!Array.isArray(texts) || !texts.every(text => typeof text === 'string')) throw new Error('content_cache_miss');
      const restore = item => {
        if (item && typeof item === 'object') {
          if (Object.keys(item).length === 1 && Number.isInteger(item.$gme_text)) {
            if (typeof texts[item.$gme_text] !== 'string') throw new Error('content_cache_miss');
            return texts[item.$gme_text];
          }
          if (Array.isArray(item)) return item.map(restore);
          return Object.fromEntries(Object.entries(item).map(([key, child]) => [key, restore(child)]));
        }
        return item;
      };
      const result = restore(value.state);
      if (ticket.generation === epoch) {
        const record = {key: ticket.key, revision: value.revision, texts, at: now(),
          bytes: texts.reduce((sum, text) => sum + text.length * 2, 0)};
        if (record.bytes <= MAX_BYTES / 2) {
          remember(ticket.key, record);
          if (ticket.persistent && value.texts !== null) void store.write(record).catch(() => {});
        }
      }
      return result;
    },
  };
}
