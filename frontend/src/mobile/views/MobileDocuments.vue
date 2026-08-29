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
      <div class="m-count-pill"><van-icon name="description" />共 {{ docs.documents.length }} 个文档</div>
      <div v-for="doc in docs.documents" :key="doc.id" class="m-card doc-item">
        <div
          class="m-type-block"
          :style="{ background: typeMeta(doc.filename).soft, color: typeMeta(doc.filename).color }"
        >{{ typeMeta(doc.filename).label }}</div>
        <div class="doc-main">
          <div class="doc-name">{{ doc.filename }}</div>
          <div class="doc-meta">{{ formatSize(doc.size) }} · {{ doc.chunk_count }} 分块 · {{ formatDate(doc.created_at) }}</div>
        </div>
        <button class="doc-del" @click="handleDelete(doc.id)" aria-label="删除">
          <van-icon name="delete-o" />
        </button>
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

/* ── 文档列表（与插件列表同款紧凑卡片）── */
.doc-list { display: flex; flex-direction: column; gap: 10px; margin-top: 10px; }
.doc-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px;
}
.m-type-block {
  width: 40px;
  height: 40px;
  border-radius: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.2px;
  flex-shrink: 0;
}
.doc-main { flex: 1; min-width: 0; }
.doc-name {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--text);
  word-break: break-all;
  line-height: 1.4;
}
.doc-meta {
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-del {
  width: 34px;
  height: 34px;
  border: none;
  border-radius: 50%;
  background: var(--m-danger-soft, rgba(244, 63, 94, 0.12));
  color: var(--m-danger, #f43f5e);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  cursor: pointer;
  flex-shrink: 0;
  transition: transform 0.1s, opacity 0.15s;
}
.doc-del:active { transform: scale(0.9); opacity: 0.75; }
</style>
