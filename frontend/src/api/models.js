import { apiRequest } from './errors';
export function modelsToOptions(models) {
    return models.map(m => ({
        value: m.id,
        label: m.name || m.id,
        desc: m.description || m.id,
    }));
}
export async function fetchModels() {
    return apiRequest('/api/models', { method: 'GET' }, true);
}
export async function fetchModelsConfig() {
    return apiRequest('/api/models/config', { method: 'GET' }, true);
}
// 按 DeepSeek V4 官方 tokenizer 估算 token（text 或 messages 二选一）
export async function estimateTokens(payload) {
    return apiRequest('/api/models/estimate-tokens', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }, true);
}
export async function saveCustomModel(entry) {
    return apiRequest('/api/models/custom', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(entry),
    }, true);
}
export async function deleteCustomModel(mid) {
    return apiRequest(`/api/models/custom/${encodeURIComponent(mid)}`, { method: 'DELETE' }, true);
}
export async function saveProvider(name, data) {
    return apiRequest(`/api/models/providers/${encodeURIComponent(name)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    }, true);
}
export async function deleteProvider(name) {
    return apiRequest(`/api/models/providers/${encodeURIComponent(name)}`, { method: 'DELETE' }, true);
}
export async function saveDefaults(default_model, small_model, image_caption_model = null, voice_model_size = null) {
    await apiRequest('/api/models/defaults', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_model, small_model, image_caption_model, voice_model_size }),
    }, true);
}
