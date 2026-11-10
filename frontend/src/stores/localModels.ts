import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  cancelLocalDownload,
  createLocalDownload,
  deleteLocalDownload,
  fetchHubFiles,
  fetchLocalDownloads,
  fetchLocalModelSources,
  searchHubModels,
  type HubFile,
  type HubModel,
  type LocalDownloadJob,
} from '../api/localModels'
import { useModelManagerStore } from './modelManager'

const ACTIVE_STATES = new Set(['pending', 'downloading', 'importing'])

// 本地模型下载：搜索 ModelScope/HuggingFace → 选择 GGUF → 下载并导入 Ollama。
// P1 用轮询（1.5s）驱动进度；导入完成后刷新模型管理目录。
export const useLocalModelsStore = defineStore('localModels', () => {
  const endpoints = ref<Record<string, string>>({})
  const priority = ref<string[]>(['modelscope', 'huggingface'])
  const query = ref('')
  const source = ref('auto')
  const results = ref<HubModel[]>([])
  const searching = ref(false)
  const repo = ref<HubModel | null>(null)
  const files = ref<HubFile[]>([])
  const filesLoading = ref(false)
  const selectedFile = ref('')
  const jobs = ref<LocalDownloadJob[]>([])
  const busyId = ref('')
  const error = ref('')

  let timer: ReturnType<typeof setInterval> | null = null
  let primed = false
  const doneSeen = new Set<string>()

  const activeJobs = computed(() => jobs.value.filter(j => ACTIVE_STATES.has(j.state)))
  const selectedEntry = computed(() => files.value.find(f => f.file === selectedFile.value) || null)

  function setError(e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  }

  async function init() {
    try {
      const info = await fetchLocalModelSources()
      endpoints.value = info.endpoints || {}
      priority.value = info.priority || priority.value
    } catch (e) {
      setError(e)
    }
    await refreshJobs()
  }

  async function search() {
    const q = query.value.trim()
    if (!q) return
    searching.value = true
    error.value = ''
    results.value = []
    repo.value = null
    files.value = []
    selectedFile.value = ''
    try {
      results.value = await searchHubModels(q, source.value, 20)
    } catch (e) {
      setError(e)
    } finally {
      searching.value = false
    }
  }

  async function openRepo(model: HubModel) {
    repo.value = model
    files.value = []
    selectedFile.value = ''
    filesLoading.value = true
    error.value = ''
    try {
      files.value = await fetchHubFiles(model.id, model.source)
      selectedFile.value = files.value[0]?.file || ''
    } catch (e) {
      setError(e)
    } finally {
      filesLoading.value = false
    }
  }

  async function startDownload() {
    if (!repo.value || !selectedEntry.value) return
    const entry = selectedEntry.value
    busyId.value = entry.file
    error.value = ''
    try {
      const job = await createLocalDownload({
        repo_id: repo.value.id,
        file: entry.file,
        files: entry.files,
        size: entry.size,
        source: entry.source,
        quantization: entry.quantization,
      })
      jobs.value = [job, ...jobs.value.filter(j => j.id !== job.id)]
      startPolling()
    } catch (e) {
      setError(e)
    } finally {
      busyId.value = ''
    }
  }

  function applyJobs(list: LocalDownloadJob[]) {
    jobs.value = list
    if (!primed) {
      list.forEach(j => { if (j.state === 'done') doneSeen.add(j.id) })
      primed = true
    }
    for (const job of list) {
      if (job.state === 'done' && !doneSeen.has(job.id)) {
        doneSeen.add(job.id)
        const mm = useModelManagerStore()
        void mm.load(false, false)
        mm.setNotice(`本地模型已导入：${job.entry_id || job.file}`)
      }
    }
    if (list.some(j => ACTIVE_STATES.has(j.state))) startPolling()
    else stopPolling()
  }

  async function refreshJobs() {
    try {
      applyJobs(await fetchLocalDownloads())
    } catch (e) {
      setError(e)
    }
  }

  function startPolling() {
    if (timer) return
    timer = setInterval(() => { void refreshJobs() }, 1500)
  }

  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  async function cancel(job: LocalDownloadJob) {
    busyId.value = job.id
    try {
      await cancelLocalDownload(job.id)
      await refreshJobs()
    } catch (e) {
      setError(e)
    } finally {
      busyId.value = ''
    }
  }

  async function remove(job: LocalDownloadJob) {
    busyId.value = job.id
    try {
      await deleteLocalDownload(job.id, true)
      jobs.value = jobs.value.filter(j => j.id !== job.id)
      doneSeen.delete(job.id)
    } catch (e) {
      setError(e)
    } finally {
      busyId.value = ''
    }
  }

  return {
    endpoints, priority, query, source, results, searching, repo, files, filesLoading,
    selectedFile, selectedEntry, jobs, activeJobs, busyId, error,
    init, search, openRepo, startDownload, refreshJobs, cancel, remove,
    startPolling, stopPolling,
  }
})
