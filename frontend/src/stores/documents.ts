import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Document } from '../types'
import { listDocuments, uploadDocument, deleteDocument } from '../api/documents'

// 文档管理 Store
export const useDocumentStore = defineStore('documents', () => {
  // 文档列表
  const documents = ref<Document[]>([])
  // 加载状态
  const loading = ref(false)
  // 上传进度百分比
  const uploadProgress = ref(0)
  // 上传阶段描述
  const uploadStage = ref('')
  // 正在删除的文档 ID
  const deletingId = ref<string | null>(null)

  // 获取所有文档
  async function fetchAll() {
    loading.value = true
    try {
      const res = await listDocuments()
      documents.value = res.documents
    } finally {
      loading.value = false
    }
  }

  // 上传文档并监听进度
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

  // 删除指定文档
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
