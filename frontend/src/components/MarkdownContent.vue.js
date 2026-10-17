/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, watch } from 'vue';
import { Marked } from 'marked';
import hljs from 'highlight.js/lib/common';
const props = defineProps();
function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
// ChatGPT 风格 Markdown 渲染：GFM + 单换行即换行；代码块带语法高亮 + 语言标签 + 复制按钮；丢弃原始 HTML（防 XSS）
const md = new Marked({
    gfm: true,
    breaks: true,
    async: false,
});
md.use({
    renderer: {
        html() {
            return '';
        },
        code({ text, lang }) {
            const langName = (lang || '').trim().split(/\s+/)[0] || 'text';
            const safeLang = langName.replace(/[^a-zA-Z0-9_+#-]/g, '');
            let body = escapeHtml(text);
            if (safeLang && hljs.getLanguage(safeLang)) {
                try {
                    body = hljs.highlight(String(text), { language: safeLang }).value;
                }
                catch {
                    body = escapeHtml(text);
                }
            }
            return `<div class="md-code">
        <div class="md-code-head">
          <span class="md-code-lang">${escapeHtml(safeLang)}</span>
          <button type="button" class="md-copy" data-copy>复制</button>
        </div>
        <pre class="md-pre"><code class="hljs language-${safeLang}">${body}</code></pre>
      </div>`;
        },
    },
});
const root = ref();
const html = ref('');
// 渲染（流式增量时重新解析，marked 对常见篇幅开销很小）
function render(src) {
    html.value = src ? md.parse(src) : '';
}
watch(() => props.text, (v) => render(v), { immediate: true });
// 复制按钮（事件委托）
function onCopy(e) {
    const btn = e.target.closest('[data-copy]');
    if (!btn || !root.value)
        return;
    const container = btn.closest('.md-code');
    const code = container?.querySelector('.md-pre code');
    if (!code)
        return;
    const text = code.textContent ?? '';
    const done = () => {
        const orig = btn.textContent;
        btn.textContent = '已复制';
        window.setTimeout(() => { btn.textContent = orig; }, 1200);
    };
    if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(done);
    }
    else {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        try {
            document.execCommand('copy');
        }
        catch { /* noop */ }
        document.body.removeChild(ta);
        done();
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ onClick: (__VLS_ctx.onCopy) },
    ref: "root",
    ...{ class: "md" },
});
__VLS_asFunctionalDirective(__VLS_directives.vHtml)(null, { ...__VLS_directiveBindingRestFields, value: (__VLS_ctx.html) }, null, null);
/** @type {typeof __VLS_ctx.root} */ ;
/** @type {__VLS_StyleScopedClasses['md']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            root: root,
            html: html,
            onCopy: onCopy,
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
