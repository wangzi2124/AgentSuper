# -*- coding: utf-8 -*-
"""
upgrade_chat_tablist.py — 移动端聊天框顶部会话标签条 + 设置表单化（幂等升级脚本）

背景：移动端聊天页 = MultiAgentView 套壳 + mobile.css 响应式收敛。
      本轮改动（小步快走，人工执行）：
        A. 移动端隐藏 chat-header 中的文件/权限路径（session-dir 📁 目录等）
        B. 聊天框最上方新增会话标签条 tablist（吸顶在消息列表上方，桌面端不显示）
        C. 左侧抽屉「设置」与「功能导航」样式统一：收敛为单个 drawer-item 整行入口
        D. 点开「设置」弹出 van-popup 表单（模型 / 向量库 / 工作目录 / 清空会话），
           数据直接读写 Pinia store（agent.*），零后端改动

涉及文件：
  frontend/src/views/MultiAgentView.vue   —— 注入 tablist 模板 + 切换/新建会话逻辑
  frontend/src/mobile/MobileShell.vue     —— 设置入口统一为 drawer-item + 表单弹层 + 样式
  frontend/src/styles/mobile.css          —— 追加移动端隐藏路径 / tablist 样式

用法：python scripts/upgrade_chat_tablist.py
幂等：各注入块带独立标记（@@CHAT_TABLIST@@ / @@CHAT_TABLIST_SCRIPT@@ /
      @@CHAT_SETTINGS_ENTRY@@ / @@CHAT_SETTINGS_FORM_TEMPLATE@@ /
      @@CHAT_SETTINGS_FORM_SCRIPT@@ / @@CHAT_SETTINGS_FORM_STYLE@@ /
      @@CHAT_TABLIST_CSS@@），已存在则跳过，可重复执行。
备份：首次修改前自动生成 .bak（沿用 upgrade_chat_panel.py 风格）。
依赖：需先执行过 scripts/upgrade_chat_panel.py（MobileShell 中须存在
      <!-- @@CHAT_PANEL_TEMPLATE@@ --> 设置分组，本脚本将其替换为统一入口）。
"""
import io
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEW = os.path.join(ROOT, 'frontend', 'src', 'views', 'MultiAgentView.vue')
VUE = os.path.join(ROOT, 'frontend', 'src', 'mobile', 'MobileShell.vue')
CSS = os.path.join(ROOT, 'frontend', 'src', 'styles', 'mobile.css')

# ── 标记（幂等） ──
MARK_TABLIST = '@@CHAT_TABLIST@@'
MARK_TABLIST_SCRIPT = '@@CHAT_TABLIST_SCRIPT@@'
MARK_SETTINGS_ENTRY = '@@CHAT_SETTINGS_ENTRY@@'
MARK_FORM_TEMPLATE = '@@CHAT_SETTINGS_FORM_TEMPLATE@@'
MARK_FORM_SCRIPT = '@@CHAT_SETTINGS_FORM_SCRIPT@@'
MARK_FORM_STYLE = '@@CHAT_SETTINGS_FORM_STYLE@@'
MARK_CSS = '@@CHAT_TABLIST_CSS@@'

OLD_GROUP_MARK = '<!-- @@CHAT_PANEL_TEMPLATE@@ -->'

# ── B. MultiAgentView.vue template：聊天框最上方插入会话标签条 ──
TABLIST_TEMPLATE_BLOCK = """
  <!-- @@CHAT_TABLIST@@ -->
  <!-- ── 会话标签条：吸顶在聊天框最上方（移动端展示，桌面端 display:none） ── -->
  <div class="chat-tablist">
    <div
      v-for="c in agent.conversations"
      :key="c.id"
      class="chat-tab"
      :class="{ current: c.id === agent.conversationId }"
      @click="switchConversation(c.id)"
      :title="c.title || '未命名会话'"
    >
      <span class="chat-tab-title">{{ c.title || '未命名会话' }}</span>
    </div>
    <div class="chat-tab chat-tab-new" title="新建会话" @click="newConversation">
      <van-icon name="plus" />
    </div>
  </div>
"""

# ── B. MultiAgentView.vue script：会话标签条逻辑 ──
TABLIST_SCRIPT_BLOCK = """
  /* @@CHAT_TABLIST_SCRIPT@@ */
  // ── 会话标签条：聊天框顶部切换 / 新建会话（数据源 = agent.conversations） ──
  agent.loadConversations()

  function switchConversation(id: string) {
    if (id === agent.conversationId) return
    router.push({ name: 'MultiAgentConversation', params: { id } })
  }

  function newConversation() {
    agent.newChat()
    router.push({ name: 'MultiAgent' })
  }
"""

