<script setup lang="ts">
import { onMounted } from 'vue'
import { useDocumentStore } from '../../stores/documents'
import { showConfirmDialog, showToast } from 'vant'

const docs = useDocumentStore()

onMounted(() => {
  docs.fetchAll()
})

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// 文件类型 → 彩色方块（颜色/缩写）
function typeMeta(filename: string): { label: string; color: string; soft: string } {
  const ext = (filename.split('.').pop() || '').toUpperCase()
  const map: Record<string, { label: string; color: string; soft: string }> = {
    PDF: { label: 'PDF', color: 'var(--m-pdf)', soft: 'var(--m-pdf-soft)' },
    MD: { label: 'MD', color: 'var(--m-md)', soft: 'var(--m-md-soft)' },
    MARKDOWN: { label: 'MD', color: 'var(--m-md)', soft: 'var(--m-md-soft)' },
    TXT: { label: 'TXT', color: 'var(--m-txt)', soft: 'var(--m-txt-soft)' },
    DOC: { label: 'DOC', color: 'var(--m-doc)', soft: 'var(--m-doc-soft)' },
    DOCX: { label: 'DOCX', color: 'var(--m-doc)', soft: 'var(--m-doc-soft)' },
  }
  return map[ext] || { label: ext.slice(0, 4), color: 'var(--m-file)', soft: 'var(--m-file-soft)' }
}

async function handleDelete(id: string) {
  try {
    await showConfirmDialog({
      title: '删除文档',
      message: '确定要删除该文档吗？相关向量分块也会被清除。',
    })
    await docs.remove(id)
    showToast('已删除')
  } catch (err: any) {
    if (err?.message && !String(err.message).includes('cancel')) {
      showToast(err.message)
    }
  }
}

async function handleUpload(file: File) {
  try {
    await docs.upload(file)
    showToast('上传成功')
  } catch (err: any) {
    showToast(err.message || '上传失败')
  }
}
</script>

<template>
  <div class="m-docs">
    <div class="upload-wrap">
      <van-uploader
        :after-read="(item: any) => handleUpload(item.file as File)"
        :max-count="1"
        accept="*"
      >
        <div class="upload-btn">
          <van-icon name="plus" />
          <span>上传文档</span>
          <span class="upload-sub">支持 .txt / .md / .pdf 等</span>
        </div>
      </van-uploader>
    </div>

    <van-loading v-if="docs.loading && !docs.uploadStage" class="loading" />

    <van-empty
      v-else-if="docs.documents.length === 0"
      image="search"
      description="还没有上传文档"
    >
      <div class="empty-hint">上传 .txt、.md、.pdf 等文件构建知识库</div>
    </van-empty>

    <div v-else class="doc-list">
      <div class="doc-list-head">
        <div class="m-count-pill"><van-icon name="description" />共 {{ docs.documents.length }} 个文档</div>
        <span class="doc-list-tip"><van-icon name="wap-nav" />左滑可删除</span>
      </div>
      <div class="doc-list-body">
        <van-swipe-cell v-for="doc in docs.documents" :key="doc.id" class="doc-swipe">
          <div class="doc-item">
            <div
              class="m-type-block"
              :style="{ background: typeMeta(doc.filename).soft, color: typeMeta(doc.filename).color }"
            >{{ typeMeta(doc.filename).label }}</div>
            <div class="doc-main">
              <div class="doc-name">{{ doc.filename }}</div>
              <div class="doc-meta">
                <span class="doc-meta-item">{{ formatSize(doc.size) }}</span>
                <span class="doc-meta-dot">·</span>
                <span class="doc-meta-item">{{ doc.chunk_count }} 分块</span>
              </div>
              <div class="doc-date">{{ formatDate(doc.created_at) }}</div>
            </div>
            <van-icon name="arrow" class="doc-arrow" />
          </div>
          <template #right>
            <div class="swipe-delete" @click="handleDelete(doc.id)">
              <van-icon name="delete-o" />
              删除
            </div>
          </template>
        </van-swipe-cell>
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-docs { padding: 0 12px 16px; }

/* ── 渐变上传卡 ── */
.upload-wrap { padding: 12px 0 4px; }
.upload-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 15px 12px;
  border-radius: var(--m-card-radius);
  background: var(--m-brand-grad);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  box-shadow: 0 6px 16px rgba(109, 94, 241, 0.28);
}
.upload-btn .van-icon { font-size: 18px; }
.upload-sub { font-size: 11px; font-weight: 400; opacity: 0.85; }

.loading { display: flex; justify-content: center; padding: 48px 0; }
.empty-hint { font-size: 13px; color: var(--text-secondary); margin-top: 4px; }

/* ── 文档列表（统一白底 + 细分隔线，非悬浮卡）── */
.doc-list { display: flex; flex-direction: column; margin-top: 10px; }
.doc-list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px 10px;
}
.doc-list-tip { font-size: 11px; color: var(--text-tertiary, #94a3b8); display: inline-flex; align-items: center; gap: 3px; }
.doc-list-body {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  overflow: hidden;
}
.doc-swipe { border-radius: 0; }
.doc-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px;
  background: transparent;
  border-bottom: 1px solid var(--border, #eef1f6);
}
.doc-item:last-child { border-bottom: none; }
.doc-main { flex: 1; min-width: 0; }
.doc-name {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--text);
  word-break: break-all;
  line-height: 1.4;
}
.doc-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  margin-top: 4px;
}
.doc-meta-item { color: var(--m-brand, #6d5ef1); font-weight: 600; }
.doc-meta-dot { color: var(--text-tertiary, #c0c6d2); }
.doc-date {
  font-size: 11px;
  color: var(--text-tertiary, #94a3b8);
  margin-top: 3px;
}
.doc-arrow { color: #c3c9d4; font-size: 15px; flex-shrink: 0; }
.swipe-delete {
  width: 76px;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  background: linear-gradient(180deg, #f43f5e, #e11d48);
  color: #fff;
  font-size: 12px;
}
.swipe-delete .van-icon { font-size: 18px; }
</style>
