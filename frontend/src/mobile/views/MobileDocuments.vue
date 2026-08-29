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
    <van-uploader
      :after-read="(item: any) => handleUpload(item.file as File)"
      :max-count="1"
      accept="*"
    >
      <div class="upload-btn">＋ 上传文档</div>
    </van-uploader>

    <van-loading v-if="docs.loading && !docs.uploadStage" class="loading" />

    <van-empty
      v-else-if="docs.documents.length === 0"
      image="search"
      description="还没有上传文档"
    >
      <div class="empty-hint">上传 .txt、.md、.pdf 等文件构建知识库</div>
    </van-empty>

    <div v-else class="doc-list">
      <div class="doc-count">共 {{ docs.documents.length }} 个文档</div>
      <van-swipe-cell v-for="doc in docs.documents" :key="doc.id">
        <van-cell
          :title="doc.filename"
          :label="`${formatSize(doc.size)} · ${doc.chunk_count} 分块 · ${formatDate(doc.created_at)}`"
          icon="description"
        >
          <template #icon>
            <div class="doc-icon">📄</div>
          </template>
        </van-cell>
        <template #right>
          <div class="swipe-delete" @click="handleDelete(doc.id)">删除</div>
        </template>
      </van-swipe-cell>
    </div>
  </div>
</template>

<style scoped>
.m-docs { padding: 4px 12px 16px; }
.upload-btn {
  margin: 12px 0;
  padding: 12px;
  text-align: center;
  font-size: 15px;
  color: var(--indigo);
  background: var(--primary-soft);
  border-radius: 12px;
  font-weight: 600;
}
.loading { display: flex; justify-content: center; padding: 48px 0; }
.empty-hint { font-size: 13px; color: #97a0b4; margin-top: 4px; }
.doc-list { display: flex; flex-direction: column; gap: 4px; }
.doc-count { font-size: 12px; color: #97a0b4; padding: 6px 4px; }
.doc-icon { margin-right: 10px; font-size: 22px; }
.swipe-delete {
  width: 64px;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #ee0a24;
  color: #fff;
  font-size: 14px;
}
</style>