# ── C. MobileShell.vue template：旧设置分组 → 与「功能导航」统一为单个 drawer-item ──
SETTINGS_ENTRY_BLOCK = """
          <!-- @@CHAT_SETTINGS_ENTRY@@ -->
          <!-- ── 设置：与「功能导航」同款 drawer-item，点开弹出参数表单 ── -->
          <div class="drawer-group-label" style="margin-top: 16px">设置</div>

          <div class="drawer-item settings-item" @click="openSettings">
            <div class="drawer-item-ico" style="background: rgba(109,94,241,.12); color: #6d5ef1;">
              <van-icon name="setting-o" />
            </div>
            <span class="drawer-item-title">参数设置</span>
            <van-icon name="arrow" class="drawer-item-arrow" />
          </div>
"""

# ── D. MobileShell.vue template：设置表单弹层（左抽屉 popup 之后） ──
SETTINGS_FORM_TEMPLATE_BLOCK = """
  <!-- @@CHAT_SETTINGS_FORM_TEMPLATE@@ -->
  <!-- ── 设置表单弹层：模型 / 向量库 / 工作目录 / 清空会话 ── -->
  <van-popup
    v-model:show="showSettingsForm"
    position="bottom"
    round
    :style="{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }"
  >
    <div class="settings-form">
      <div class="settings-form-head">
        <span class="settings-form-title">参数设置</span>
        <van-icon name="cross" class="settings-form-close" @click="showSettingsForm = false" />
      </div>
      <div class="settings-form-body">
        <div class="form-field">
          <div class="form-label">模型</div>
          <van-field
            :model-value="formModelText"
            readonly
            is-link
            placeholder="选择模型"
            @click="showModelPicker = true"
          />
        </div>
        <div class="form-field form-row">
          <div class="form-label">向量库检索</div>
          <van-switch v-model="agent.useVectorDb" size="22" />
        </div>
        <div class="form-field">
          <div class="form-label">工作目录</div>
          <van-field v-model="formDirectory" placeholder="如 F:\\\\tetris" clearable />
          <div class="form-tip">作用于当前聊天会话（随消息发送到 Agent）</div>
        </div>
        <van-button type="primary" block round class="form-save" @click="saveSettings">
          保存设置
        </van-button>
        <van-button type="danger" plain block round class="form-clear" @click="onClearClick">
          {{ clearConfirm ? '再点一次确认清空' : '清空当前会话' }}
        </van-button>
      </div>
    </div>
  </van-popup>

  <van-popup v-model:show="showModelPicker" position="bottom" round>
    <van-picker
      :columns="modelColumns"
      title="选择模型"
      @confirm="onModelConfirm"
      @cancel="showModelPicker = false"
    />
  </van-popup>
"""

# ── D. MobileShell.vue script：设置表单逻辑（挂在 currentView computed 之前） ──
SETTINGS_FORM_SCRIPT_BLOCK = """
  /* @@CHAT_SETTINGS_FORM_SCRIPT@@ */
  // ── 设置表单：模型 / 向量库 / 工作目录 / 清空会话（弹层表单） ──
  const showSettingsForm = ref(false)
  const showModelPicker = ref(false)
  const formModel = ref(agent.selectedModel)
  const formDirectory = ref(agent.sessionDirectory)
  const modelColumns = computed(() => SUPPORTED_MODELS.map(m => ({ text: m.label, value: m.value })))
  const formModelText = computed(
    () => SUPPORTED_MODELS.find(m => m.value === formModel.value)?.label || formModel.value
  )

  function openSettings() {
    formModel.value = agent.selectedModel
    formDirectory.value = agent.sessionDirectory
    showSettingsForm.value = true
  }

  function onModelConfirm(params: any) {
    const opt = params?.selectedOptions?.[0]
    if (opt && opt.value) formModel.value = opt.value
    agent.selectedModel = formModel.value
    showModelPicker.value = false
  }

  function saveSettings() {
    // 直接写 store 状态（不依赖 setSessionDirectory 方法签名）
    agent.sessionDirectory = formDirectory.value.trim()
    showSettingsForm.value = false
  }
"""

