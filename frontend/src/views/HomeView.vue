<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import StatusBar from '../components/StatusBar.vue'
import SpineNav from '../components/SpineNav.vue'
import SettingsDrawer from '../components/SettingsDrawer.vue'
import AIChatFloat from '../components/AIChatFloat.vue'
import BookSetupView from './BookSetupView.vue'
import ModuleAdaptView from './ModuleAdaptView.vue'
import CocSimView from './CocSimView.vue'
import EmbodyView from './EmbodyView.vue'
import {
  currentProjectId,
  currentPhase,
  currentVolumeNo,
  currentChapterNo,
  currentVolumeKind,
  currentIsLastChapter,
} from '../state'

const PHASE_LABELS: Record<string, string> = {
  setup: '开书设定',
  adapt: '剧本改编',
  adapt_empty: '新卷',
  simulate: '模拟跑团',
  embody: '魂穿设计',
  scaffold: '卷内详设',
  rhythm: '节奏骨架',
  briefing: '章前规划',
  desk: '日更',
  settle: '杀青',
  interlude: '蓝星间章',
}

const bookTitle = ref('')
const loading = ref(false)
// TODO: Task 10 接入书脊真实数据
const spineVolumes = ref<any[]>([])
// TODO: Task 11 接 protagonist API 读取真实污染值
const contamination = ref<number>(0)

const phase = computed(() => currentPhase.value)
const phaseLabel = computed(() => (PHASE_LABELS[phase.value] ?? phase.value) || '—')

async function loadPhase() {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  try {
    const r = await fetch(`/api/projects/${pid}/phase`)
    if (!r.ok) throw new Error(`phase ${r.status}`)
    const d = await r.json()
    // 返回字段预期：{phase, volume_no, chapter_no, volume_kind, is_last_chapter, book_title?}
    currentPhase.value = d.phase ?? ''
    currentVolumeNo.value = d.volume_no ?? 0
    currentChapterNo.value = d.chapter_no ?? 0
    currentVolumeKind.value = d.volume_kind ?? 'dungeon'
    currentIsLastChapter.value = !!d.is_last_chapter
    if (d.book_title) bookTitle.value = d.book_title
  } catch (e: any) {
    console.error('[HomeView] loadPhase failed:', e)
    ElMessage.error('加载项目阶段失败：' + (e?.message ?? e))
  } finally {
    loading.value = false
  }
}

async function onAddVolume() {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const r = await fetch(`/api/projects/${pid}/workbench/volumes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: '' }),
    })
    if (!r.ok) {
      const detail = await r.json().catch(() => ({}))
      throw new Error(detail.detail ?? `${r.status}`)
    }
    const d = await r.json()
    ElMessage.success(`已创建第 ${d.volume_no} 卷`)
    await loadPhase()
  } catch (e: any) {
    ElMessage.error('创建新卷失败：' + (e?.message ?? e))
  }
}

function onGoSetup() {
  currentPhase.value = 'setup'
}

function onSelectVolume(no: number) {
  console.log('[SpineNav] selectVolume', no)
}
function onSelectChapter(vn: number, cn: number) {
  console.log('[SpineNav] selectChapter', vn, cn)
}

onMounted(() => {
  if (currentProjectId.value) loadPhase()
})

watch(currentProjectId, () => {
  if (currentProjectId.value) loadPhase()
  else {
    currentPhase.value = ''
    currentVolumeNo.value = 0
    currentChapterNo.value = 0
  }
})

defineExpose({ refresh: loadPhase })
</script>

<template>
  <div class="home-view">
    <StatusBar :book-title="bookTitle" :contamination="contamination" />
    <div class="home-body">
      <SpineNav
        :volumes="spineVolumes"
        :active-volume-no="currentVolumeNo"
        :active-chapter-no="currentChapterNo"
        @select-volume="onSelectVolume"
        @select-chapter="onSelectChapter"
        @add-volume="onAddVolume"
        @go-setup="onGoSetup"
      />
      <main class="home-main">
        <div v-if="loading" class="home-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>加载中…</span>
        </div>
        <template v-else>
          <!-- setup 阶段：开书设定面板 -->
          <BookSetupView v-if="phase === 'setup'" @done="loadPhase" />

          <!-- adapt / adapt_empty 阶段：上传 PDF + 并行改编 + 选版 -->
          <ModuleAdaptView
            v-else-if="phase === 'adapt' || phase === 'adapt_empty'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
          />

          <!-- simulate 阶段：3场并行 COC 跑团 + 单选/拼贴 -->
          <CocSimView
            v-else-if="phase === 'simulate'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
          />

          <!-- embody 阶段：唐迟魂穿 4 步 -->
          <EmbodyView
            v-else-if="phase === 'embody'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
          />

          <!-- 其他阶段：占位提示 -->
          <div v-else class="placeholder-view">
            <div class="placeholder-icon">🚧</div>
            <h2>当前阶段：{{ phaseLabel }}</h2>
            <p>该阶段视图正在开发中，敬请期待。</p>
          </div>
        </template>
      </main>
    </div>

    <SettingsDrawer />
    <AIChatFloat />
  </div>
</template>

<style scoped>
.home-view {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}
.home-body {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.home-main {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}
.home-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  color: #909399;
  font-size: 14px;
}
.empty-state,
.placeholder-view {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: #606266;
}
.empty-icon,
.placeholder-icon {
  font-size: 56px;
  margin-bottom: 16px;
}
.empty-state h2,
.placeholder-view h2 {
  margin: 0 0 8px;
  font-size: 20px;
  color: #303133;
}
.empty-desc {
  margin: 0 0 24px;
  color: #909399;
}
.placeholder-view p {
  color: #909399;
}
</style>
