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
import DungeonScaffoldView from './DungeonScaffoldView.vue'
import RhythmView from './RhythmView.vue'
import DeskView from './DeskView.vue'
import SettleView from './SettleView.vue'
import InterludeView from './InterludeView.vue'
import ChapterBriefModal from '../components/ChapterBriefModal.vue'
import { deskApi, volumesApi, adaptApi } from '../api'
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
const spineVolumes = ref<any[]>([])
// 外部触发 StatusBar 刷新污染值（杀青后使用）
const statusRefreshKey = ref(0)
// 触发 SpineNav 重载
const spineVolumesRefreshKey = ref(0)

const phase = computed(() => currentPhase.value)
const phaseLabel = computed(() => (PHASE_LABELS[phase.value] ?? phase.value) || '—')

// ---- Desk 容器（briefing/desk 共用）----
const briefModalVisible = ref(false)
const deskKey = ref(0) // 用于章节切换后强制刷新 DeskView
const existingBrief = ref<any | null>(null)

async function refreshDeskBrief() {
  // 读取当前章的 brief，用于重新打开 modal 时显示已选
  const pid = currentProjectId.value
  const cn = currentChapterNo.value
  if (!pid || !cn) {
    existingBrief.value = null
    return
  }
  try {
    const d = await deskApi.chapter(pid, cn)
    existingBrief.value = (d.chapter as any)?.brief || null
  } catch {
    existingBrief.value = null
  }
}

function onReopenBrief() {
  refreshDeskBrief().finally(() => {
    briefModalVisible.value = true
  })
}

function onBriefSelected() {
  briefModalVisible.value = false
  loadPhase()
}

function onNextChapter(no: number) {
  currentChapterNo.value = no
  deskKey.value += 1
  // 新章自动弹 brief modal
  existingBrief.value = null
  briefModalVisible.value = true
}

function onToSettle() {
  loadPhase()
}

// phase 切到 briefing 时自动弹 brief modal
watch(
  () => currentPhase.value,
  (p) => {
    if (p === 'briefing') {
      existingBrief.value = null
      briefModalVisible.value = true
    } else if (p === 'desk') {
      briefModalVisible.value = false
      refreshDeskBrief()
      deskKey.value += 1
    }
  },
  { immediate: false },
)

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
    // 阶段变化后刷新书脊 & 污染值
    spineVolumesRefreshKey.value += 1
    statusRefreshKey.value += 1
  } catch (e: any) {
    console.error('[HomeView] loadPhase failed:', e)
    ElMessage.error('加载项目阶段失败：' + (e?.message ?? e))
  } finally {
    loading.value = false
  }
}

// ---- SpineNav 书脊加载 ----
async function loadSpine() {
  const pid = currentProjectId.value
  if (!pid) {
    spineVolumes.value = []
    return
  }
  try {
    // 并发拉 volumes 列表 + desk 详情
    const [volRes, deskRes] = await Promise.all([
      volumesApi.list(pid).catch(() => ({ volumes: [], total: 0 })),
      deskApi.detail(pid).catch(() => ({ chapters: [], volumes: [] })),
    ])
    const vols = (volRes as any).volumes ?? []
    const chapters = (deskRes as any).chapters ?? []
    // 按 field_no 把章分组到卷；volume_no → chapters[]
    const volsByField = new Map<number, any>()
    for (const v of vols) {
      volsByField.set(v.field_no, v)
    }
    // 兼容：若没 field_no（老数据），按 volume_no 顺序归组
    const orderedVols = [...vols].sort((a: any, b: any) => a.volume_no - b.volume_no)
    const chaptersByVol = new Map<number, any[]>()
    let interludeFlag = new Set<number>()
    for (const v of orderedVols) {
      if (v.volume_kind === 'interlude') interludeFlag.add(v.volume_no)
      chaptersByVol.set(v.volume_no, [])
    }
    // 建立 field_no → volume_no 的映射
    const fieldToVolNo = new Map<number, number>()
    for (const v of orderedVols) {
      if (v.field_no != null) fieldToVolNo.set(v.field_no, v.volume_no)
    }
    for (const c of chapters) {
      let targetVol = 0
      if (c.field_no != null && fieldToVolNo.has(c.field_no)) {
        targetVol = fieldToVolNo.get(c.field_no)!
      } else {
        // fallback：把章按数量近似分配到最后一卷
        if (orderedVols.length > 0) targetVol = orderedVols[orderedVols.length - 1].volume_no
      }
      if (targetVol > 0) {
        if (!chaptersByVol.has(targetVol)) chaptersByVol.set(targetVol, [])
        chaptersByVol.get(targetVol)!.push(c)
      }
    }
    // 再拉每个 volume 的详细信息拿 contamination & 标题
    const detailedVols = await Promise.all(
      orderedVols.map(async (v: any) => {
        try {
          const full = await adaptApi.getVolume(pid, v.volume_no)
          return { ...v, ...(full.volume || {}) }
        } catch {
          return v
        }
      }),
    )
    spineVolumes.value = detailedVols.map((v: any) => {
      const chs = (chaptersByVol.get(v.volume_no) || []).sort((a: any, b: any) => a.no - b.no)
      const isInterlude = v.volume_kind === 'interlude'
      return {
        no: v.volume_no,
        title: v.title || (isInterlude ? '蓝星间章' : '新副本'),
        icon: isInterlude ? '🌌' : '📕',
        contamination:
          v.settle?.contamination_after ??
          v.settle?.contamination_delta ??
          v.protagonist_snapshot?.contamination_level ??
          0,
        chapters: chs.map((c: any) => ({
          no: c.no,
          title: c.title || '（未命名）',
          finalized: !!c.finalized_at,
          brief: (c.outline_preview || '').slice(0, 40),
        })),
      }
    })
  } catch (e: any) {
    console.debug('[HomeView] loadSpine failed:', e)
  }
}