# ── D. MobileShell.vue style：设置表单样式（完整自足，不依赖旧样式块） ──
SETTINGS_FORM_STYLE_BLOCK = """
  /* @@CHAT_SETTINGS_FORM_STYLE@@ */
  /* ── 设置表单弹层（与功能导航 drawer-item 视觉统一） ── */
  .settings-form { padding: 6px 0 calc(12px + env(safe-area-inset-bottom, 0px)); }
  .settings-form-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px 6px;
  }
  .settings-form-title { font-size: 16px; font-weight: 700; }
  .settings-form-close {
    font-size: 18px;
    color: var(--text-secondary, #94a3b8);
    cursor: pointer;
    padding: 4px;
  }
  .settings-form-body { padding: 8px 16px; }
  .form-field { margin-bottom: 14px; }
  .form-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary, #64748b);
    margin-bottom: 6px;
    letter-spacing: 0.5px;
  }
  .form-row { display: flex; align-items: center; justify-content: space-between; }
  .form-row .form-label { margin-bottom: 0; }
  .form-tip { font-size: 11px; color: var(--text-secondary, #94a3b8); margin-top: 6px; }
  .form-save { margin-top: 8px; }
  .form-clear { margin-top: 10px; }
"""

# ── A/B. mobile.css 尾部追加：隐藏路径 + tablist 样式 ──
CSS_TAIL = """
/* ============================================================================
 * 聊天框顶部会话标签条 + 头部路径隐藏（@@CHAT_TABLIST_CSS@@）
 * A. 移动端隐藏 chat-header 中的文件/权限路径（session-dir 📁 目录等）
 * B. 移动端 tablist 吸顶在聊天框最上方；桌面端不显示（保持原布局）
 * ============================================================================ */
.chat-tablist { display: none; }

@media (max-width: 768px) {
  /* A. 隐藏文件/权限路径：工作目录已在先前规则隐藏，这里隐藏会话目录 📁 */
  .mobile-body .chat-header .session-dir { display: none !important; }

  /* B. 会话标签条：吸顶在消息列表上方，横向可滚动 */
  .mobile-body .chat-tablist {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    overflow-x: auto;
    flex-shrink: 0;
    border-bottom: 1px solid #f0f1f5;
    background: var(--surface, #fff);
    scrollbar-width: none;
  }
  .mobile-body .chat-tablist::-webkit-scrollbar { display: none; }
  .mobile-body .chat-tab {
    display: inline-flex;
    align-items: center;
    max-width: 160px;
    padding: 6px 12px;
    border-radius: 999px;
    border: 1px solid var(--border, #eef1f6);
    background: var(--bg, #f8fafc);
    font-size: 12px;
    color: var(--text, #1e293b);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    cursor: pointer;
    flex-shrink: 0;
    transition: all 0.15s;
  }
  .mobile-body .chat-tab.current {
    border-color: #6d5ef1;
    background: rgba(109, 94, 241, 0.12);
    color: #6d5ef1;
    font-weight: 600;
  }
  .mobile-body .chat-tab-new {
    padding: 6px 10px;
    border-style: dashed;
    color: var(--text-secondary, #64748b);
  }
}
"""


def detect_newline(s):
    return '\r\n' if '\r\n' in s else '\n'


def backup(path):
    bak = path + '.bak'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print('[backup] ' + bak)
    else:
        print('[backup] exists, skip: ' + bak)


def inject_before(src, anchor, block, nl, tag):
    idx = src.find(anchor)
    if idx == -1:
        print('[vue] ERROR: anchor not found: ' + tag)
        sys.exit(1)
    b = block.strip('\n').replace('\n', nl)
    return src[:idx] + b + nl + src[idx:]


def inject_after(src, anchor, block, nl, tag):
    idx = src.find(anchor)
    if idx == -1:
        print('[vue] ERROR: anchor not found: ' + tag)
        sys.exit(1)
    end = idx + len(anchor)
    b = block.strip('\n').replace('\n', nl)
    return src[:end] + nl + b + src[end:]


