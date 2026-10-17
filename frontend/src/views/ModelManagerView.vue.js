/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useModelManagerStore } from '../stores/modelManager';
const mm = useModelManagerStore();
const defaultSel = ref('');
const smallSel = ref('');
const imageSel = ref('');
const voiceSel = ref('');
// [模型管理] 语音合成模型规格（Qwen3-TTS 尺寸）
const VOICE_SIZE_OPTIONS = ['0.6B', '1.7B'];
onMounted(async () => {
    await mm.load();
    syncSelectors();
});
function syncSelectors() {
    defaultSel.value = mm.config?.default_model || '';
    smallSel.value = mm.config?.small_model || '';
    imageSel.value = mm.config?.image_caption_model || '';
    voiceSel.value = mm.config?.voice_model_size || '';
}
const modelList = computed(() => mm.config?.models || []);
// ── 模型列表：名称/ID 检索 + 分页 ──
const modelQuery = ref('');
const page = ref(1);
const pageSize = ref(10);
const filteredModels = computed(() => {
    const q = modelQuery.value.trim().toLowerCase();
    if (!q)
        return modelList.value;
    return modelList.value.filter(m => (m.name || '').toLowerCase().includes(q) || m.id.toLowerCase().includes(q));
});
const totalPages = computed(() => Math.max(1, Math.ceil(filteredModels.value.length / pageSize.value)));
const pagedModels = computed(() => {
    const start = (page.value - 1) * pageSize.value;
    return filteredModels.value.slice(start, start + pageSize.value);
});
watch(modelQuery, () => { page.value = 1; });
watch(pageSize, () => { page.value = 1; });
watch(totalPages, (tp) => { if (page.value > tp)
    page.value = tp; });
function gotoPage(n) { page.value = Math.min(Math.max(1, n), totalPages.value); }
// ── 默认/轻量模型 ──
const defaultsSaving = ref(false);
async function saveDefaults() {
    defaultsSaving.value = true;
    await mm.setDefaults(defaultSel.value || null, smallSel.value || null, imageSel.value || null, voiceSel.value || null);
    defaultsSaving.value = false;
    syncSelectors();
}
// ── Provider 编辑 ──
const showProviderForm = ref(false);
// 正在编辑已有 Provider（而非新增）：此时前缀不可改
const providerIsEdit = ref(false);
// 「自定义模型前缀」高级项：默认收起，留空则按 Label / api_base 自动生成
const providerAdvanced = ref(false);
const providerForm = reactive({
    name: '', label: '', api_base: '', api_key: '', enabled: true,
});
// 生成 litellm 前缀的 slug（只保留 ASCII 字母数字，其余折叠为 '-'）
function _slug(s) {
    return s.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}
