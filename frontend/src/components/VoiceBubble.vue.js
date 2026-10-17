/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
// 语音消息气泡：波形 + 时长 + 点击播放/暂停（微信式）。
// 全局单例 <audio>，多气泡共享同一播放，点新气泡停旧的。
import { onBeforeUnmount, ref } from 'vue';
import { voiceAudioUrl } from '../api/voice';
import { fetchWithTimeout } from '../api/fetch';
const props = defineProps();
let sharedAudio = null;
let loadUrl = '';
let blobUrl = '';
const playing = ref(false);
const progress = ref(0);
function resolveUrl() {
    const u = props.voice?.url || '';
    if (!u)
        return '';
    if (u.startsWith('/api/voice/audio/'))
        return u;
    if (u.includes('/'))
        return u;
    return voiceAudioUrl(u);
}
async function toggle() {
    const url = resolveUrl();
    if (!url)
        return;
    const audio = sharedAudio;
    // 同一段已在此气泡播放 → 暂停复位
    if (audio && loadUrl === url && playing.value) {
        audio.pause();
        playing.value = false;
        progress.value = 0;
        return;
    }
    // 停掉其它/旧播放
    if (audio) {
        audio.pause();
        audio.currentTime = 0;
    }
    // 释放旧 blob URL
    if (blobUrl) {
        URL.revokeObjectURL(blobUrl);
        blobUrl = '';
    }
    try {
        // 带鉴权头获取音频 blob（new Audio(url) 不带 auth → 401）
        const res = await fetchWithTimeout(url, {}, 30000);
        if (!res.ok) {
            playing.value = false;
            progress.value = 0;
            return;
        }
        const blob = await res.blob();
        blobUrl = URL.createObjectURL(blob);
        const a = new Audio(blobUrl);
        sharedAudio = a;
        loadUrl = url;
        playing.value = true;
        a.ontimeupdate = () => {
            if (a.duration)
                progress.value = a.currentTime / a.duration;
        };
        a.onended = () => {
            playing.value = false;
            progress.value = 0;
        };
        a.onerror = () => {
            playing.value = false;
            progress.value = 0;
        };
        void a.play().catch(() => {
            playing.value = false;
            progress.value = 0;
        });
    }
    catch {
        playing.value = false;
        progress.value = 0;
    }
}
onBeforeUnmount(() => {
    // 仅当本气泡仍持有共享播放时停止，避免误停其它气泡
    if (sharedAudio && playing.value) {
        sharedAudio.pause();
        sharedAudio.currentTime = 0;
    }
    if (blobUrl) {
        URL.revokeObjectURL(blobUrl);
        blobUrl = '';
    }
});
const bars = computedBars();
function computedBars() {
    const w = props.voice?.waveform;
    if (w && w.length)
        return w;
    // 无波形时按时长生成占位
    const n = Math.max(8, Math.min(32, Math.round((props.voice?.duration || 2) * 2)));
    return Array.from({ length: n }, (_, i) => 0.4 + 0.5 * Math.abs(Math.sin(i * 1.7)));
}
const durText = (d) => `${Math.max(1, Math.round(d))}″`;
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['voice-bubble']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-bubble']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['playing']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-bar']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.toggle) },
    ...{ class: "voice-bubble" },
    ...{ class: ({ playing: __VLS_ctx.playing }) },
    'aria-label': ((__VLS_ctx.playing ? '暂停' : '播放') + '，语音时长 ' + __VLS_ctx.durText(__VLS_ctx.voice.duration) + '秒'),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "voice-icon" },
    ...{ class: ({ playing: __VLS_ctx.playing }) },
});
(__VLS_ctx.playing ? '▐▌' : '▶');
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "voice-wave" },
    'aria-hidden': "true",
});
for (const [b, i] of __VLS_getVForSourceType((__VLS_ctx.bars))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        key: (i),
        ...{ class: "voice-bar" },
        ...{ class: ({ lit: __VLS_ctx.progress > 0 && i / __VLS_ctx.bars.length <= __VLS_ctx.progress }) },
        ...{ style: ({ height: Math.round(20 + b * 60) + '%' }) },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "voice-dur" },
});
(__VLS_ctx.durText(__VLS_ctx.voice.duration));
/** @type {__VLS_StyleScopedClasses['voice-bubble']} */ ;
/** @type {__VLS_StyleScopedClasses['playing']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['playing']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-wave']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['lit']} */ ;
/** @type {__VLS_StyleScopedClasses['voice-dur']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            playing: playing,
            progress: progress,
            toggle: toggle,
            bars: bars,
            durText: durText,
        };
    },
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