def main():
    # ── 1. MultiAgentView.vue：tablist 模板 + 脚本 ──
    with io.open(VIEW, 'r', encoding='utf-8') as f:
        src = f.read()
    nl = detect_newline(src)
    changed = False

    if MARK_TABLIST in src:
        print('[view-template] already injected, skip')
    else:
        backup(VIEW)
        # 锚点：chat-header 结束后、chat-body 之前（聊天框最上方）
        anchor = '\n    <div class="chat-body">'
        src = inject_before(src, anchor, TABLIST_TEMPLATE_BLOCK, nl, 'tablist-template')
        changed = True
        print('[view-template] injected tablist -> ' + VIEW)

    if MARK_TABLIST_SCRIPT in src:
        print('[view-script] already injected, skip')
    else:
        backup(VIEW)
        idx = src.rfind('</script>')
        if idx == -1:
            print('[vue] ERROR: </script> not found in ' + VIEW)
            sys.exit(1)
        b = TABLIST_SCRIPT_BLOCK.strip('\n').replace('\n', nl)
        src = src[:idx] + b + nl + src[idx:]
        changed = True
        print('[view-script] injected tablist logic -> ' + VIEW)

    if changed:
        with io.open(VIEW, 'w', encoding='utf-8', newline='') as f:
            f.write(src)
        print('[view] saved: ' + VIEW)

    # ── 2. MobileShell.vue：设置入口统一 + 表单弹层 + 脚本 + 样式 ──
    with io.open(VUE, 'r', encoding='utf-8') as f:
        src = f.read()
    nl = detect_newline(src)
    changed = False

    # C. 设置分组 → 统一 drawer-item（替换旧 @@CHAT_PANEL_TEMPLATE@@ 分组）
    if MARK_SETTINGS_ENTRY in src:
        print('[vue-settings-entry] already replaced, skip')
    else:
        start = src.find(OLD_GROUP_MARK)
        if start == -1:
            print('[vue] ERROR: old settings group not found (' + OLD_GROUP_MARK + ').')
            print('       Please run scripts/upgrade_chat_panel.py first.')
            sys.exit(1)
        # 旧组结尾：drawer-body 闭合之前（最后一个 10 空格 </div> 之后）
        end_anchor = '\n        </div>\n      </div>\n    </van-popup>'
        end = src.find(end_anchor, start)
        if end == -1:
            print('[vue] ERROR: settings group end anchor not found in ' + VUE)
            sys.exit(1)
        backup(VUE)
        b = SETTINGS_ENTRY_BLOCK.strip('\n').replace('\n', nl)
        src = src[:start] + b + nl + src[end:]
        changed = True
        print('[vue-settings-entry] replaced with unified drawer-item -> ' + VUE)

    # D. 表单弹层模板：左抽屉 </van-popup> 之后
    if MARK_FORM_TEMPLATE in src:
        print('[vue-form-template] already injected, skip')
    else:
        backup(VUE)
        anchor = '\n    </van-popup>'
        src = inject_after(src, anchor, SETTINGS_FORM_TEMPLATE_BLOCK, nl, 'settings-form-template')
        changed = True
        print('[vue-form-template] injected settings form -> ' + VUE)

    # D. 表单脚本：currentView computed 之前
    if MARK_FORM_SCRIPT in src:
        print('[vue-form-script] already injected, skip')
    else:
        backup(VUE)
        anchor = 'const currentView = computed('
        src = inject_before(src, anchor, SETTINGS_FORM_SCRIPT_BLOCK, nl, 'settings-form-script')
        changed = True
        print('[vue-form-script] injected settings form logic -> ' + VUE)

    # D. 表单样式：</style> 之前
    if MARK_FORM_STYLE in src:
        print('[vue-form-style] already injected, skip')
    else:
        backup(VUE)
        anchor = '\n</style>'
        src = inject_before(src, anchor, SETTINGS_FORM_STYLE_BLOCK, nl, 'settings-form-style')
        changed = True
        print('[vue-form-style] injected settings form styles -> ' + VUE)

    if changed:
        with io.open(VUE, 'w', encoding='utf-8', newline='') as f:
            f.write(src)
        print('[vue] saved: ' + VUE)

    # ── 3. mobile.css：追加隐藏路径 + tablist 样式 ──
    with io.open(CSS, 'r', encoding='utf-8') as f:
        src = f.read()
    nl = detect_newline(src)
    if MARK_CSS in src:
        print('[css] already appended, skip')
    else:
        backup(CSS)
        b = CSS_TAIL.strip('\n').replace('\n', nl)
        with io.open(CSS, 'a', encoding='utf-8', newline='') as f:
            f.write(nl + b + nl)
        print('[css] appended tablist/path styles -> ' + CSS)

    print('[DONE] 升级完成：A 隐藏路径 / B tablist 吸顶 / C 设置样式统一 / D 设置表单')


if __name__ == '__main__':
    main()
