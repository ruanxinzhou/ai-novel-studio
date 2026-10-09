// 全局当前项目（localStorage 持久化，刷新不丢）
import { ref, watch } from 'vue'

const KEY = 'novel-studio.current-project'

export const currentProjectId = ref<string>(localStorage.getItem(KEY) ?? '')

watch(currentProjectId, (v) => {
  if (v) localStorage.setItem(KEY, v)
  else localStorage.removeItem(KEY)
})
