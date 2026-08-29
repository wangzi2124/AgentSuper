# 移动端 UI 改版 — 源码修改说明

版本 v1.0 / 2026-08-29
执行方式：python scripts/upgrade_mobile_ui.py（禁止手工直接改源码）
适用范围：frontend/src/mobile/views/* 、 frontend/src/styles/mobile.css

## 一、背景

移动端（<=768px）界面原为「van-cell 列表 + 单一强调色 #4f46e5」，视觉单调、无品牌感、深色模式有硬编码浅色残留（问题编号 U1~U7）。本次通过 Python 脚本统一升级源码，实现卡片化 + 语义色彩 + 品牌渐变。设计令牌统一在 styles/mobile.css 的 --m-* 变量中定义。

## 二、本次脚本修改的文件清单

| 文件 | 操作 | 修改要点 |
|---|---|---|
| MobileVectors.vue | 整文件重写 | 顶部渐变统计卡、筛选卡片化、chunk 卡片（序号徽章 + 章节引用条）；逻辑不变 |
| MobileMonitoring.vue | 整文件重写 | 2x2 大数字统计卡（渐变描边 + 图标）、HTTP/LLM 分区彩色标题；fetchStats 逻辑不变 |
| MobileGenerated.vue | 整文件重写 | 文件卡片（JS=闪电橙 / 其他=文件蓝）、swipe 圆角色块、运行面板渐变头；逻辑不变 |
| MobileCustomTools.vue | 整文件重写 | CTA 渐变按钮、工具卡片（script 粉 / pin 青）、表单渐变头部；逻辑不变 |
| mobile.css | 追加 | 聊天页气泡圆角、输入框阴影、发送按钮渐变（样式层覆盖） |

> 所有视图仅改 <template> 与 <style>，<script setup> 数据流/API/事件逐行保留，保证行为零回归。

## 三、执行与回滚

执行升级（工作区根目录）：python scripts/upgrade_mobile_ui.py
构建验证：cd frontend && npm run build

回滚：脚本执行前为每个目标文件生成 .bak 备份（同目录 *.vue.bak）；构建失败时把 .bak 重命名回原文件即可。

## 四、验收

- [ ] 四个页面视觉升级生效
- [ ] 深色模式（html[data-theme=dark]）下无硬编码浅色残留
- [ ] 原有交互全部可用
- [ ] npm run build（含 vue-tsc）通过
