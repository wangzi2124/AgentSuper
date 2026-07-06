import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Document } from '../types'
import { listDocuments, uploadDocument, deleteDocument } from '../api/documents'

export const useDocumentStore = defineStore('documents', () => {
  const documents = ref<Document[]>([])
  const loading = ref(false)
  const uploadProgress = ref(0)
  const uploadStage = ref('')
  const deletingId = ref<string | null>(null)

  async function fetchAll() {
    loading.value = true
    try {
      const res = await listDocuments()
      documents.value = res.documents
    } finally {
      loading.value = false
    }
  }

  async function upload(file: File) {
    uploadProgress.value = 0
    uploadStage.value = 'Starting upload'
    try {
      const doc = await uploadDocument(file, (pct, stage) => {
        uploadProgress.value = pct
        uploadStage.value = stage
      })
      documents.value.unshift(doc)
      return doc
    } finally {
      uploadStage.value = ''
    }
  }

  async function remove(id: string) {
    deletingId.value = id
    try {
      await deleteDocument(id)
      documents.value = documents.value.filter((d) => d.id !== id)
    } finally {
      deletingId.value = null
    }
  }

  return { documents, loading, uploadProgress, uploadStage, deletingId, fetchAll, upload, remove }
})
