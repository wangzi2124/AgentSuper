import { fetchWithTimeout } from './fetch';
const BASE = '/api/sessions';
function qs(params) {
    const parts = Object.entries(params)
        .filter(([, v]) => v !== undefined && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    return parts.length ? `?${parts.join('&')}` : '';
}
async function request(path, init, timeout = 10000) {
    const res = await fetchWithTimeout(path, init, timeout);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`${res.status} ${err || res.statusText}`);
    }
    return res.json();
}
// ===== 会话 CRUD =====
// [D2] 不再导出 `createSession`：会话由后端在首条消息时自动创建（`chat.py:_resolve_multi_agent_parent`），
// 前端只用客户端 genId 占位，故此包装函数无调用者。后端 `POST /api/sessions` 保留为编程/运维接口。
export async function listSessions(opts) {
    return request(`${BASE}${qs({
        project_id: opts?.project_id,
        workspace_id: opts?.workspace_id,
        roots: opts?.roots,
        search: opts?.search,
        archived: opts?.archived,
        kind: opts?.kind,
        limit: opts?.limit,
    })}`, { method: 'GET' }, 15000);
}
export async function getSession(sessionId) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}`, { method: 'GET' }, 10000);
}
export async function updateSession(sessionId, body) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    }, 10000);
}
export async function deleteSession(sessionId) {
    const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}`, { method: 'DELETE' }, 10000);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Delete session error: ${err || res.statusText}`);
    }
}
// 删除会话中的单条消息（及其 parts）；前端仍以墓碑防止缓存复活
export async function deleteSessionMessage(sessionId, messageId) {
    const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}`, { method: 'DELETE' }, 10000);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Delete session message error: ${err || res.statusText}`);
    }
}
// ===== 待处理任务队列（执行中追加，新→旧执行）=====
/** 执行中追加一条待处理任务；clientMsgId 幂等（重复投递只排一次） */
export async function enqueueSessionPrompt(sessionId, prompt, clientMsgId) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}/prompts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, delivery: 'queue', client_msg_id: clientMsgId }),
    }, 15000);
}
/** 列出待处理任务（按将执行顺序：最新在前） */
export async function listSessionPrompts(sessionId) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}/prompts`, {}, 10000);
}
/** 移除一条待处理任务 */
export async function removeSessionPrompt(sessionId, promptId) {
    const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}/prompts/${encodeURIComponent(promptId)}`, { method: 'DELETE' }, 10000);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Remove prompt error: ${err || res.statusText}`);
    }
}
// ===== 会话操作 =====
export async function forkSession(sessionId, messageId) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}/fork${qs({ message_id: messageId })}`, {
        method: 'POST',
    }, 15000);
}
export async function getSessionMessages(sessionId, opts) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}/messages${qs({
        after_seq: opts?.after_seq,
        limit: opts?.limit,
    })}`, { method: 'GET' }, 15000);
}
export async function compactSession(sessionId, checkpoint) {
    const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}/compact${qs({ checkpoint })}`, { method: 'POST' }, 30000);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Compact session error: ${err || res.statusText}`);
    }
}
export async function revertSession(sessionId, messageId) {
    const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}/revert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message_id: messageId }),
    }, 15000);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Revert session error: ${err || res.statusText}`);
    }
    return res.json();
}
// 中断正在运行的会话：真正停止后台 Agent 任务（配合前端本地 abort 双保险）
export async function interruptSession(sessionId) {
    const res = await fetchWithTimeout(`${BASE}/${encodeURIComponent(sessionId)}/interrupt`, { method: 'POST' }, 5000);
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`Interrupt session error: ${err || res.statusText}`);
    }
}
// [D2] `getSessionContext` / `getSessionChildren` / `createSession` 三个包装函数已删除（零调用者）。
// 后端 `GET /{id}/context`、`GET /{id}/children`、`POST /api/sessions` 保留为编程/运维接口
// （上下文 epoch 排障、子任务树排查、脚本化建会话），不再在前端暴露同名包装。
export async function getSessionStatus(sessionId) {
    return request(`${BASE}/${encodeURIComponent(sessionId)}/status`, { method: 'GET' }, 10000);
}
function _msgTypeToRole(msgType) {
    return {
        user: 'user', assistant: 'assistant', tool: 'tool',
        compaction: 'system', epoch: 'system', system: 'system',
    }[msgType] || 'system';
}
function _fmtTime(ms) {
    return ms ? new Date(ms).toISOString() : '';
}
// 列表：kind 对应旧 conv_type（chat / multi-agent）
export async function listConversations(convType) {
    const sessions = await listSessions({ kind: convType, archived: false, limit: 1000 });
    return sessions.map(s => ({
        id: s.id,
        title: s.title || '新对话',
        directory: s.directory || '',
        model: s.model || null,
        tokens_input: s.tokens_input,
        tokens_output: s.tokens_output,
        tokens_reasoning: s.tokens_reasoning,
        tokens_cache_read: s.tokens_cache_read,
        tokens_cache_write: s.tokens_cache_write,
        cost: s.cost,
        created_at: _fmtTime(s.time_created),
        updated_at: _fmtTime(s.time_updated),
    }));
}
// 详情：会话信息 + 全部消息（含 parts）
export async function getConversation(conversationId, convType) {
    const [session, messages] = await Promise.all([
        getSession(conversationId),
        getSessionMessages(conversationId),
    ]);
    if (convType && session.kind !== convType) {
        throw new Error('404 Conversation not found');
    }
    return {
        id: session.id,
        title: session.title || '新对话',
        directory: session.directory || '',
        model: session.model || null,
        tokens_input: session.tokens_input,
        tokens_output: session.tokens_output,
        tokens_reasoning: session.tokens_reasoning,
        tokens_cache_read: session.tokens_cache_read,
        tokens_cache_write: session.tokens_cache_write,
        cost: session.cost,
        created_at: _fmtTime(session.time_created),
        updated_at: _fmtTime(session.time_updated),
        messages: messages.map(m => {
            const msg = {
                id: m.id,
                role: m.role || _msgTypeToRole(m.type),
                content: m.data?.content ?? m.content ?? '',
                seq: m.seq,
            };
            if (m.data?.sources)
                msg.sources = m.data.sources;
            if (m.data?.steps)
                msg.steps = m.data.steps;
            if (m.data?.agents)
                msg.agents = m.data.agents;
            if (m.data?.files)
                msg.files = m.data.files;
            if (m.data?.voice)
                msg.voice = m.data.voice;
            if (m.data?.model)
                msg.model = m.data.model;
            if (m.data?.tokens)
                msg.tokens = m.data.tokens;
            if (m.data?.cost != null)
                msg.cost = m.data.cost;
            if (m.parts?.length)
                msg.parts = m.parts;
            return msg;
        }),
    };
}
// 重命名标题
export async function renameConversation(conversationId, title, _convType) {
    await updateSession(conversationId, { title });
}
// 删除会话
export async function deleteConversation(conversationId, _convType) {
    await deleteSession(conversationId);
}
