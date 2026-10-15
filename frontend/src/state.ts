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

// 工作台全局阶段状态（由 HomeView 在 /phase 请求后填充）
export const currentPhase = ref<string>('')
export const currentVolumeNo = ref<number>(0)
export const currentChapterNo = ref<number>(0)
export const currentVolumeKind = ref<string>('dungeon')
export const currentIsLastChapter = ref<boolean>(false)

// 抽屉开关
export const drawerOpen = ref(false)
// AI 气泡开关（预留，供外部快捷键使用；AIChatFloat 组件内部自管 open 状态）
export const aiChatOpen = ref(false)
