import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

const AUTO_TTS_KEY = 'agent_super_auto_tts'

// 聊天相关设置（桌面端在设置抽屉、移动端在菜单「设置」中共享）
export const useChatSettingsStore = defineStore('chatSettings', () => {
  let init = false
  try { init = localStorage.getItem(AUTO_TTS_KEY) === '1' } catch { /* noop */ }

  // 自动朗读回复
  const autoRead = ref(init)
  watch(autoRead, v => {
    try { localStorage.setItem(AUTO_TTS_KEY, v ? '1' : '0') } catch { /* noop */ }
  })

  return { autoRead }
})