// 前缀留空时的自动推导：优先 Label，其次 api_base 主机名，最后兜底 custom
function deriveProviderName() {
    const fromLabel = _slug(providerForm.label);
    if (fromLabel)
        return fromLabel;
    const raw = providerForm.api_base.trim();
    try {
        const u = new URL(/^https?:\/\//i.test(raw) ? raw : `http://${raw}`);
        const host = _slug((u.hostname || '').replace(/\./g, '-'));
        if (host)
            return `prov-${host}${u.port ? '-' + u.port : ''}`;
    }
    catch { /* 解析失败按兜底 */ }
    return 'custom';
}
function openProviderForm(p) {
    providerIsEdit.value = !!p;
    providerAdvanced.value = !!p; // 编辑时始终展示前缀（只读），便于看到模型 id 的前半段
    providerForm.name = p?.provider || '';
    providerForm.label = p?.label || '';
    providerForm.api_base = p?.api_base || '';
    providerForm.api_key = p?.api_key || '';
    providerForm.enabled = p?.enabled ?? true;
    showProviderForm.value = true;
}
async function submitProvider() {
    if (!providerForm.api_base.trim()) {
        mm.setError('请填写 api_base（OpenAI 兼容服务地址，如 http://127.0.0.1:8001/v1）');
        return;
    }
    // 新增时前缀留空 → 自动生成；编辑时不可改
    const name = (providerIsEdit.value
        ? providerForm.name
        : (providerForm.name.trim() || deriveProviderName())).trim();
    if (!name) {
        mm.setError('无法自动生成前缀，请展开「高级：自定义模型前缀」填写（如 vllm）');
        return;
    }
    if (name.includes('/')) {
        // Provider 名称是 litellm 前缀，不含 '/'；模型 id 形如 deepseek/deepseek-v4-flash
        mm.setError(`Provider 名称不能包含「/」，请只填前缀（如想让「deepseek/deepseek-v4-flash」可用，名称填 deepseek）`);
        return;
    }
    // 仅在保存成功时关闭弹窗：失败时保留用户输入，否则错误 toast 容易被错过
    const ok = await mm.updateProvider(name, { label: providerForm.label, api_base: providerForm.api_base, api_key: providerForm.api_key, enabled: providerForm.enabled });
    if (ok)
        showProviderForm.value = false;
}
async function toggleProvider(p) {
    await mm.updateProvider(p.provider, { enabled: !p.enabled });
}
async function delProvider(p) {
    if (!window.confirm(`删除 Provider「${p.label || p.provider}」？其自动注册的模型会一并消失。`))
        return;
    await mm.removeProvider(p.provider);
}
// ── 自定义模型编辑 ──
const showModelForm = ref(false);
const modelForm = reactive({
    id: '', name: '', provider: 'ollama', family: '', description: '',
    context_length: 32768, max_output_tokens: 8192,
    input_price: 0, output_price: 0, cache_read_price: 0, cache_write_price: 0,
    tool_use: true, vision: false, reasoning: false, thinking: '',
});
function openModelForm(m) {
    modelForm.id = m?.id || '';
    modelForm.name = m?.name || '';
    modelForm.provider = m?.provider || 'ollama';
    modelForm.family = m?.family || m?.id?.split('/')[1] || '';
    modelForm.description = m?.description || '';
    modelForm.context_length = m?.context_length || 32768;
    modelForm.max_output_tokens = m?.limits?.max_output_tokens || 8192;
    modelForm.input_price = m?.cost?.input_per_1m ?? 0;
    modelForm.output_price = m?.cost?.output_per_1m ?? 0;
    modelForm.cache_read_price = m?.cost?.cache_read_per_1m ?? 0;
    modelForm.cache_write_price = m?.cost?.cache_write_per_1m ?? 0;
    modelForm.tool_use = m?.capabilities?.tool_use ?? true;
    modelForm.vision = m?.capabilities?.vision ?? false;
    modelForm.reasoning = m?.capabilities?.reasoning ?? false;
    modelForm.thinking = m?.thinking || '';
    showModelForm.value = true;
}
async function submitModel() {
    if (!modelForm.id.trim())
        return;
    const entry = {
        id: modelForm.id.trim(),
        name: modelForm.name.trim() || modelForm.id.trim(),
        provider: modelForm.provider.trim() || (modelForm.id.split('/')[0] || ''),
        family: modelForm.family.trim() || modelForm.id.split('/')[1] || '',
        description: modelForm.description,
        context_length: Number(modelForm.context_length) || 32768,
        limits: { max_output_tokens: Number(modelForm.max_output_tokens) || 8192 },
        capabilities: { tool_use: modelForm.tool_use, vision: modelForm.vision, reasoning: modelForm.reasoning },
        thinking: modelForm.thinking || undefined,
        cost: {
            input_per_1m: Number(modelForm.input_price) || 0,
            output_per_1m: Number(modelForm.output_price) || 0,
            cache_read_per_1m: Number(modelForm.cache_read_price) || 0,
            cache_write_per_1m: Number(modelForm.cache_write_price) || 0,
        },
    };
    // 仅在保存成功时关闭弹窗：失败时保留用户输入，否则错误 toast 容易被错过
    const ok = await mm.addOrUpdateModel(entry);
    if (ok)
        showModelForm.value = false;
}
async function delModel(m) {
    if (!window.confirm(`删除模型「${m.name || m.id}」？`))
        return;
    await mm.removeModel(m.id);
}
function fmtCost(v) {
    if (!v)
        return '免费';
    return `$${v} /1M`;
}
function capOf(m) {
    const c = m.capabilities || {};
    const think = m.thinking === 'enabled' ? '思考' : '';
    return [c.tool_use && '工具', c.vision && '视觉', c.reasoning && '推理', think].filter(Boolean).join('·') || '—';
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['mm-block']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-adv-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-save-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-th']} */ ;
/** @type {__VLS_StyleScopedClasses['provider-table']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-tr']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-th']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-tr']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-tr']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-search']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-search']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pagesize']} */ ;
/** @type {__VLS_StyleScopedClasses['dialog']} */ ;
/** @type {__VLS_StyleScopedClasses['switch']} */ ;
/** @type {__VLS_StyleScopedClasses['slider']} */ ;
/** @type {__VLS_StyleScopedClasses['switch']} */ ;
/** @type {__VLS_StyleScopedClasses['slider']} */ ;
/** @type {__VLS_StyleScopedClasses['switch']} */ ;
/** @type {__VLS_StyleScopedClasses['slider']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
(__VLS_ctx.mm.config?.source_path || 'data/model_catalog.db');
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-content" },
});
const __VLS_0 = {}.Transition;
/** @type {[typeof __VLS_components.Transition, typeof __VLS_components.Transition, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    name: "toast",
}));
const __VLS_2 = __VLS_1({
    name: "toast",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
if (__VLS_ctx.mm.notice) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-toast mm-toast--ok" },
    });
    (__VLS_ctx.mm.notice);
}
var __VLS_3;
const __VLS_4 = {}.Transition;
/** @type {[typeof __VLS_components.Transition, typeof __VLS_components.Transition, ]} */ ;
// @ts-ignore
const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
    name: "toast",
}));
const __VLS_6 = __VLS_5({
    name: "toast",
}, ...__VLS_functionalComponentArgsRest(__VLS_5));
__VLS_7.slots.default;
if (__VLS_ctx.mm.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-toast mm-toast--err" },
    });
    (__VLS_ctx.mm.error);
}
var __VLS_7;
if (__VLS_ctx.mm.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "loading-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "card mm-block" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-defaults-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-pill-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill-caption" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-icon" },
        width: "13",
        height: "13",
        viewBox: "0 0 24 24",
        fill: "currentColor",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
        value: (__VLS_ctx.defaultSel),
        ...{ class: "mm-pill-select" },
    });
    for (const [m] of __VLS_getVForSourceType((__VLS_ctx.modelList))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            key: (m.id),
            value: (m.id),
        });
        (m.name || m.id);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-chevron" },
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2.5",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
        points: "6 9 12 15 18 9",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-pill-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill-caption" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-icon mm-pill-icon-two" },
        width: "13",
        height: "13",
        viewBox: "0 0 24 24",
        fill: "currentColor",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
        value: (__VLS_ctx.smallSel),
        ...{ class: "mm-pill-select" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "",
    });
    for (const [m] of __VLS_getVForSourceType((__VLS_ctx.modelList))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            key: (m.id),
            value: (m.id),
        });
        (m.name || m.id);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-chevron" },
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2.5",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
        points: "6 9 12 15 18 9",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-pill-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill-caption" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-icon" },
        width: "13",
        height: "13",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.rect)({
        x: "3",
        y: "5",
        width: "18",
        height: "14",
        rx: "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.circle)({
        cx: "9",
        cy: "10",
        r: "1.6",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M4.5 18.5l5-5 2.8 2.8 3.2-3.2 4 4.4",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
        value: (__VLS_ctx.imageSel),
        ...{ class: "mm-pill-select" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "",
    });
    for (const [m] of __VLS_getVForSourceType((__VLS_ctx.modelList))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            key: (m.id),
            value: (m.id),
        });
        (m.name || m.id);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-chevron" },
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2.5",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
        points: "6 9 12 15 18 9",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-pill-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill-caption" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-pill" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-icon mm-pill-icon-two" },
        width: "13",
        height: "13",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.rect)({
        x: "9",
        y: "2",
        width: "6",
        height: "12",
        rx: "3",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M5 10a7 7 0 0 0 14 0",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M12 17v4",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M8 21h8",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
        value: (__VLS_ctx.voiceSel),
        ...{ class: "mm-pill-select" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "",
    });
    for (const [sz] of __VLS_getVForSourceType((__VLS_ctx.VOICE_SIZE_OPTIONS))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            key: (sz),
            value: (sz),
        });
        (sz);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        ...{ class: "mm-pill-chevron" },
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2.5",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
        points: "6 9 12 15 18 9",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-defaults-save" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.saveDefaults) },
        ...{ class: "btn btn-primary mm-save-btn" },
        disabled: (__VLS_ctx.mm.saving || __VLS_ctx.defaultsSaving),
    });
    if (__VLS_ctx.defaultsSaving) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner-mm" },
        });
    }
    (__VLS_ctx.defaultsSaving ? '保存中…' : '保存');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "hint mm-defaults-hint" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "card mm-block" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-title" },
    });
    if (__VLS_ctx.mm.refreshing) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner-mm mm-refresh-spin" },
            title: "刷新中",
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!!(__VLS_ctx.mm.loading))
                    return;
                __VLS_ctx.openProviderForm();
            } },
        ...{ class: "btn btn-primary mm-title-action" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "hint" },
    });
    if (!__VLS_ctx.mm.config?.providers?.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "empty-tip" },
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-table provider-table" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-th" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-ops" },
    });
    for (const [p] of __VLS_getVForSourceType((__VLS_ctx.mm.config?.providers))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (p.provider),
            ...{ class: "mm-tr" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-main" },
        });
        (p.label || p.provider);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-mono" },
        });
        (p.api_base);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (p.models?.length ?? 0);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
            ...{ class: "switch" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
            ...{ onChange: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    __VLS_ctx.toggleProvider(p);
                } },
            type: "checkbox",
            checked: (p.enabled),
            disabled: (!!__VLS_ctx.mm.busyId),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "slider" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-ops" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    __VLS_ctx.openProviderForm(p);
                } },
            ...{ class: "btn btn-sm" },
            disabled: (__VLS_ctx.mm.busyId === p.provider),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    __VLS_ctx.delProvider(p);
                } },
            ...{ class: "btn btn-sm btn-danger" },
            disabled: (__VLS_ctx.mm.busyId === p.provider || !!__VLS_ctx.mm.busyId),
        });
        if (__VLS_ctx.mm.busyId === p.provider) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "spinner-mm" },
            });
        }
        (__VLS_ctx.mm.busyId === p.provider ? '删除中…' : '删除');
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.section, __VLS_intrinsicElements.section)({
        ...{ class: "card mm-block" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-title" },
    });
    if (__VLS_ctx.mm.refreshing) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner-mm mm-refresh-spin" },
            title: "刷新中",
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!!(__VLS_ctx.mm.loading))
                    return;
                __VLS_ctx.openModelForm();
            } },
        ...{ class: "btn btn-primary mm-title-action" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "hint" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-list-toolbar" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-search" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "14",
        height: "14",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.circle)({
        cx: "11",
        cy: "11",
        r: "8",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M21 21l-4.35-4.35",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        placeholder: "按名称 / ID 检索模型…",
    });
    (__VLS_ctx.modelQuery);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-count" },
    });
    (__VLS_ctx.filteredModels.length);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-table" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "mm-th" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-num" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-num" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mm-ops" },
    });
    for (const [m] of __VLS_getVForSourceType((__VLS_ctx.pagedModels))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (m.id),
            ...{ class: "mm-tr" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-main" },
        });
        (m.name || m.id);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-mono mm-id" },
        });
        (m.id);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-num" },
        });
        (m.context_length.toLocaleString());
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-num" },
        });
        (__VLS_ctx.fmtCost(m.cost?.input_per_1m));
        (__VLS_ctx.fmtCost(m.cost?.output_per_1m));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-cap" },
        });
        (__VLS_ctx.capOf(m));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-ops" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    __VLS_ctx.openModelForm(m);
                } },
            ...{ class: "btn btn-sm" },
            disabled: (__VLS_ctx.mm.busyId === m.id || !!__VLS_ctx.mm.busyId),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    __VLS_ctx.delModel(m);
                } },
            ...{ class: "btn btn-sm btn-danger" },
            disabled: (__VLS_ctx.mm.busyId === m.id || !!__VLS_ctx.mm.busyId),
        });
        if (__VLS_ctx.mm.busyId === m.id) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "spinner-mm" },
            });
        }
        (__VLS_ctx.mm.busyId === m.id ? '删除中…' : '删除');
    }
    if (!__VLS_ctx.filteredModels.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "empty-tip" },
        });
        (__VLS_ctx.modelQuery ? '未找到匹配的模型' : '暂无模型');
    }
    if (__VLS_ctx.filteredModels.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "mm-pager" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mm-pager-info" },
        });
        (__VLS_ctx.page);
        (__VLS_ctx.totalPages);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
            ...{ class: "mm-pagesize" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
            value: (__VLS_ctx.pageSize),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            value: (10),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            value: (20),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
            value: (50),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    if (!(__VLS_ctx.filteredModels.length))
                        return;
                    __VLS_ctx.gotoPage(__VLS_ctx.page - 1);
                } },
            ...{ class: "btn btn-sm" },
            disabled: (__VLS_ctx.page <= 1),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.mm.loading))
                        return;
                    if (!(__VLS_ctx.filteredModels.length))
                        return;
                    __VLS_ctx.gotoPage(__VLS_ctx.page + 1);
                } },
            ...{ class: "btn btn-sm" },
            disabled: (__VLS_ctx.page >= __VLS_ctx.totalPages),
        });
    }
}
if (__VLS_ctx.showProviderForm) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "overlay" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "dialog" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-title" },
    });
    (__VLS_ctx.providerIsEdit ? '编辑 Provider' : '新增 Provider');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field mm-wide" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "本地 vLLM",
    });
    (__VLS_ctx.providerForm.label);
    if (__VLS_ctx.providerIsEdit || __VLS_ctx.providerAdvanced) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "form-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
            ...{ class: "mm-field mm-wide" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        if (__VLS_ctx.providerIsEdit) {
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
            ...{ class: "ctrl" },
            placeholder: (__VLS_ctx.deriveProviderName()),
            disabled: (__VLS_ctx.providerIsEdit),
        });
        (__VLS_ctx.providerForm.name);
    }
    if (!__VLS_ctx.providerIsEdit) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "form-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.showProviderForm))
                        return;
                    if (!(!__VLS_ctx.providerIsEdit))
                        return;
                    __VLS_ctx.providerAdvanced = !__VLS_ctx.providerAdvanced;
                } },
            type: "button",
            ...{ class: "mm-adv-toggle" },
        });
        (__VLS_ctx.providerAdvanced ? '收起自定义前缀' : '高级：自定义模型前缀');
        if (!__VLS_ctx.providerAdvanced) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "mm-adv-current" },
            });
            (__VLS_ctx.deriveProviderName());
        }
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "hint provider-prefix-hint" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field mm-wide" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "http://127.0.0.1:8001/v1",
    });
    (__VLS_ctx.providerForm.api_base);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field mm-wide" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "password",
        placeholder: "sk-...",
    });
    (__VLS_ctx.providerForm.api_key);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-footer" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.providerForm.enabled);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.showProviderForm))
                    return;
                __VLS_ctx.showProviderForm = false;
            } },
        ...{ class: "btn" },
        disabled: (__VLS_ctx.mm.saving),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.submitProvider) },
        ...{ class: "btn btn-primary mm-save-btn" },
        disabled: (__VLS_ctx.mm.saving),
    });
    if (__VLS_ctx.mm.saving) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner-mm" },
        });
    }
    (__VLS_ctx.mm.saving ? '保存中…' : '保存');
}
if (__VLS_ctx.showModelForm) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "overlay" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "dialog wide" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-title" },
    });
    (__VLS_ctx.modelForm.id && __VLS_ctx.modelList.find(m => m.id === __VLS_ctx.modelForm.id) ? '编辑模型' : '新增自定义模型');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "ollama/qwen2.5-coder:latest",
    });
    (__VLS_ctx.modelForm.id);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "Ollama Qwen2.5-Coder",
    });
    (__VLS_ctx.modelForm.name);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "ollama / deepseek / openai / 自定义",
        list: "provider-list",
    });
    (__VLS_ctx.modelForm.provider);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.datalist, __VLS_intrinsicElements.datalist)({
        id: "provider-list",
    });
    for (const [p] of __VLS_getVForSourceType((__VLS_ctx.mm.config?.providers))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.option)({
            key: (p.provider),
            value: (p.provider),
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option)({
        value: "ollama",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option)({
        value: "deepseek",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option)({
        value: "openai",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "number",
        min: "1024",
    });
    (__VLS_ctx.modelForm.context_length);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "number",
        min: "256",
    });
    (__VLS_ctx.modelForm.max_output_tokens);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field mm-wide" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "用途与能力说明",
    });
    (__VLS_ctx.modelForm.description);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-title small" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "number",
        min: "0",
        step: "0.0001",
    });
    (__VLS_ctx.modelForm.input_price);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "number",
        min: "0",
        step: "0.0001",
    });
    (__VLS_ctx.modelForm.output_price);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "number",
        min: "0",
        step: "0.0001",
    });
    (__VLS_ctx.modelForm.cache_read_price);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        type: "number",
        min: "0",
        step: "0.0001",
    });
    (__VLS_ctx.modelForm.cache_write_price);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-check" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.modelForm.tool_use);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-check" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.modelForm.vision);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-check" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.modelForm.reasoning);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "mm-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
        value: (__VLS_ctx.modelForm.thinking),
        ...{ class: "ctrl" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "disabled",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "enabled",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        value: "adaptive",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "hint" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-footer" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.showModelForm))
                    return;
                __VLS_ctx.showModelForm = false;
            } },
        ...{ class: "btn" },
        disabled: (__VLS_ctx.mm.saving),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.submitModel) },
        ...{ class: "btn btn-primary mm-save-btn" },
        disabled: (__VLS_ctx.mm.saving),
    });
    if (__VLS_ctx.mm.saving) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner-mm" },
        });
    }
    (__VLS_ctx.mm.saving ? '保存中…' : '保存');
}
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-toast']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-toast--ok']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-toast']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-toast--err']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-block']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-defaults-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-caption']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-select']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-caption']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-icon-two']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-select']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-caption']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-select']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-caption']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-icon-two']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-select']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pill-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-defaults-save']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-save-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-defaults-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-block']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-refresh-spin']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-title-action']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-table']} */ ;
/** @type {__VLS_StyleScopedClasses['provider-table']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-th']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-ops']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-tr']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-main']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-mono']} */ ;
/** @type {__VLS_StyleScopedClasses['switch']} */ ;
/** @type {__VLS_StyleScopedClasses['slider']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-ops']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-block']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-refresh-spin']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-title-action']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-list-toolbar']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-search']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-count']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-table']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-th']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-num']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-num']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-ops']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-tr']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-main']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-mono']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-id']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-num']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-num']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-cap']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-ops']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pager']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pager-info']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-pagesize']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['dialog']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-wide']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-wide']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-adv-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-adv-current']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['provider-prefix-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-wide']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-wide']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-save-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
/** @type {__VLS_StyleScopedClasses['overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['dialog']} */ ;
/** @type {__VLS_StyleScopedClasses['wide']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-wide']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['small']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-check']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-check']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-check']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-field']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['form-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['mm-save-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner-mm']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            mm: mm,
            defaultSel: defaultSel,
            smallSel: smallSel,
            imageSel: imageSel,
            voiceSel: voiceSel,
            VOICE_SIZE_OPTIONS: VOICE_SIZE_OPTIONS,
            modelList: modelList,
            modelQuery: modelQuery,
            page: page,
            pageSize: pageSize,
            filteredModels: filteredModels,
            totalPages: totalPages,
            pagedModels: pagedModels,
            gotoPage: gotoPage,
            defaultsSaving: defaultsSaving,
            saveDefaults: saveDefaults,
            showProviderForm: showProviderForm,
            providerIsEdit: providerIsEdit,
            providerAdvanced: providerAdvanced,
            providerForm: providerForm,
            deriveProviderName: deriveProviderName,
            openProviderForm: openProviderForm,
            submitProvider: submitProvider,
            toggleProvider: toggleProvider,
            delProvider: delProvider,
            showModelForm: showModelForm,
            modelForm: modelForm,
            openModelForm: openModelForm,
            submitModel: submitModel,
            delModel: delModel,
            fmtCost: fmtCost,
            capOf: capOf,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
