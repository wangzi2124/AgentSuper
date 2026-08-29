#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
upgrade_mobile_ui.py — 移动端 UI 改版脚本（分批执行，小步快跑）

用法:
    python scripts/upgrade_mobile_ui.py monitor    # 只改 MobileMonitoring.vue
    python scripts/upgrade_mobile_ui.py all        # 全部已实现视图

行为:
    1. 读取目标 SFC，提取 <script setup> 块（逐行保留，不动逻辑）
    2. 用脚本内嵌的新 <template> / <style> 重组文件
    3. 首次修改前自动生成 *.vue.bak 备份（已存在则不再覆盖）
    4. 幂等：若目标文件已包含新模板标记（.m-stat-card）则跳过
"""
import re
import shutil
import sys
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
VIEWS = BASE / 'frontend' / 'src' / 'mobile' / 'views'

# ---------------------------------------------------------------------------
# MobileMonitoring.vue — 系统监控：平铺 cell -> 2x2 统计卡 + 分组卡片
# ---------------------------------------------------------------------------
MONITOR_TEMPLATE = '''<template>
  <div class="m-monitor">
    <van-loading v-if="loading" class="loading" />
    <van-empty v-else-if="error" image="network" :description="error" />
    <template v-else-if="stats">
      <!-- 2x2 统计卡片 -->
      <div class="m-grid">
        <div class="m-stat-card">
          <div class="m-stat-icon ic-blue"><van-icon name="chart-trending-o" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">HTTP 请求</div>
            <div class="m-stat-value">{{ num(stats.requests.total) }}</div>
          </div>
        </div>
        <div class="m-stat-card">
          <div class="m-stat-icon ic-violet"><van-icon name="records-o" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">LLM 调用</div>
            <div class="m-stat-value">{{ num(stats.model_calls.total) }}</div>
          </div>
        </div>
        <div class="m-stat-card">
          <div class="m-stat-icon ic-cyan"><van-icon name="descending" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">输入 Token</div>
            <div class="m-stat-value">{{ num(stats.model_calls.total_prompt_tokens) }}</div>
          </div>
        </div>
        <div class="m-stat-card">
          <div class="m-stat-icon ic-orange"><van-icon name="ascending" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">输出 Token</div>
            <div class="m-stat-value">{{ num(stats.model_calls.total_completion_tokens) }}</div>
          </div>
        </div>
      </div>

      <!-- HTTP 明细 -->
      <div class="m-sec-title">HTTP 请求明细</div>
      <div class="m-card m-card-pad">
        <div v-for="(count, path) in stats.requests.by_path" :key="path" class="m-row">
          <span class="m-row-name mono">{{ path }}</span>
          <span class="m-pill">{{ num(count) }}</span>
        </div>
        <div v-if="Object.keys(stats.requests.by_status).length" class="m-tags">
          <span
            v-for="(count, status) in stats.requests.by_status"
            :key="'s' + status"
            class="m-tag"
          >状态 {{ status }} · {{ num(count) }}</span>
        </div>
      </div>

      <!-- LLM 明细 -->
      <div class="m-sec-title">LLM 调用</div>
      <div class="m-card m-card-pad">
        <div class="m-row">
          <span class="m-row-name">总耗时</span>
          <span class="m-row-val">{{ ms(stats.model_calls.total_duration_ms) }}</span>
        </div>
        <div class="m-row">
          <span class="m-row-name">平均耗时</span>
          <span class="m-row-val">{{ ms(stats.model_calls.avg_duration_ms) }}</span>
        </div>
        <div class="m-row">
          <span class="m-row-name">工具轮数</span>
          <span class="m-row-val">{{ num(stats.model_calls.tool_rounds_total) }}（均 {{ stats.model_calls.avg_tool_rounds }}）</span>
        </div>
      </div>

      <!-- 按模型 -->
      <div class="m-sec-title">按模型</div>
      <div class="m-card m-card-pad">
        <div v-for="(count, model) in stats.model_calls.by_model" :key="model" class="m-row">
          <span class="m-row-name mono">{{ model }}</span>
          <span class="m-pill">{{ num(count) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>
'''

MONITOR_STYLE = '''<style scoped>
.m-monitor { padding: 8px 12px 24px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }

/* 2x2 统计卡 */
.m-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 4px; }
.m-stat-card {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  padding: 12px;
}
.m-stat-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  color: #fff;
  flex-shrink: 0;
}
.ic-blue   { background: linear-gradient(135deg, #3b82f6, #38bdf8); }
.ic-violet { background: linear-gradient(135deg, #8b5cf6, #6d5ef1); }
.ic-cyan   { background: linear-gradient(135deg, #06b6d4, #22d3ee); }
.ic-orange { background: linear-gradient(135deg, #f59e0b, #f97316); }
.m-stat-meta { min-width: 0; }
.m-stat-label { font-size: 12px; color: var(--text-secondary, #64748b); }
.m-stat-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text, #1e293b);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 卡片内行 */
.m-card-pad { padding: 4px 14px; }
.m-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 11px 0;
  border-bottom: 1px dashed var(--border, #eef1f6);
}
.m-row:last-child { border-bottom: none; }
.m-row-name {
  font-size: 13px;
  color: var(--text, #1e293b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-row-val { font-size: 13px; font-weight: 600; color: var(--text, #1e293b); flex-shrink: 0; }
.mono { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
.m-pill {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--m-monitor, #f59e0b);
  background: var(--m-monitor-soft, rgba(245, 158, 11, 0.12));
  padding: 3px 10px;
  border-radius: 999px;
}
.m-tags { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 0 4px; }
.m-tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: 999px;
  padding: 4px 10px;
}
</style>
'''

# ---------------------------------------------------------------------------
# MobileVectors.vue — 向量库：搜索 + 绿色统计横幅 + 筛选卡 + 分块卡片
# ---------------------------------------------------------------------------
VECTORS_TEMPLATE = '''<template>
  <div class="m-vectors">
    <van-search
      v-model="searchText"
      placeholder="搜索分块内容..."
      show-action
      @search="onSearch"
      @cancel="onSearch"
    />

    <!-- 统计横幅（绿色系） -->
    <div class="m-hero">
      <div class="m-hero-icon"><van-icon name="apps-o" /></div>
      <div class="m-hero-meta">
        <div class="m-hero-label">向量库</div>
        <div class="m-hero-value">{{ vs.total }} 个分块</div>
      </div>
      <div class="m-hero-extra">
        <div class="m-hero-mini"><span class="m-hero-num">{{ ds.documents.length }}</span> 文档</div>
        <div class="m-hero-mini" v-if="vs.searchQuery"><span class="m-hero-num">{{ vs.chunks.length }}</span> 匹配</div>
      </div>
    </div>

    <!-- 筛选入口 -->
    <div class="m-filter-card" @click="filterSheet = true">
      <div class="m-filter-left">
        <div class="m-filter-icon"><van-icon name="filter-o" /></div>
        <div class="m-filter-meta">
          <div class="m-filter-title">{{ chunksTitle }}</div>
          <div class="m-filter-sub">{{ selectedDocLabel() }}</div>
        </div>
      </div>
      <van-icon name="arrow" class="m-filter-arrow" />
    </div>

    <van-loading v-if="vs.loading && vs.chunks.length === 0" class="loading" />
    <van-empty v-else-if="vs.chunks.length === 0" image="search" description="未找到分块" />

    <div v-else class="chunk-list">
      <div v-for="(chunk, i) in vs.chunks" :key="chunk.id" class="m-chunk-card">
        <div class="m-chunk-head">
          <div v-if="chunk.metadata.chapter_title" class="m-chunk-chapter">
            <van-icon name="orders-o" /> {{ chunk.metadata.chapter_title }}
          </div>
          <div class="m-chunk-idx">#{{ vs.offset + i + 1 }}</div>
        </div>
        <div class="m-chunk-src">{{ chunkSource(chunk) }}</div>
        <div class="m-chunk-text">{{ chunk.text.slice(0, 200) }}{{ chunk.text.length > 200 ? '...' : '' }}</div>
      </div>
    </div>

    <div v-if="hasMore" class="load-more">
      <van-button size="small" round plain type="primary" @click="loadMore">加载更多</van-button>
    </div>

    <div class="actions">
      <van-button size="small" v-if="vs.config && vs.config.ttl_days > 0" @click="handleClearExpired">
        清理过期（TTL {{ vs.config.ttl_days }} 天）
      </van-button>
      <van-button size="small" type="danger" plain @click="handleClearAll">清空向量库</van-button>
    </div>

    <van-action-sheet
      v-model:show="filterSheet"
      :actions="docOptions"
      title="按文档筛选"
      cancel-text="取消"
      @select="onPickDoc"
    />
  </div>
</template>
'''

VECTORS_STYLE = '''<style scoped>
.m-vectors { padding: 8px 12px 24px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }

/* 顶部统计横幅（绿色系） */
.m-hero {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 4px 0 12px;
  padding: 14px 16px;
  border-radius: var(--m-card-radius, 16px);
  background: linear-gradient(135deg, #059669, #10b981 55%, #34d399);
  box-shadow: 0 6px 18px rgba(16, 185, 129, 0.28);
  color: #fff;
}
.m-hero-icon {
  width: 40px;
  height: 40px;
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.m-hero-meta { flex: 1; min-width: 0; }
.m-hero-label { font-size: 12px; opacity: 0.85; }
.m-hero-value { font-size: 20px; font-weight: 700; margin-top: 2px; }
.m-hero-extra { text-align: right; flex-shrink: 0; }
.m-hero-mini { font-size: 11px; opacity: 0.92; margin-top: 2px; }
.m-hero-num { font-weight: 700; font-size: 13px; }

/* 筛选入口卡片 */
.m-filter-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  padding: 13px 14px;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
}
.m-filter-left { display: flex; align-items: center; gap: 10px; min-width: 0; }
.m-filter-icon {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  color: var(--m-vector, #10b981);
  background: var(--m-vector-soft, rgba(16, 185, 129, 0.12));
  flex-shrink: 0;
}
.m-filter-meta { min-width: 0; }
.m-filter-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text, #1e293b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-filter-sub { font-size: 12px; color: var(--text-secondary, #64748b); margin-top: 2px; }
.m-filter-arrow { color: var(--text-tertiary, #94a3b8); font-size: 14px; }

/* 分块卡片 */
.chunk-list { display: flex; flex-direction: column; gap: 10px; }
.m-chunk-card {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  padding: 12px 14px;
}
.m-chunk-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 6px;
}
.m-chunk-chapter {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  font-weight: 600;
  color: var(--m-vector, #10b981);
  border-left: 3px solid var(--m-vector, #10b981);
  padding-left: 8px;
  line-height: 1.4;
  min-width: 0;
}
.m-chunk-idx {
  font-size: 11px;
  font-weight: 700;
  color: var(--m-vector, #10b981);
  background: var(--m-vector-soft, rgba(16, 185, 129, 0.12));
  border-radius: 999px;
  padding: 2px 8px;
  flex-shrink: 0;
}
.m-chunk-src {
  font-size: 11px;
  color: var(--text-tertiary, #94a3b8);
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-chunk-text {
  font-size: 13px;
  color: var(--text-secondary, #64748b);
  line-height: 1.6;
  word-break: break-word;
}
.load-more { display: flex; justify-content: center; padding: 16px 0; }
.actions { display: flex; justify-content: flex-end; gap: 12px; padding: 12px 4px; }
</style>
'''

# ---------------------------------------------------------------------------
# MobileGenerated.vue — 生成文件：cell -> 文件卡片 + 类型彩色图标块
# MobileCustomTools.vue — 自定义工具：CTA 渐变按钮 + script粉/pin青 工具卡
# 走 replace_once（锚点模板） + append_style（追加 scoped 样式），visual-only。
# ---------------------------------------------------------------------------

GENERATED_STYLE = '''<style scoped>
/* ===== @@M_FILE_CARD@@ 生成文件卡片化（upgrade_mobile_ui.py generated job） ===== */
.file-list { gap: 10px; padding: 0 12px; }
.count {
  font-size: 12px;
  font-weight: 700;
  color: var(--m-generated, #6d5ef1);
  background: var(--m-generated-soft, rgba(109, 94, 241, 0.10));
  align-self: flex-start;
  border-radius: 999px;
  padding: 4px 12px;
  margin: 4px 2px 8px;
}
.m-file-cell.van-cell--clickable {
  border-radius: var(--m-card-radius, 16px);
  background: var(--surface, #fff) !important;
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  margin-bottom: 6px;
  border: 1px solid var(--border, #eef1f6);
  padding: 10px 12px;
}
.m-file-cell :deep(.van-cell__left-icon) {
  width: 34px;
  height: 34px;
  border-radius: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  color: #fff;
  flex-shrink: 0;
}
.m-file-cell.ftype-js :deep(.van-cell__left-icon) {
  background: linear-gradient(135deg, #8b5cf6, #ec4899);
}
.m-file-cell.ftype-doc :deep(.van-cell__left-icon) {
  background: linear-gradient(135deg, #06b6d4, #3b82f6);
}
.m-file-cell :deep(.van-cell__title) {
  font-size: 14px;
  font-weight: 600;
  color: var(--text, #1e293b);
}
.m-file-cell :deep(.van-cell__label) {
  font-size: 12px;
  color: var(--text-tertiary, #94a3b8);
  margin-top: 2px;
}

/* swipe 右侧操作区：圆角色块 */
.m-generated :deep(.van-swipe-cell__right) {
  border-radius: 12px;
  overflow: hidden;
  margin-left: 8px;
}
.swipe-btn {
  font-weight: 600;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.12);
}
.swipe-run { background: linear-gradient(135deg, #6d5ef1, #8b5cf6); }
.swipe-dl  { background: linear-gradient(135deg, #1989fa, #38bdf8); }
.swipe-del { background: linear-gradient(135deg, #ee0a24, #f87171); }

/* 运行面板：渐变头部 + 卡片化输出区 */
.run-panel { padding: 14px 16px; }
.run-head {
  background: linear-gradient(135deg, #6d5ef1, #8b5cf6);
  border-radius: 14px;
  padding: 10px 14px;
  color: #fff;
  margin-bottom: 10px;
}
.run-head .run-title { font-size: 14px; font-weight: 700; }
.run-head :deep(.van-button) { border-radius: 999px; }
.run-body { border-radius: 14px; box-shadow: inset 0 1px 6px rgba(0, 0, 0, 0.25); }
</style>
'''

TOOLS_STYLE = '''<style scoped>
/* ===== @@M_TOOL_CARD@@ 自定义工具卡片化（upgrade_mobile_ui.py tools job） ===== */
.m-tools { padding: 0 12px 24px; }

/* CTA：品牌渐变胶囊按钮（覆盖 Vant plain primary 轮廓） */
.cta { padding: 4px 2px 14px; }
.m-tools :deep(.cta .van-button--plain.van-button--primary) {
  color: #fff;
  background: linear-gradient(135deg, #6d5ef1, #8b5cf6);
  border: none;
  border-radius: 999px;
  font-weight: 600;
  box-shadow: 0 4px 14px rgba(109, 94, 241, 0.30);
}
.tool-list { gap: 8px; padding: 0 2px; }
.count {
  font-size: 12px;
  font-weight: 700;
  color: var(--m-tools, #8b5cf6);
  background: var(--m-tools-soft, rgba(139, 92, 246, 0.10));
  align-self: flex-start;
  border-radius: 999px;
  padding: 4px 12px;
  margin: 4px 2px 8px;
}
.m-tools :deep(.van-swipe-cell .van-cell) {
  border-radius: var(--m-card-radius, 16px);
  background: var(--surface, #fff) !important;
  border: 1px solid var(--border, #eef1f6);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  margin-bottom: 8px;
}
.m-tools :deep(.van-cell__title) { font-size: 14px; font-weight: 600; color: var(--text, #1e293b); }
.m-tools :deep(.van-cell__label) { font-size: 12px; color: var(--text-secondary, #64748b); }

/* 工具类型语义色：script 粉 / pin 青 */
.m-tools :deep(.t-script .van-cell__value .van-switch) { --van-switch-on-background: #ec4899; }
.m-tools :deep(.t-pin .van-cell__value .van-switch)    { --van-switch-on-background: #06b6d4; }
.m-tools :deep(.t-script .van-cell__title) { border-left: 3px solid var(--sw-pink, #ec4899); padding-left: 8px; line-height: 1.4; }
.m-tools :deep(.t-pin .van-cell__title)    { border-left: 3px solid var(--sw-cyan, #06b6d4); padding-left: 8px; line-height: 1.4; }
.swipe-del { background: linear-gradient(135deg, #ee0a24, #f87171); font-weight: 600; }

/* 表单面板：渐变标题头 + 选中项描边 */
.form-panel :deep(.form-title) {
  font-size: 16px;
  font-weight: 700;
  display: inline-block;
  padding: 2px 0 4px;
  background: linear-gradient(90deg, #6d5ef1, #8b5cf6);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.form-panel { background: var(--surface, #fff); }
.pin-options :deep(.van-cell.active) {
  background: var(--primary-soft, rgba(109, 94, 241, 0.10));
  border: 1px solid var(--primary, #4f46e5);
  border-radius: 12px;
}
.pin-options :deep(.van-cell) { border: 1px solid var(--border, #eef1f6); border-radius: 12px; margin-bottom: 6px; }
</style>
'''


def generated_job() -> tuple:
    """生成文件页：cell 卡片 + 类型图标块（锚点模板替换 + 追加样式）。"""
    path = VIEWS / 'MobileGenerated.vue'
    old = ":icon=\"isJsFile(f.filename) ? 'lightning' : 'description'\" is-link"
    new = (":icon=\"isJsFile(f.filename) ? 'lightning' : 'description'\""
           " class=\"m-file-cell\" :class=\"isJsFile(f.filename) ? 'ftype-js' : 'ftype-doc'\" is-link")
    tmpl_ok = replace_once(path, old, new, 'm-file-cell')
    style_ok = append_style(path, GENERATED_STYLE, '@@M_FILE_CARD@@')
    return tmpl_ok or style_ok


def tools_job() -> tuple:
    """自定义工具页：CTA 渐变 + script粉/pin青 工具卡（锚点模板替换 + 追加样式）。"""
    path = VIEWS / 'MobileCustomTools.vue'
    old = "<van-cell\n          :title=\"item.name\""
    new = "<van-cell\n          :class=\"'t-' + item.type\"\n          :title=\"item.name\""
    tmpl_ok = replace_once(path, old, new, 't-script')
    style_ok = append_style(path, TOOLS_STYLE, '@@M_TOOL_CARD@@')
    return tmpl_ok or style_ok


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def extract_script(text: str) -> str:
    m = re.search(r'<script[^>]*>.*?</script>', text, re.S)
    return m.group(0) if m else ''


def make_backup(path: pathlib.Path) -> bool:
    """首次修改前生成 *.vue.bak 备份（已存在则不再覆盖）。"""
    backup = path.with_suffix(path.suffix + '.bak')
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f'[BAK ] {path.name} -> {backup.name}')
        return True
    return False


def patch_file(path: pathlib.Path, new_template: str, new_style: str, marker: str) -> bool:
    text = path.read_text(encoding='utf-8')
    if marker in text:
        print(f'[SKIP] {path.name}: already patched (marker {marker!r} found)')
        return False
    script = extract_script(text)
    if not script:
        print(f'[FAIL] {path.name}: <script> block not found, abort')
        return False
    make_backup(path)
    path.write_text(script + '\n\n' + new_template + '\n\n' + new_style + '\n', encoding='utf-8')
    print(f'[OK  ] {path.name} patched')
    return True


def replace_once(path: pathlib.Path, old: str, new: str, marker: str) -> bool:
    """锚定模板改动：old 必须恰好命中一次，否则 abort 且不写回（幂等由 marker 防重）。"""
    text = path.read_text(encoding='utf-8')
    if marker in text:
        print(f'[SKIP] {path.name}: already patched (marker {marker!r} found)')
        return False
    count = text.count(old)
    if count != 1:
        print(f'[FAIL] {path.name}: pattern matched {count} times (expect 1), abort without writing'
              + (f'; left unchanged' if count > 0 else ''))
        return False
    make_backup(path)
    path.write_text(text.replace(old, new), encoding='utf-8')
    print(f'[OK  ] {path.name} templated (marker {marker!r})')
    return True


def append_style(path: pathlib.Path, new_style: str, marker: str) -> bool:
    """追加 <style scoped> 块（Vue SFC 允许多个 style 块，visual-only，不动模板/脚本）。"""
    text = path.read_text(encoding='utf-8')
    if marker in text:
        print(f'[SKIP] {path.name}: styles already injected (marker {marker!r})')
        return False
    if '</style>' not in text:
        print(f'[FAIL] {path.name}: no <style> block found, abort')
        return False
    make_backup(path)
    path.write_text(text + '\n' + new_style + '\n', encoding='utf-8')
    print(f'[OK  ] {path.name} styles appended (marker {marker!r})')
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else 'all'
    jobs = []
    if target in ('monitor', 'all'):
        jobs.append(('MobileMonitoring.vue', MONITOR_TEMPLATE, MONITOR_STYLE, 'm-stat-card'))
    if target in ('vectors', 'all'):
        jobs.append(('MobileVectors.vue', VECTORS_TEMPLATE, VECTORS_STYLE, 'm-chunk-card'))
    if target in ('generated', 'all'):
        jobs.append(('generated_job', generated_job, GENERATED_STYLE, 'm-file-cell'))
    if target in ('tools', 'all'):
        jobs.append(('tools_job', tools_job, TOOLS_STYLE, 't-script'))

    done = 0
    for name, fn, _style, marker in jobs:
        if name in ('generated_job', 'tools_job'):
            if fn():
                done += 1
            continue
        if patch_file(VIEWS / name, fn, _style, marker):
            done += 1
    print(f'--- done: {done}/{len(jobs)} ---')


if __name__ == '__main__':
    main()
