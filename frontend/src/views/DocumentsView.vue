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
    <h2>Documents</h2>
    <p>Upload and manage your knowledge base documents</p>
  </div>
  <div class="page-content">
    <FileUpload :loading="docs.loading" :progress="docs.uploadProgress" :stage="docs.uploadStage" @upload="handleUpload" />

    <div v-if="docs.loading && !docs.uploadStage" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="docs.documents.length === 0" class="empty-state">
      <div class="icon">📄</div>
      <p>No documents uploaded yet</p>
      <p style="font-size:13px;margin-top:4px;">Upload .txt, .md, or .pdf files to build your knowledge base.</p>
    </div>

    <div v-else style="margin-top:20px;display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
        {{ docs.documents.length }} document(s)
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
