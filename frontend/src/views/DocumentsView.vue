<script setup lang="ts">
import { onMounted } from 'vue'
import { useDocumentStore } from '../stores/documents'
import DocumentCard from '../components/DocumentCard.vue'
import FileUpload from '../components/FileUpload.vue'

const docs = useDocumentStore()

onMounted(() => {
  docs.fetchAll()
})

async function handleUpload(file: File) {
  try {
    await docs.upload(file)
  } catch (err: any) {
    alert(err.message)
  }
}

async function handleDelete(id: string) {
  if (confirm('Delete this document?')) {
    try {
      await docs.remove(id)
    } catch (err: any) {
      alert(err.message)
    }
  }
}
</script>

<template>
  <div class="page-header">
    <h2>文档管理</h2>
    <p>上传并管理你的知识库文档</p>
  </div>
  <div class="page-content">
    <FileUpload :loading="docs.loading" :progress="docs.uploadProgress" :stage="docs.uploadStage" @upload="handleUpload" />

    <div v-if="docs.loading && !docs.uploadStage" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="docs.documents.length === 0" class="empty-state">
      <div class="icon">📄</div>
      <p>还没有上传文档</p>
      <p style="font-size:13px;margin-top:4px;">上传 .txt、.md 或 .pdf 文件来构建你的知识库。</p>
    </div>

    <div v-else class="doc-list">
      <div class="list-meta">
        <span>{{ docs.documents.length }} 个文档</span>
      </div>
      <DocumentCard
        v-for="doc in docs.documents"
        :key="doc.id"
        :doc="doc"
        :deleting="docs.deletingId === doc.id"
        @delete="handleDelete"
      />
    </div>
  </div>
</template>

<style scoped>
.loading-wrap { display: flex; justify-content: center; padding: 48px; }
.doc-list { margin-top: 24px; display: flex; flex-direction: column; gap: 10px; }
.list-meta {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}
</style>
