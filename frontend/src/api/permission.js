import { addAuthHeaders } from './fetch';
// 权限 API 基础路径
const BASE = '/api/permission';
// 获取待审批的权限请求列表（含 expires_at，供前端倒计时/自动拒绝）
export async function fetchPendingRequests() {
    const res = await fetch(BASE + '/pending', { headers: await addAuthHeaders() });
    if (!res.ok)
        throw new Error('Failed to fetch pending requests');
    return res.json();
}
// 回复权限请求（允许/拒绝），可记住决策
export async function respondToRequest(requestId, decision, remember = false) {
    const res = await fetch(BASE + '/request/' + encodeURIComponent(requestId) + '/respond', {
        method: 'POST',
        headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ decision, remember }),
    });
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Respond error: ${err || res.statusText}`);
    }
}
// 获取所有可写工作区（主工作区 + 额外工作区）
export async function fetchWorkspaces() {
    const res = await fetch(BASE + '/workspaces', { headers: await addAuthHeaders() });
    if (!res.ok)
        throw new Error('Failed to fetch workspaces');
    return res.json();
}
// 浏览文件系统子目录（供工作目录选择器使用）
export async function browseDirectories(path) {
    const res = await fetch(BASE + '/browse?path=' + encodeURIComponent(path), { headers: await addAuthHeaders() });
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Browse error: ${err || res.statusText}`);
    }
    return res.json();
}
// 运行时新增可写工作区（免重启生效）
export async function addWorkspace(path) {
    const res = await fetch(BASE + '/workspaces', {
        method: 'POST',
        headers: await addAuthHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ path }),
    });
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Add workspace error: ${err || res.statusText}`);
    }
    return res.json();
}
// 运行时移除可写工作区
export async function removeWorkspace(path) {
    const res = await fetch(BASE + '/workspaces?path=' + encodeURIComponent(path), {
        method: 'DELETE',
        headers: await addAuthHeaders(),
    });
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Remove workspace error: ${err || res.statusText}`);
    }
    return res.json();
}
