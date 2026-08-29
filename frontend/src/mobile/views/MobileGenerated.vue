<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useGeneratedStore } from '../../stores/generated'
import { getGeneratedContent, downloadGenerated } from '../../api/generated'
import { showConfirmDialog, showToast } from 'vant'

const store = useGeneratedStore()

const runningFile = ref<string | null>(null)
const runOutput = ref<string | null>(null)
const runError = ref<string | null>(null)

onMounted(() => {
  store.fetchAll()
})

function isJsFile(filename: string): boolean {
  return filename.endsWith('.js')
}

async function handleDelete(filename: string) {
  try {
    await showConfirmDialog({ title: '删除文件', message: `确定删除「${filename}」？` })
    await store.remove(filename)
    showToast('已删除')
  } catch (err: any) {
    if (err?.message && !String(err.message).includes('cancel')) showToast(err.message)
  }
}

async function handleDownload(filename: string) {
  try {
    await downloadGenerated(filename)
  } catch (err: any) {
    showToast(err.message || '下载失败')
  }
}

async function handleRun(filename: string) {
  runningFile.value = filename
  runOutput.value = null
  runError.value = null
  try {
    const code = await getGeneratedContent(filename)
    const logs: string[] = []
    const writtenFiles: Record<string, string> = {}
    const mockConsole = {
      log: (...args: unknown[]) => logs.push(args.map(a => String(a)).join(' ')),
      warn: (...args: unknown[]) => logs.push('[warn] ' + args.map(a => String(a)).join(' ')),
      error: (...args: unknown[]) => logs.push('[error] ' + args.map(a => String(a)).join(' ')),
    }
    const mockRequire = (mod: string) => {
      if (mod === 'fs') {
        return {
          writeFileSync: (path: string, data: string) => { writtenFiles[path] = data; logs.push(`[fs] wrote ${data.length} bytes to ${path}`) },
          readFileSync: (path: string) => { logs.push(`[fs] read ${path} (returned empty mock)`); return '' },
          existsSync: () => true,
          mkdirSync: () => {},
        }
      }
      if (mod === 'path') {
        return {
          join: (...args: string[]) => args.join('/'),
          resolve: (...args: string[]) => args.join('/'),
          dirname: (p: string) => p.split('/').slice(0, -1).join('/'),
          basename: (p: string) => p.split('/').pop() || p,
        }
      }
      throw new Error(`require('${mod}') is not supported in browser. Try running this file on the server side.`)
    }
    const fn = new Function('console', 'require', code)
    const result = fn(mockConsole, mockRequire)
    if (result !== undefined) logs.push('=> ' + String(result))
    const output = logs.join('\n') || '(no output)'
    runOutput.value = Object.keys(writtenFiles).length > 0
      ? output + '\n\n--- Files written ---\n' + Object.entries(writtenFiles).map(([p, d]) => `${p} (${d.length} bytes)`).join('\n')
      : output
  } catch (err: any) {
    runError.value = err.message || String(err)
  }
}

function closeOutput() {
  runningFile.value = null
  runOutput.value = null
  runError.value = null
}
</script>

<template>
  <div class="m-generated">
    <van-search v-model="store.searchQuery" placeholder="按文件名搜索..." />

    <van-loading v-if="store.loading" class="loading" />
    <van-empty
      v-else-if="store.filteredFiles.length === 0"
      image="file"
      :description="store.searchQuery ? '没有匹配的文件' : '还没有生成文件'"
    />

    <div v-else class="file-list">
      <div class="count">{{ store.filteredFiles.length }} 个文件</div>
      <van-cell-group inset>
        <van-swipe-cell v-for="f in store.filteredFiles" :key="f.filename">
          <van-cell :title="f.filename" :label="store.formatSize(f.size)" :icon="isJsFile(f.filename) ? 'lightning' : 'description'" class="m-file-cell" :class="isJsFile(f.filename) ? 'ftype-js' : 'ftype-doc'" is-link @click="isJsFile(f.filename) ? handleRun(f.filename) : handleDownload(f.filename)" />
          <template #right>
            <div class="swipe-btn swipe-run" v-if="isJsFile(f.filename)" @click="handleRun(f.filename)">运行</div>
            <div class="swipe-btn swipe-dl" @click="handleDownload(f.filename)">下载</div>
            <div class="swipe-btn swipe-del" @click="handleDelete(f.filename)">删除</div>
          </template>
        </van-swipe-cell>
      </van-cell-group>
    </div>

    <van-popup :show="!!runningFile" round position="bottom" :style="{ height: '70%' }" @close="closeOutput">
      <div class="run-panel" v-if="runningFile">
        <div class="run-head">
          <span class="run-title">输出：{{ runningFile }}</span>
          <van-button size="small" @click="closeOutput">关闭</van-button>
        </div>
        <pre v-if="runOutput" class="run-body">{{ runOutput }}</pre>
        <pre v-else-if="runError" class="run-body error">{{ runError }}</pre>
        <div v-else class="run-body">运行中...</div>
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.m-generated { padding-bottom: 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.file-list { display: flex; flex-direction: column; }
.count { font-size: 12px; color: #97a0b4; padding: 6px 16px; }
.swipe-btn {
  width: 56px; height: 100%; display: flex; align-items: center; justify-content: center;
  color: #fff; font-size: 14px;
}
.swipe-run { background: #4f46e5; }
.swipe-dl { background: #1989fa; }
.swipe-del { background: #ee0a24; }
.run-panel { padding: 12px 16px; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; }
.run-head { display: flex; align-items: center; justify-content: space-between; padding-bottom: 8px; }
.run-title { font-weight: 600; }
.run-body { flex: 1; overflow: auto; margin: 0; font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; background: #0f172a; color: #e2e8f0; border-radius: 10px; padding: 12px; }
.run-body.error { color: #f87171; }
</style>

<style scoped>
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

