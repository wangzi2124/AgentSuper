# -*- coding: utf-8 -*-
"""
upgrade_chat_scroll.py — ChatGPT 式「回到底部」按钮样式补齐（幂等升级脚本）

背景：MultiAgentView.vue 的 script 滚动逻辑与 template 按钮已就位，
     但 <style scoped> 缺少 .scroll-to-bottom-btn / .scroll-fade 样式，
     mobile.css 也缺少移动端触控规则 → 按钮「裸奔」。

本脚本自动完成：
  A. MultiAgentView.vue  → 在 </style> 前注入按钮基础样式（浮动定位/圆角/阴影/过渡）
  B. mobile.css          → 末尾追加移动端触控规则（48px 触控 / safe-area / 毛玻璃 / 深色）

用法：python scripts/upgrade_chat_scroll.py
幂等：注入内容带标记 /* @@CHAT_SCROLL_INJECTED@@ */，已存在则跳过。
备份：首次修改前自动生成 .bak。
"""
import io
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VUE = os.path.join(ROOT, 'frontend', 'src', 'views', 'MultiAgentView.vue')
CSS = os.path.join(ROOT, 'frontend', 'src', 'styles', 'mobile.css')

MARK = '/* @@CHAT_SCROLL_INJECTED@@ */'

SCOPED_STYLE = """
  /* @@CHAT_SCROLL_INJECTED@@ */
  /* ── ChatGPT 式「回到底部」浮动按钮（用户滚动优先） ── */
  .scroll-to-bottom-btn {
    position: absolute;
    right: 20px;
    bottom: 16px;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 1px solid var(--border, #eef1f6);
    background: var(--surface, #ffffff);
    color: var(--text-secondary, #64748b);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    box-shadow: 0 6px 20px rgba(31, 41, 55, 0.14);
    transition: all 0.2s ease;
    z-index: 20;
    -webkit-tap-highlight-color: transparent;
  }
  .scroll-to-bottom-btn:hover {
    color: var(--primary, #4f46e5);
    transform: translateY(-1px);
    box-shadow: 0 8px 24px rgba(31, 41, 55, 0.18);
  }
  .scroll-to-bottom-btn:active { transform: scale(0.94); }

  /* 进入/离开过渡：淡入 + 上浮 */
  .scroll-fade-enter-active,
  .scroll-fade-leave-active { transition: opacity 0.22s ease, transform 0.22s ease; }
  .scroll-fade-enter-from,
  .scroll-fade-leave-to { opacity: 0; transform: translateY(12px); }
"""

CSS_TAIL = """
/* ============================================================================
 * 移动端「回到底部」按钮触控适配（ChatGPT 式滚动交互 · 第 6 轮追加）
 * 覆盖 MultiAgentView scoped 样式：加大触控区、贴 safe-area、毛玻璃、深色适配。
 * ============================================================================ */
/* @@CHAT_SCROLL_INJECTED@@ */
@media (max-width: 768px) {
  .scroll-to-bottom-btn {
    width: 48px !important;
    height: 48px !important;
    right: 14px !important;
    bottom: calc(14px + env(safe-area-inset-bottom, 0px)) !important;
    background: rgba(255, 255, 255, 0.92) !important;
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border-color: #eef1f6 !important;
    box-shadow: 0 6px 24px rgba(31, 41, 55, 0.16) !important;
  }
}

html[data-theme='dark'] {
  .scroll-to-bottom-btn {
    background: rgba(17, 26, 45, 0.92) !important;
    border-color: var(--border, #2b3650) !important;
    color: #cbd5e1 !important;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.45) !important;
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


def main():
    changed = False

    # ---- A. MultiAgentView.vue：注入 scoped 基础样式 ----
    with io.open(VUE, 'r', encoding='utf-8') as f:
        src = f.read()
    if MARK in src:
        print('[vue] already injected, skip')
    else:
        backup(VUE)
        nl = detect_newline(src)
        idx = src.rfind('</style>')
        if idx == -1:
            print('[vue] ERROR: </style> not found')
            sys.exit(1)
        block = SCOPED_STYLE.strip('\n').replace('\n', nl)
        src = src[:idx] + block + nl + '</style>' + src[idx + len('</style>'):]
        with io.open(VUE, 'w', encoding='utf-8', newline='') as f:
            f.write(src)
        print('[vue] injected scoped styles -> ' + VUE)
        changed = True

    # ---- B. mobile.css：追加移动端触控规则 ----
    with io.open(CSS, 'r', encoding='utf-8') as f:
        css = f.read()
    if MARK in css:
        print('[css] already injected, skip')
    else:
        backup(CSS)
        nl = detect_newline(css)
        tail = CSS_TAIL.strip('\n').replace('\n', nl)
        with io.open(CSS, 'a', encoding='utf-8', newline='') as f:
            f.write(nl + tail + nl)
        print('[css] appended mobile rules -> ' + CSS)
        changed = True

    if changed:
        print('DONE. Next: cd frontend && npm run build')
    else:
        print('Nothing to do (already injected).')


if __name__ == '__main__':
    main()
