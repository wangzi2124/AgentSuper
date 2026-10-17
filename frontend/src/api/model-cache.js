const CACHE_KEY = 'agentsuper:model-config-cache:v1';
export function loadModelCache() {
    try {
        const raw = localStorage.getItem(CACHE_KEY);
        if (!raw)
            return null;
        const parsed = JSON.parse(raw);
        if (!parsed || !Array.isArray(parsed.models))
            return null;
        return parsed;
    }
    catch {
        return null;
    }
}
export function saveModelCache(cache) {
    try {
        // 增量写保留未提供字段（聊天层只写 models/defaults，管理页补 providers/source_path）
        const prev = loadModelCache();
        const merged = {
            ...prev,
            ...cache,
            savedAt: cache.savedAt || Date.now(),
            providers: cache.providers ?? prev?.providers,
            source_path: cache.source_path ?? prev?.source_path,
        };
        localStorage.setItem(CACHE_KEY, JSON.stringify(merged));
    }
    catch {
        // 存储不可用（隐私模式/配额）时静默忽略，后端仍是事实来源
    }
}
