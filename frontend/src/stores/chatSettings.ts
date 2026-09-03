import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const AUTO_TTS_KEY = 'agent_super_auto_tts'
const TTS_LANG_KEY = 'agent_super_tts_lang'

// 与后端 app/services/voice.py LANGUAGES 保持一致
export const TTS_LANGUAGES = [
  { label: '自动检测', value: 'Auto' },
  { label: '中文', value: 'Chinese' },
  { label: '英语', value: 'English' },
  { label: '日语', value: 'Japanese' },
  { label: '韩语', value: 'Korean' },
  { label: '法语', value: 'French' },
  { label: '德语', value: 'German' },
  { label: '西班牙语', value: 'Spanish' },
  { label: '葡萄牙语', value: 'Portuguese' },
  { label: '俄语', value: 'Russian' },
  { label: '意大利语', value: 'Italian' },
]

// 聊天相关设置（桌面端在设置抽屉、移动端在菜单「设置」中共享）
export const useChatSettingsStore = defineStore('chatSettings', () => {
  let init = false
  try { init = localStorage.getItem(AUTO_TTS_KEY) === '1' } catch { /* noop */ }

  // 自动朗读回复
  const autoRead = ref(init)
  watch(autoRead, v => {
    try { localStorage.setItem(AUTO_TTS_KEY, v ? '1' : '0') } catch { /* noop */ }
  })

  // 朗读语言
  let langInit = 'Auto'
  try {
    const raw = localStorage.getItem(TTS_LANG_KEY)
    if (raw && TTS_LANGUAGES.some(l => l.value === raw)) langInit = raw
  } catch { /* noop */ }
  const ttsLang = ref(langInit)
  watch(ttsLang, v => {
    try { localStorage.setItem(TTS_LANG_KEY, v) } catch { /* noop */ }
  })

  return { autoRead, ttsLang }
})
