// 全局当前项目（localStorage 持久化，刷新不丢）
import { ref, watch } from 'vue'

const KEY = 'novel-studio.current-project'

export const currentProjectId = ref<string>(localStorage.getItem(KEY) ?? '')

watch(currentProjectId, (v) => {
  if (v) localStorage.setItem(KEY, v)
  else localStorage.removeItem(KEY)
})

// 全局导航 tab（视图内部可跳转，如向导毕业 → 今日章台）
export const activeTab = ref('projects')