watch(spineVolumesRefreshKey, loadSpine)

async function onAddVolume() {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const d = await volumesApi.createDungeon(pid, '')
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
  console.debug('[SpineNav] selectVolume', no)
  ElMessage.info('卷目浏览暂不支持切回旧卷，将在后续版本开放')
}
function onSelectChapter(vn: number, cn: number) {
  console.debug('[SpineNav] selectChapter', vn, cn)
  ElMessage.info('卷目浏览暂不支持切回旧卷，将在后续版本开放')
}

function onChatRefresh() {
  console.debug('[AIChatFloat] refresh requested')
  ElMessage.info('已请求重新生成')
}

onMounted(() => {
  if (currentProjectId.value) {
    loadPhase()
  }
})

watch(currentProjectId, () => {
  if (currentProjectId.value) loadPhase()
  else {
    currentPhase.value = ''
    currentVolumeNo.value = 0
    currentChapterNo.value = 0
    spineVolumes.value = []
  }
})

watch(() => currentVolumeNo.value, () => {
  spineVolumesRefreshKey.value += 1
})

defineExpose({ refresh: loadPhase })
</script>

<template>
  <div class="home-view">
    <StatusBar :book-title="bookTitle" :refresh-key="statusRefreshKey" />
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

          <!-- scaffold 阶段：副本详设 6 卡 -->
          <DungeonScaffoldView
            v-else-if="phase === 'scaffold'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
          />

          <!-- rhythm 阶段：节奏骨架 -->
          <RhythmView
            v-else-if="phase === 'rhythm'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
          />

          <!-- briefing / desk 阶段：章前 5 分钟卡 Modal + 章台 -->
          <div v-else-if="phase === 'briefing' || phase === 'desk'" class="desk-container">
            <div v-if="phase === 'briefing' && !briefModalVisible" class="briefing-placeholder">
              <div class="briefing-placeholder-icon">📝</div>
              <h2>章前 5 分钟</h2>
              <p>开写前花 5 分钟选一张方向卡，本章会更稳</p>
              <el-button type="primary" @click="briefModalVisible = true">开始选方向</el-button>
            </div>
            <DeskView
              v-else
              :key="deskKey"
              :chapter-no="currentChapterNo || undefined"
              @reopen-brief="onReopenBrief"
              @next-chapter="onNextChapter"
              @to-settle="onToSettle"
            />
            <ChapterBriefModal
              v-model:visible="briefModalVisible"
              :pid="currentProjectId"
              :vol-no="currentVolumeNo"
              :chapter-no="currentChapterNo || 1"
              :existing-brief="existingBrief"
              @selected="onBriefSelected"
            />
          </div>

          <!-- settle 阶段：杀青归账 6 步 -->
          <SettleView
            v-else-if="phase === 'settle'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
          />

          <!-- interlude 阶段：蓝星间章 4 步 -->
          <InterludeView
            v-else-if="phase === 'interlude'"
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
    <AIChatFloat @refresh="onChatRefresh" />
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
.desk-container {
  min-height: 100%;
}
.briefing-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 60vh;
  text-align: center;
  color: #606266;
}
.briefing-placeholder-icon {
  font-size: 56px;
  margin-bottom: 12px;
}
.briefing-placeholder h2 {
  margin: 0 0 8px;
  font-size: 20px;
  color: #303133;
}
.briefing-placeholder p {
  color: #909399;
  margin: 0 0 20px;
}
</style>
