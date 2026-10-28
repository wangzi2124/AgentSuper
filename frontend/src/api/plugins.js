import { fetchWithTimeout } from './fetch';
// 插件 API 基础路径
const BASE = '/api/plugins';
async function unwrapError(res, fallback) {
    const err = await res.json().catch(() => null);
    return new Error((err === null || err === void 0 ? void 0 : err.detail) || (err === null || err === void 0 ? void 0 : err.message) || `${fallback}: ${res.statusText}`);
}
// 获取所有插件列表
export async function listPlugins() {
    const res = await fetchWithTimeout(BASE + '/');
    if (!res.ok)
        throw await unwrapError(res, 'Failed to list plugins');
    return res.json();
}
// 获取单个插件详情（含完整正文/说明）
export async function getPlugin(name) {
    const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name));
    if (!res.ok)
        throw await unwrapError(res, 'Failed to get plugin');
    return res.json();
}
// 切换插件启用/禁用状态
export async function togglePlugin(name, enabled) {
    const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(name) + '/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
    });
    if (!res.ok)
        throw await unwrapError(res, 'Toggle plugin failed');
}
