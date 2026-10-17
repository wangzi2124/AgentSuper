/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, nextTick, ref, watch, onMounted, onBeforeUnmount } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useMultiAgentStore } from '../stores/multiAgent';
import { useAuthStore } from '../stores/auth';
import { useChatSettingsStore } from '../stores/chatSettings';
import { synthesize, speakNative, stopNative } from '../api/voice';
import MultiAgentResponse from '../components/MultiAgentResponse.vue';
import ChatInput from '../components/ChatInput.vue';
import VoiceBubble from '../components/VoiceBubble.vue';
const route = useRoute();
const router = useRouter();
const agent = useMultiAgentStore();
const auth = useAuthStore();
const parentRef = ref();
const chatInputRef = ref();
const isNearBottom = ref(true);
function isImgAvatar(v) {
    return !!v && (v.startsWith('data:') || v.startsWith('http'));
}
// [F8] 聊天图片点击放大预览（当前预览图的 data URL；空串 = 未预览）
const previewImage = ref('');
const messages = computed(() => agent.messages);
// ── ChatGPT 式滚动交互 ──
const showScrollBtn = computed(() => !isNearBottom.value && messages.value.length > 0);
function scrollToBottom(behavior = 'smooth') {
    const el = parentRef.value;
    if (!el)
        return;
    isNearBottom.value = true;
    el.scrollTo({ top: el.scrollHeight, behavior });
}
watch(() => messages.value.length, async () => {
    await nextTick();
    if (isNearBottom.value && parentRef.value) {
        parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' });
    }
});
watch(() => {
    const msgs = messages.value;
    if (msgs.length === 0)
        return '';
    return msgs[msgs.length - 1]?.content || '';
}, async () => {
    await nextTick();
    if (isNearBottom.value && parentRef.value) {
        parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' });
    }
});
watch(() => {
    const msgs = messages.value;
    if (msgs.length === 0)
        return 0;
    return msgs[msgs.length - 1]?.agents?.reduce((n, a) => n + (a.steps?.length || 0), 0) ?? 0;
}, async () => {
    await nextTick();
    if (isNearBottom.value && parentRef.value) {
        parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' });
    }
});
// ── [模型目录] 消息头 usage 摘要（model · in→out · cost；hover 看明细）──
function fmtTokens(n) {
    if (!n)
        return '';
    return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : `${n}`;
}
function fmtCost(n) {
    if (!n || n <= 0)
        return '';
    return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`;
}
function usageLabel(msg) {
    const t = msg.tokens || {};
    const parts = [];
    if (msg.model)
        parts.push(msg.model);
    if (t.input || t.output)
        parts.push(`${fmtTokens(t.input)}→${fmtTokens(t.output)}`);
    const c = fmtCost(msg.cost);
    if (c)
        parts.push(c);
    return parts.join(' · ');
}
function usageDetail(msg) {
    const t = msg.tokens || {};
    const lines = [`模型: ${msg.model || '-'}`];
    if (t.input != null || t.output != null || t.reasoning != null || t.cache_read != null || t.cache_write != null) {
        lines.push(`输入 ${fmtTokens(t.input)} · 输出 ${fmtTokens(t.output)} · 推理 ${fmtTokens(t.reasoning)} · 缓存读 ${fmtTokens(t.cache_read)} · 缓存写 ${fmtTokens(t.cache_write)}`);
    }
    lines.push(`成本: ${fmtCost(msg.cost) || '$0.00'}`);
    return lines.join('\n');
}
onMounted(() => {
    const id = route.params.id;
    if (id)
        agent.loadConversation(id);
    window.addEventListener('keydown', onGlobalKeydown);
});
// ── 双击 Esc 快捷取消：连按两次 Esc → 取消当前任务并清空队列 ──
let escCount = 0;
let escTimer = null;
function onGlobalKeydown(e) {
    if (e.key !== 'Escape') {
        escCount = 0;
        return;
    }
    const now = Date.now();
    // 1s 外的第一次 Esc 视为新的一次
    if (escCount === 0) {
        if (escTimer) {
            clearTimeout(escTimer);
            escTimer = null;
        }
        escTimer = window.setTimeout(() => { escCount = 0; }, 1000);
    }
    escCount += 1;
    if (escCount === 2) {
        escCount = 0;
        if (escTimer) {
            clearTimeout(escTimer);
            escTimer = null;
        }
        if (agent.loading || pendingQueue.value.length) {
            handleCancel();
        }
    }
}
watch(() => route.params.id, (newId) => {
    if (newId)
        agent.loadConversation(newId);
    else
        agent.newChat();
});
function onScroll(e) {
    if (!e.isTrusted)
        return;
    const el = parentRef.value;
    if (!el)
        return;
    isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
}
function handleSend(text, files, voice) {
    markPendingAutoRead();
    // 任务正在进行时入队，完成后自动发出（排队方案）
    if (agent.loading) {
        pendingQueue.value.push({ text, files: files || [], voice });
        return;
    }
    agent.send(text, undefined, files || [], voice).then((completed) => {
        if (completed && agent.conversationId && route.name !== 'MultiAgentConversation') {
            router.push({ name: 'MultiAgentConversation', params: { id: agent.conversationId } });
        }
    });
}
const pendingQueue = ref([]);
const queueCount = computed(() => pendingQueue.value.length);
watch(() => agent.loading, (loading) => {
    // 空闲后自动发队列中的下一条
    if (!loading && pendingQueue.value.length) {
        const next = pendingQueue.value.shift();
        agent.send(next.text, undefined, next.files, next.voice).then((completed) => {
            if (completed && agent.conversationId && route.name !== 'MultiAgentConversation') {
                router.push({ name: 'MultiAgentConversation', params: { id: agent.conversationId } });
            }
        });
    }
});
function cancelQueue() {
    pendingQueue.value = [];
}
function handleCancel() { cancelQueue(); agent.cancel(); }
function handleUndo(index) {
    if (agent.loading)
        agent.cancel();
    const msgText = messages.value[index]?.content;
    agent.undoMessage(index);
    if (msgText) {
        chatInputRef.value?.setText(msgText);
        nextTick(() => chatInputRef.value?.focus());
    }
}
// [撤回改动] 恢复某条 assistant 消息对应轮次的文件改动
async function handleRestore(msg) {
    if (agent.loading)
        agent.cancel();
    try {
        const r = await agent.restoreSnapshot(msg.id);
        if (r?.already) {
            agent.setNotice('该轮次改动已恢复过，无需重复操作');
        }
        else if (r && r.missing && r.missing.length) {
            agent.setNotice(`已撤回 ${r.restored_files?.length ?? r.internal ?? 0} 个文件；${r.missing.length} 个文件已无法恢复（${r.missing.join('、')}）`);
        }
        else {
            agent.setNotice('已撤回本轮文件改动');
        }
    }
    catch (e) {
        console.error('Restore failed:', e);
        agent.setNotice(`撤回失败：${e instanceof Error ? e.message : '未知错误'}`);
    }
}
function handleMessageDelete(messageId) {
    if (agent.loading)
        agent.cancel();
    agent.deleteMessage(messageId);
}
// [队列] 移除一条待处理任务
const pendingCount = computed(() => agent.pendingPrompts.length);
async function handleRemoveQueued(msg) {
    const pid = msg.queuePromptId || msg.clientMsgId;
    if (!pid || !agent.activeSessionId)
        return;
    try {
        await agent.removeQueuedPrompt(agent.activeSessionId, pid);
    }
    catch (e) {
        agent.setNotice(`移除失败：${e instanceof Error ? e.message : '未知错误'}`);
    }
}
// [TTS] AI 消息朗读
const speakingId = ref(null);
let speakAudio = null;
const chatSettings = useChatSettingsStore();
const autoRead = computed(() => chatSettings.autoRead);
let pendingAutoRead = false;
function markPendingAutoRead() { if (autoRead.value)
    pendingAutoRead = true; }
watch(() => agent.loading, (loading) => {
    if (loading || !pendingAutoRead)
        return;
    pendingAutoRead = false;
    const last = agent.messages[agent.messages.length - 1];
    if (!last || last.role !== 'assistant' || last.isError || !last.content)
        return;
    const text = last.content.trim();
    if (text)
        handleSpeak(last.id, text);
});
async function handleSpeak(id, content) {
    if (speakingId.value === id) {
        stopSpeaking();
        return;
    }
    stopSpeaking();
    let text = (content || '').trim();
    if (!text)
        return;
    const MAX_TTS_CHARS = 1200;
    if (text.length > MAX_TTS_CHARS)
        text = text.slice(0, MAX_TTS_CHARS) + '。';
    speakingId.value = id;
    try {
        const url = await synthesize(text, undefined, chatSettings.ttsLang);
        const audio = new Audio(url);
        speakAudio = audio;
        audio.onended = () => { if (speakingId.value === id)
            speakingId.value = null; };
        audio.onerror = () => { stopSpeaking(); speakNative((content || '').trim(), chatSettings.ttsLang); };
        await audio.play();
    }
    catch {
        // [fix] 后端 qwen TTS 失败降级时也要带上「朗读语言」，而非固定中文
        speakNative((content || '').trim(), chatSettings.ttsLang);
    }
}
function stopSpeaking() {
    if (speakAudio) {
        try {
            speakAudio.pause();
        }
        catch { /* noop */ }
        if (speakAudio.src)
            URL.revokeObjectURL(speakAudio.src);
        speakAudio = null;
    }
    stopNative();
    speakingId.value = null;
}
onBeforeUnmount(() => {
    stopSpeaking();
    window.removeEventListener('keydown', onGlobalKeydown);
    if (escTimer)
        clearTimeout(escTimer);
});
function handleRetry(messageId) {
    agent.manualRetry(messageId);
}
const copiedId = ref(null);
async function handleCopy(messageId, text) {
    if (!text)
        return;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
        }
        else {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        }
        copiedId.value = messageId;
        setTimeout(() => { if (copiedId.value === messageId)
            copiedId.value = null; }, 1500);
    }
    catch (e) {
        console.error('Copy failed:', e);
    }
}
/* @@CHAT_TABLIST_SCRIPT@@ */
// ── 会话标签条：聊天框顶部切换 / 新建会话（数据源 = agent.conversations） ──
agent.loadConversations();
function switchConversation(id) {
    if (id === agent.conversationId)
        return;
    router.push({ name: 'MultiAgentConversation', params: { id } });
}
function newConversation() {
    agent.newChat();
    router.push({ name: 'MultiAgent' });
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "multi-agent-view" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "chat-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "chat-heading" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "header-actions" },
});
if (__VLS_ctx.agent.queuePosition != null) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "status-badge queued" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.circle)({
        cx: "12",
        cy: "12",
        r: "10",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
        points: "12 6 12 12 16 14",
    });
    (__VLS_ctx.agent.queuePosition);
}
else if (__VLS_ctx.agent.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "status-badge running" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pulse-dot" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "chat-tablist" },
});
for (const [c] of __VLS_getVForSourceType((__VLS_ctx.agent.conversations))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.switchConversation(c.id);
            } },
        key: (c.id),
        ...{ class: "chat-tab" },
        ...{ class: ({ current: c.id === __VLS_ctx.agent.conversationId }) },
        title: (c.title || '未命名会话'),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "chat-tab-title" },
    });
    (c.title || '未命名会话');
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ onClick: (__VLS_ctx.newConversation) },
    ...{ class: "chat-tab chat-tab-new" },
    title: "新建会话",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "12",
    y1: "5",
    x2: "12",
    y2: "19",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "5",
    y1: "12",
    x2: "19",
    y2: "12",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "chat-body" },
});
if (__VLS_ctx.messages.length === 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-state" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-orb" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-orb-core" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-orb-ring" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "empty-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "empty-hint" },
    });
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onScroll: (__VLS_ctx.onScroll) },
        ref: "parentRef",
        ...{ class: "message-list" },
    });
    /** @type {typeof __VLS_ctx.parentRef} */ ;
    for (const [msg, idx] of __VLS_getVForSourceType((__VLS_ctx.messages))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (msg.id),
            ...{ class: "message-wrapper" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "chat-message" },
            ...{ class: ([msg.role, { 'is-error': msg.isError, queued: msg.role === 'user' && msg.queued }]) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "avatar" },
            ...{ class: (msg.role) },
        });
        if (msg.role === 'user') {
            if (__VLS_ctx.isImgAvatar(__VLS_ctx.auth.avatar)) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
                    ...{ class: "avatar-img" },
                    src: (__VLS_ctx.auth.avatar),
                    alt: "avatar",
                });
            }
            else if (__VLS_ctx.auth.avatar) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
                (__VLS_ctx.auth.avatar);
            }
            else {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            }
        }
        else if (msg.isError) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "bubble" },
        });
        if (msg.role === 'user') {
            if (msg.queued) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "queued-badge" },
                    title: "当前消息正在执行中，已排到待处理队列（后续按新→旧执行）",
                });
                if (__VLS_ctx.pendingCount > 1) {
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
                    (__VLS_ctx.pendingCount);
                }
                __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!!(__VLS_ctx.messages.length === 0))
                                return;
                            if (!(msg.role === 'user'))
                                return;
                            if (!(msg.queued))
                                return;
                            __VLS_ctx.handleRemoveQueued(msg);
                        } },
                    ...{ class: "queued-remove" },
                    title: "从待处理队列移除",
                });
            }
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "content" },
            });
            (msg.content);
            if (msg.voice) {
                /** @type {[typeof VoiceBubble, ]} */ ;
                // @ts-ignore
                const __VLS_0 = __VLS_asFunctionalComponent(VoiceBubble, new VoiceBubble({
                    voice: (msg.voice),
                }));
                const __VLS_1 = __VLS_0({
                    voice: (msg.voice),
                }, ...__VLS_functionalComponentArgsRest(__VLS_0));
            }
            if (msg.files && msg.files.length) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "msg-files" },
                });
                for (const [f, fi] of __VLS_getVForSourceType((msg.files))) {
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                        key: (fi),
                        ...{ class: "msg-file" },
                    });
                    if (f.mime_type?.startsWith('image/')) {
                        __VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
                            ...{ onClick: (...[$event]) => {
                                    if (!!(__VLS_ctx.messages.length === 0))
                                        return;
                                    if (!(msg.role === 'user'))
                                        return;
                                    if (!(msg.files && msg.files.length))
                                        return;
                                    if (!(f.mime_type?.startsWith('image/')))
                                        return;
                                    __VLS_ctx.previewImage = `data:${f.mime_type};base64,${f.data}`;
                                } },
                            src: (f._thumb ? `data:image/jpeg;base64,${f._thumb}` : `data:${f.mime_type};base64,${f.data}`),
                            ...{ class: "msg-file-image" },
                            alt: "",
                        });
                    }
                    if (f._caption) {
                        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                            ...{ class: "msg-file-caption" },
                        });
                        (f._caption);
                    }
                    else {
                        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                            ...{ class: "msg-file-name" },
                        });
                        (f.filename);
                    }
                }
            }
        }
        else {
            /** @type {[typeof MultiAgentResponse, ]} */ ;
            // @ts-ignore
            const __VLS_3 = __VLS_asFunctionalComponent(MultiAgentResponse, new MultiAgentResponse({
                ...{ 'onRestore': {} },
                message: (msg),
                routingStatus: (__VLS_ctx.agent.routingStatus),
                isLast: (idx === __VLS_ctx.messages.length - 1),
                loadDiff: (__VLS_ctx.agent.fetchMessageDiff),
            }));
            const __VLS_4 = __VLS_3({
                ...{ 'onRestore': {} },
                message: (msg),
                routingStatus: (__VLS_ctx.agent.routingStatus),
                isLast: (idx === __VLS_ctx.messages.length - 1),
                loadDiff: (__VLS_ctx.agent.fetchMessageDiff),
            }, ...__VLS_functionalComponentArgsRest(__VLS_3));
            let __VLS_6;
            let __VLS_7;
            let __VLS_8;
            const __VLS_9 = {
                onRestore: (...[$event]) => {
                    if (!!(__VLS_ctx.messages.length === 0))
                        return;
                    if (!!(msg.role === 'user'))
                        return;
                    __VLS_ctx.handleRestore(msg);
                }
            };
            var __VLS_5;
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "message-footer" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "time" },
        });
        (msg.timestamp.toLocaleTimeString());
        if (msg.role !== 'user' && (msg.model || (msg.tokens && (msg.tokens.input || msg.tokens.output)))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "msg-usage" },
                title: (__VLS_ctx.usageDetail(msg)),
            });
            (__VLS_ctx.usageLabel(msg));
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "message-actions" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "btn-wrapper" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.messages.length === 0))
                        return;
                    __VLS_ctx.handleCopy(msg.id, msg.content);
                } },
            ...{ class: "icon-btn" },
            title: "复制",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
            width: "14",
            height: "14",
            viewBox: "0 0 24 24",
            fill: "none",
            stroke: "currentColor",
            'stroke-width': "2",
            'stroke-linecap': "round",
            'stroke-linejoin': "round",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.rect, __VLS_intrinsicElements.rect)({
            x: "9",
            y: "9",
            width: "13",
            height: "13",
            rx: "2",
            ry: "2",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.path, __VLS_intrinsicElements.path)({
            d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1",
        });
        if (__VLS_ctx.copiedId === msg.id) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "copy-toast" },
            });
        }
        if (msg.role !== 'user') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.messages.length === 0))
                            return;
                        if (!(msg.role !== 'user'))
                            return;
                        __VLS_ctx.handleSpeak(msg.id, msg.content);
                    } },
                ...{ class: "icon-btn speak-btn" },
                ...{ class: ({ speaking: __VLS_ctx.speakingId === msg.id }) },
                title: (__VLS_ctx.speakingId === msg.id ? '停止朗读' : '朗读'),
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                width: "14",
                height: "14",
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'stroke-width': "2",
                'stroke-linecap': "round",
                'stroke-linejoin': "round",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.polygon)({
                points: "11 5 6 9 2 9 2 15 6 15 11 19 11 5",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M15.54 8.46a5 5 0 0 1 0 7.07",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M19.07 4.93a10 10 0 0 1 0 14.14",
            });
        }
        if (msg.role === 'user') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.messages.length === 0))
                            return;
                        if (!(msg.role === 'user'))
                            return;
                        __VLS_ctx.handleUndo(idx);
                    } },
                ...{ class: "icon-btn" },
                title: "撤销到此处",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                width: "14",
                height: "14",
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'stroke-width': "2",
                'stroke-linecap': "round",
                'stroke-linejoin': "round",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M3 7v6h6",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13",
            });
        }
        if (msg.isError && msg.errorInfo?.retryable) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.messages.length === 0))
                            return;
                        if (!(msg.isError && msg.errorInfo?.retryable))
                            return;
                        __VLS_ctx.handleRetry(msg.id);
                    } },
                ...{ class: "icon-btn" },
                title: "重试",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                width: "14",
                height: "14",
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'stroke-width': "2",
                'stroke-linecap': "round",
                'stroke-linejoin': "round",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M23 4v6h-6",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M20.49 15a9 9 0 1 1-2.12-9.36L23 10",
            });
        }
        if (msg.isError && __VLS_ctx.agent.retryCountdown > 0) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ class: "retry-countdown" },
                title: "自动重试中",
            });
            (__VLS_ctx.agent.retryCountdown);
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.messages.length === 0))
                        return;
                    __VLS_ctx.handleMessageDelete(msg.id);
                } },
            ...{ class: "icon-btn delete-btn" },
            title: "删除消息",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
            width: "14",
            height: "14",
            viewBox: "0 0 24 24",
            fill: "none",
            stroke: "currentColor",
            'stroke-width': "2",
            'stroke-linecap': "round",
            'stroke-linejoin': "round",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
            points: "3 6 5 6 21 6",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
            d: "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
        });
    }
}
const __VLS_10 = {}.transition;
/** @type {[typeof __VLS_components.Transition, typeof __VLS_components.transition, typeof __VLS_components.Transition, typeof __VLS_components.transition, ]} */ ;
// @ts-ignore
const __VLS_11 = __VLS_asFunctionalComponent(__VLS_10, new __VLS_10({
    name: "scroll-fade",
}));
const __VLS_12 = __VLS_11({
    name: "scroll-fade",
}, ...__VLS_functionalComponentArgsRest(__VLS_11));
__VLS_13.slots.default;
if (__VLS_ctx.showScrollBtn) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.showScrollBtn))
                    return;
                __VLS_ctx.scrollToBottom();
            } },
        ...{ class: "scroll-to-bottom-btn" },
        title: "回到底部",
        'aria-label': "回到底部",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "18",
        height: "18",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2.2",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M12 5v14",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M19 12l-7 7-7-7",
    });
}
var __VLS_13;
if (__VLS_ctx.agent.notice) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "chat-notice" },
    });
    (__VLS_ctx.agent.notice);
}
if (__VLS_ctx.queueCount) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "send-queue-bar" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "send-queue-text" },
    });
    (__VLS_ctx.queueCount);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.cancelQueue) },
        ...{ class: "send-queue-clear" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "chat-footer" },
});
/** @type {[typeof ChatInput, ]} */ ;
// @ts-ignore
const __VLS_14 = __VLS_asFunctionalComponent(ChatInput, new ChatInput({
    ...{ 'onSend': {} },
    ...{ 'onCancel': {} },
    ref: "chatInputRef",
    loading: (__VLS_ctx.agent.loading),
}));
const __VLS_15 = __VLS_14({
    ...{ 'onSend': {} },
    ...{ 'onCancel': {} },
    ref: "chatInputRef",
    loading: (__VLS_ctx.agent.loading),
}, ...__VLS_functionalComponentArgsRest(__VLS_14));
let __VLS_17;
let __VLS_18;
let __VLS_19;
const __VLS_20 = {
    onSend: (__VLS_ctx.handleSend)
};
const __VLS_21 = {
    onCancel: (__VLS_ctx.handleCancel)
};
/** @type {typeof __VLS_ctx.chatInputRef} */ ;
var __VLS_22 = {};
var __VLS_16;
if (__VLS_ctx.previewImage) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.previewImage))
                    return;
                __VLS_ctx.previewImage = '';
            } },
        ...{ class: "image-preview-overlay" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.previewImage))
                    return;
                __VLS_ctx.previewImage = '';
            } },
        src: (__VLS_ctx.previewImage),
        ...{ class: "image-preview-img" },
        alt: "预览",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.previewImage))
                    return;
                __VLS_ctx.previewImage = '';
            } },
        ...{ class: "image-preview-close" },
    });
}
/** @type {__VLS_StyleScopedClasses['multi-agent-view']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-header']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['header-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['status-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['queued']} */ ;
/** @type {__VLS_StyleScopedClasses['status-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['running']} */ ;
/** @type {__VLS_StyleScopedClasses['pulse-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-tablist']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['current']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-tab-title']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-tab']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-tab-new']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-body']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-orb']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-orb-core']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-orb-ring']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-title']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['message-list']} */ ;
/** @type {__VLS_StyleScopedClasses['message-wrapper']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-message']} */ ;
/** @type {__VLS_StyleScopedClasses['queued']} */ ;
/** @type {__VLS_StyleScopedClasses['is-error']} */ ;
/** @type {__VLS_StyleScopedClasses['avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['avatar-img']} */ ;
/** @type {__VLS_StyleScopedClasses['bubble']} */ ;
/** @type {__VLS_StyleScopedClasses['queued-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['queued-remove']} */ ;
/** @type {__VLS_StyleScopedClasses['content']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-files']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-file']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-file-image']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-file-caption']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['message-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['time']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-usage']} */ ;
/** @type {__VLS_StyleScopedClasses['message-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-wrapper']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['copy-toast']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['speak-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['speaking']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['retry-countdown']} */ ;
/** @type {__VLS_StyleScopedClasses['icon-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['delete-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['scroll-to-bottom-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-notice']} */ ;
/** @type {__VLS_StyleScopedClasses['send-queue-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['send-queue-text']} */ ;
/** @type {__VLS_StyleScopedClasses['send-queue-clear']} */ ;
/** @type {__VLS_StyleScopedClasses['chat-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['image-preview-overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['image-preview-img']} */ ;
/** @type {__VLS_StyleScopedClasses['image-preview-close']} */ ;
// @ts-ignore
var __VLS_23 = __VLS_22;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            MultiAgentResponse: MultiAgentResponse,
            ChatInput: ChatInput,
            VoiceBubble: VoiceBubble,
            agent: agent,
            auth: auth,
            parentRef: parentRef,
            chatInputRef: chatInputRef,
            isImgAvatar: isImgAvatar,
            previewImage: previewImage,
            messages: messages,
            showScrollBtn: showScrollBtn,
            scrollToBottom: scrollToBottom,
            usageLabel: usageLabel,
            usageDetail: usageDetail,
            onScroll: onScroll,
            handleSend: handleSend,
            queueCount: queueCount,
            cancelQueue: cancelQueue,
            handleCancel: handleCancel,
            handleUndo: handleUndo,
            handleRestore: handleRestore,
            handleMessageDelete: handleMessageDelete,
            pendingCount: pendingCount,
            handleRemoveQueued: handleRemoveQueued,
            speakingId: speakingId,
            handleSpeak: handleSpeak,
            handleRetry: handleRetry,
            copiedId: copiedId,
            handleCopy: handleCopy,
            switchConversation: switchConversation,
            newConversation: newConversation,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
