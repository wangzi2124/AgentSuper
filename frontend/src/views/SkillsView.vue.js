/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, onMounted, ref } from 'vue';
import { useSkillStore } from '../stores/skills';
import DirPickerModal from '../components/DirPickerModal.vue';
const skillStore = useSkillStore();
const showDirPicker = ref(false);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const search = ref('');
// ── 新建 / 编辑弹窗 ──
const editor = ref(false);
const editing = ref(null); // null = 新建
const form = ref({ name: '', description: '', content: '', enabled: true, disable_model_invocation: false });
const saving = ref(false);
const confirmDelete = ref(null);
const filtered = computed(() => {
    const q = search.value.toLowerCase().trim();
    if (!q)
        return skillStore.skills;
    return skillStore.skills.filter((s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q));
});
const enabledCount = computed(() => skillStore.skills.filter((s) => s.enabled).length);
/** 可挂载为 load_skill_* 工具的数量（仅手动 的技能不算） */
const toolCount = computed(() => skillStore.skills.filter((s) => s.enabled && !s.disable_model_invocation).length);
const managedCount = computed(() => skillStore.skills.filter((s) => s.managed).length);
/** 随系统发布的内置技能（后端 app/skills/bundled 播种进受管库） */
const bundledCount = computed(() => skillStore.skills.filter((s) => s.bundled).length);
/** 技能挂载为 load_skill_<name> 工具（后端 app/agent/tools.py:create_skill_tools 同款命名） */
function toolNameOf(name) {
    return 'load_skill_' + name.replace(/-/g, '_').replace(/ /g, '_');
}
function isManaged(s) {
    return s.managed !== false;
}
async function reload() {
    busy.value = true;
    error.value = '';
    try {
        await skillStore.refresh();
    }
    catch (err) {
        error.value = err?.message || '加载技能失败';
    }
    finally {
        busy.value = false;
    }
}
onMounted(() => {
    if (!skillStore.initialized)
        void reload();
});
// ── 外部技能源 ──
async function handleSelectDir(path) {
    showDirPicker.value = false;
    busy.value = true;
    error.value = '';
    try {
        await skillStore.addDirectory(path);
        notice.value = '已追加外部技能源（受管库中的技能不受影响）';
    }
    catch (err) {
        error.value = err?.message || '添加技能目录失败';
    }
    finally {
        busy.value = false;
    }
}
async function handleRemoveDir(path) {
    busy.value = true;
    error.value = '';
    try {
        await skillStore.removeDirectory(path);
        notice.value = '已移除该外部技能源';
    }
    catch (err) {
        error.value = err?.message || '移除技能目录失败';
    }
    finally {
        busy.value = false;
    }
}
// ── 启停 ──
async function handleToggle(skill) {
    error.value = '';
    try {
        await skillStore.toggle(skill.name, !skill.enabled);
    }
    catch (err) {
        error.value = err?.message || '切换技能失败';
    }
}
// ── 新建 / 编辑 / 删除 ──
function openCreate() {
    editing.value = null;
    form.value = { name: '', description: '', content: '', enabled: true, disable_model_invocation: false };
    editor.value = true;
}
async function openEdit(skill) {
    error.value = '';
    editor.value = true;
    editing.value = skill.name;
    form.value = {
        name: skill.name,
        description: skill.description || '',
        content: '',
        enabled: skill.enabled,
        disable_model_invocation: skill.disable_model_invocation,
    };
    try {
        const d = await skillStore.detail(skill.name);
        form.value.content = d.content || '';
    }
    catch (err) {
        error.value = err?.message || '读取技能内容失败';
        editor.value = false;
    }
}
async function submitForm() {
    const f = form.value;
    if (!f.name.trim()) {
        error.value = '技能名不能为空';
        return;
    }
    saving.value = true;
    error.value = '';
    try {
        if (editing.value) {
            await skillStore.update(editing.value, {
                description: f.description,
                content: f.content,
                enabled: f.enabled,
                disable_model_invocation: f.disable_model_invocation,
            });
            notice.value = `已保存「${editing.value}」`;
        }
        else {
            await skillStore.create({
                name: f.name.trim(),
                description: f.description,
                content: f.content,
                enabled: f.enabled,
                disable_model_invocation: f.disable_model_invocation,
            });
            notice.value = `已创建「${f.name.trim()}」并挂载为 ${toolNameOf(f.name.trim())}`;
        }
        editor.value = false;
    }
    catch (err) {
        error.value = err?.message || (editing.value ? '保存技能失败' : '创建技能失败');
    }
    finally {
        saving.value = false;
    }
}
async function handleDelete() {
    const name = confirmDelete.value;
    if (!name)
        return;
    saving.value = true;
    error.value = '';
    try {
        await skillStore.remove(name);
        notice.value = `已删除「${name}」`;
    }
    catch (err) {
        error.value = err?.message || '删除技能失败';
    }
    finally {
        saving.value = false;
        confirmDelete.value = null;
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-card']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-head']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-x']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-body']} */ ;
/** @type {__VLS_StyleScopedClasses['field-label']} */ ;
/** @type {__VLS_StyleScopedClasses['field-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['chk']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['search']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-card']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['modal']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-content skills-wrap" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card stat-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-num" },
});
(__VLS_ctx.skillStore.skills.length);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-num" },
});
(__VLS_ctx.enabledCount);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-num" },
});
(__VLS_ctx.toolCount);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-num" },
});
(__VLS_ctx.managedCount);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-num" },
});
(__VLS_ctx.bundledCount);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "stat-actions" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ class: "ctrl search" },
    placeholder: "搜索技能…",
});
(__VLS_ctx.search);
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.reload) },
    ...{ class: "btn" },
    disabled: (__VLS_ctx.busy || __VLS_ctx.skillStore.loading),
});
(__VLS_ctx.busy || __VLS_ctx.skillStore.loading ? '加载中…' : '重新扫描');
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.openCreate) },
    ...{ class: "btn btn-primary" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card src-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "src-head" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "src-title" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "src-hint" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
(__VLS_ctx.bundledCount);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "src-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "tag tag-managed" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "src-path" },
    title: (__VLS_ctx.skillStore.managedDir),
});
(__VLS_ctx.skillStore.managedDir || '加载中…');
for (const [d] of __VLS_getVForSourceType((__VLS_ctx.skillStore.extraDirs))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        key: (d),
        ...{ class: "src-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "tag tag-ext" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "src-path" },
        title: (d),
    });
    (d);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.handleRemoveDir(d);
            } },
        ...{ class: "btn btn-sm btn-danger" },
        disabled: (__VLS_ctx.busy),
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "src-actions" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.showDirPicker = true;
        } },
    ...{ class: "btn" },
    disabled: (__VLS_ctx.busy),
});
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "msg msg-error" },
    });
    (__VLS_ctx.error);
}
if (__VLS_ctx.notice && !__VLS_ctx.error) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "msg msg-ok" },
    });
    (__VLS_ctx.notice);
}
if (__VLS_ctx.skillStore.loading && !__VLS_ctx.skillStore.skills.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "loading-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
else if (!__VLS_ctx.skillStore.skills.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-state" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "empty-sub" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.openCreate) },
        ...{ class: "btn btn-primary" },
    });
}
else if (!__VLS_ctx.filtered.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-state" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    (__VLS_ctx.search);
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "skill-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "list-meta" },
    });
    (__VLS_ctx.filtered.length);
    (__VLS_ctx.skillStore.skills.length);
    for (const [s] of __VLS_getVForSourceType((__VLS_ctx.filtered))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (s.name),
            ...{ class: "card skill-card" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "skill-icon" },
        });
        (s.disable_model_invocation ? '🔒' : '⚡');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "skill-info" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "skill-name" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "skill-title" },
        });
        (s.name);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge mono" },
        });
        (__VLS_ctx.toolNameOf(s.name));
        if (s.bundled) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge builtin" },
                title: "随系统发布的内置技能（可编辑/删除，删掉不会自动恢复）",
            });
        }
        if (s.disable_model_invocation) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge warn" },
                title: "仅由用户显式触发，不暴露为模型工具",
            });
        }
        if (!__VLS_ctx.isManaged(s)) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge" },
                title: "来自外部技能源，只读",
            });
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "skill-desc" },
        });
        (s.description || '（无描述）');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "skill-path" },
            title: (s.path),
        });
        (s.path);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge" },
            ...{ class: (s.enabled ? 'ok' : 'off') },
        });
        (s.enabled ? '已启用' : '已禁用');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "skill-actions" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.skillStore.loading && !__VLS_ctx.skillStore.skills.length))
                        return;
                    if (!!(!__VLS_ctx.skillStore.skills.length))
                        return;
                    if (!!(!__VLS_ctx.filtered.length))
                        return;
                    __VLS_ctx.handleToggle(s);
                } },
            ...{ class: "btn btn-sm" },
            ...{ class: (s.enabled ? 'btn-danger' : 'btn-primary') },
            disabled: (s.disable_model_invocation),
        });
        (s.enabled ? '禁用' : '启用');
        if (__VLS_ctx.isManaged(s)) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.skillStore.loading && !__VLS_ctx.skillStore.skills.length))
                            return;
                        if (!!(!__VLS_ctx.skillStore.skills.length))
                            return;
                        if (!!(!__VLS_ctx.filtered.length))
                            return;
                        if (!(__VLS_ctx.isManaged(s)))
                            return;
                        __VLS_ctx.openEdit(s);
                    } },
                ...{ class: "btn btn-sm" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.skillStore.loading && !__VLS_ctx.skillStore.skills.length))
                            return;
                        if (!!(!__VLS_ctx.skillStore.skills.length))
                            return;
                        if (!!(!__VLS_ctx.filtered.length))
                            return;
                        if (!(__VLS_ctx.isManaged(s)))
                            return;
                        __VLS_ctx.confirmDelete = s.name;
                    } },
                ...{ class: "btn btn-sm btn-danger" },
            });
        }
    }
}
/** @type {[typeof DirPickerModal, ]} */ ;
// @ts-ignore
const __VLS_0 = __VLS_asFunctionalComponent(DirPickerModal, new DirPickerModal({
    ...{ 'onClose': {} },
    ...{ 'onSelect': {} },
    show: (__VLS_ctx.showDirPicker),
}));
const __VLS_1 = __VLS_0({
    ...{ 'onClose': {} },
    ...{ 'onSelect': {} },
    show: (__VLS_ctx.showDirPicker),
}, ...__VLS_functionalComponentArgsRest(__VLS_0));
let __VLS_3;
let __VLS_4;
let __VLS_5;
const __VLS_6 = {
    onClose: (...[$event]) => {
        __VLS_ctx.showDirPicker = false;
    }
};
const __VLS_7 = {
    onSelect: (__VLS_ctx.handleSelectDir)
};
var __VLS_2;
if (__VLS_ctx.editor) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.editor))
                    return;
                __VLS_ctx.editor = false;
            } },
        ...{ class: "modal-mask" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h3, __VLS_intrinsicElements.h3)({});
    (__VLS_ctx.editing ? `编辑技能：${__VLS_ctx.editing}` : '新建技能');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.editor))
                    return;
                __VLS_ctx.editor = false;
            } },
        ...{ class: "modal-x" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal-body" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "field-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.em, __VLS_intrinsicElements.em)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        disabled: (!!__VLS_ctx.editing),
        placeholder: "my-skill（字母/数字/连字符，将生成 load_skill_my_skill 工具）",
    });
    (__VLS_ctx.form.name);
    if (!__VLS_ctx.editing && __VLS_ctx.form.name) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "field-hint" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
        (__VLS_ctx.toolNameOf(__VLS_ctx.form.name));
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "field-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ class: "ctrl" },
        placeholder: "一句话说明这个技能做什么（会进工具 schema，≤200 字符）",
    });
    (__VLS_ctx.form.description);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "field-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.textarea, __VLS_intrinsicElements.textarea)({
        value: (__VLS_ctx.form.content),
        ...{ class: "ctrl mono-area" },
        rows: "14",
        placeholder: "# 技能标题&#10;&#10;模型加载本技能后读到的完整说明书。&#10;写清步骤、约束与产出格式。",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "field-hint" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "field checks" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "chk" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.form.enabled);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "chk" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.form.disable_model_invocation);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    if (__VLS_ctx.error) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "msg msg-error" },
        });
        (__VLS_ctx.error);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal-foot" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.editor))
                    return;
                __VLS_ctx.editor = false;
            } },
        ...{ class: "btn" },
        disabled: (__VLS_ctx.saving),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.submitForm) },
        ...{ class: "btn btn-primary" },
        disabled: (__VLS_ctx.saving),
    });
    (__VLS_ctx.saving ? '保存中…' : __VLS_ctx.editing ? '保存' : '创建');
}
if (__VLS_ctx.confirmDelete) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.confirmDelete))
                    return;
                __VLS_ctx.confirmDelete = null;
            } },
        ...{ class: "modal-mask" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal modal-sm" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h3, __VLS_intrinsicElements.h3)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal-body" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
    (__VLS_ctx.confirmDelete);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "modal-foot" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.confirmDelete))
                    return;
                __VLS_ctx.confirmDelete = null;
            } },
        ...{ class: "btn" },
        disabled: (__VLS_ctx.saving),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.handleDelete) },
        ...{ class: "btn btn-danger" },
        disabled: (__VLS_ctx.saving),
    });
    (__VLS_ctx.saving ? '删除中…' : '删除');
}
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['skills-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['stat']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-num']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-num']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-num']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-num']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-num']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['search']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['src-card']} */ ;
/** @type {__VLS_StyleScopedClasses['src-head']} */ ;
/** @type {__VLS_StyleScopedClasses['src-title']} */ ;
/** @type {__VLS_StyleScopedClasses['src-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['src-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tag']} */ ;
/** @type {__VLS_StyleScopedClasses['tag-managed']} */ ;
/** @type {__VLS_StyleScopedClasses['src-path']} */ ;
/** @type {__VLS_StyleScopedClasses['src-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tag']} */ ;
/** @type {__VLS_StyleScopedClasses['tag-ext']} */ ;
/** @type {__VLS_StyleScopedClasses['src-path']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['src-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['msg']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-error']} */ ;
/** @type {__VLS_StyleScopedClasses['msg']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-ok']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['icon']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-sub']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-list']} */ ;
/** @type {__VLS_StyleScopedClasses['list-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-card']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-info']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-name']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-title']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['builtin']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['warn']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-desc']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-path']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['skill-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-mask']} */ ;
/** @type {__VLS_StyleScopedClasses['modal']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-head']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-x']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-body']} */ ;
/** @type {__VLS_StyleScopedClasses['field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-label']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['field-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-label']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['field']} */ ;
/** @type {__VLS_StyleScopedClasses['field-label']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['mono-area']} */ ;
/** @type {__VLS_StyleScopedClasses['field-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['field']} */ ;
/** @type {__VLS_StyleScopedClasses['checks']} */ ;
/** @type {__VLS_StyleScopedClasses['chk']} */ ;
/** @type {__VLS_StyleScopedClasses['chk']} */ ;
/** @type {__VLS_StyleScopedClasses['msg']} */ ;
/** @type {__VLS_StyleScopedClasses['msg-error']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-foot']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-mask']} */ ;
/** @type {__VLS_StyleScopedClasses['modal']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-head']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-body']} */ ;
/** @type {__VLS_StyleScopedClasses['modal-foot']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            DirPickerModal: DirPickerModal,
            skillStore: skillStore,
            showDirPicker: showDirPicker,
            busy: busy,
            error: error,
            notice: notice,
            search: search,
            editor: editor,
            editing: editing,
            form: form,
            saving: saving,
            confirmDelete: confirmDelete,
            filtered: filtered,
            enabledCount: enabledCount,
            toolCount: toolCount,
            managedCount: managedCount,
            bundledCount: bundledCount,
            toolNameOf: toolNameOf,
            isManaged: isManaged,
            reload: reload,
            handleSelectDir: handleSelectDir,
            handleRemoveDir: handleRemoveDir,
            handleToggle: handleToggle,
            openCreate: openCreate,
            openEdit: openEdit,
            submitForm: submitForm,
            handleDelete: handleDelete,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
