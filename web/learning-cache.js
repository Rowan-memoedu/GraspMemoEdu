// Administrator answers and timers stay in memory, never Web Storage.
export function createLearningCache(getAccess) {
  const memory = new Map();
  const transient = key => getAccess()?.learning_policy?.persistence === 'memory'
    && /(^|:)(draft|elapsed|submission)(:|$)/.test(key);
  return {
    read(storage, key, fallback = null) {
      if (transient(key)) return memory.get(key) ?? fallback;
      try { return storage.getItem(key) ?? fallback; } catch { return fallback; }
    },
    write(storage, key, value) {
      if (transient(key)) { value === null ? memory.delete(key) : memory.set(key, value); return; }
      try { value === null ? storage.removeItem(key) : storage.setItem(key, value); } catch {}
    },
    clear() { memory.clear(); },
  };
}
