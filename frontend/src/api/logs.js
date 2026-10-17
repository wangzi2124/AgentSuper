import { apiRequest } from './errors';
// 全链路日志 API 客户端（后端 /api/logs）
const BASE = '/api/logs';
// 链路阶段：与后端 chainlog.STAGES 对齐（用于筛选下拉与时间线分组着色）
export const STAGES = [
    'http', 'session', 'agent', 'tool', 'llm', 'permission', 'memory', 'vector', 'persist', 'system',
];
export const STAGE_LABELS = {
    http: 'HTTP',
    session: '会话',
    agent: '智能体',
    tool: '工具',
    llm: '模型调用',
    permission: '权限审批',
    memory: '记忆',
    vector: '知识库',
    persist: '落库',
    system: '系统',
};
export const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];
export const LEVEL_LABELS = {
    DEBUG: '调试', INFO: '信息', WARNING: '警告', ERROR: '错误', CRITICAL: '严重',
};
function query(filters, extra = {}) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries({ ...filters, ...extra })) {
        if (v === undefined || v === null || v === '' || v === false)
            continue;
        p.set(k, String(v));
    }
    const s = p.toString();
    return s ? `?${s}` : '';
}
// 链路列表（一次请求一行）
export function listTraces(filters, offset = 0, limit = 30) {
    return apiRequest(`${BASE}${query(filters, { view: 'traces', offset, limit })}`);
}
// 原始日志条目列表
export function listEntries(filters, offset = 0, limit = 50, order = 'desc') {
    return apiRequest(`${BASE}${query(filters, { view: 'entries', offset, limit, order })}`);
}
// 单条链路时间线
export function getTrace(traceId, limit = 2000) {
    return apiRequest(`${BASE}/traces/${encodeURIComponent(traceId)}?limit=${limit}`);
}
// 聚合统计
export function getStats(filters = {}) {
    return apiRequest(`${BASE}/stats${query(filters)}`);
}
// 筛选下拉取值
export function getFilterOptions() {
    return apiRequest(`${BASE}/filters`);
}
// 按 TTL / 行数上限裁剪（管理员）
export function cleanupLogs() {
    return apiRequest(`${BASE}/cleanup`, { method: 'POST' });
}
// 清理日志：按 trace_id / before 水位 / 全量（管理员）
export function clearLogs(payload = {}) {
    return apiRequest(`${BASE}/clear`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
}
