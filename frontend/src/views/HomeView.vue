<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
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
import MainlineLandingView from './MainlineLandingView.vue'
import WritingDeskView from './WritingDeskView.vue'
import ChapterBriefModal from '../components/ChapterBriefModal.vue'
import VolumeReviewPanel from '../components/VolumeReviewPanel.vue'
import NewVolumeDialog from '../components/NewVolumeDialog.vue'
import { deskApi, volumesApi, adaptApi } from '../api'
import {
  currentProjectId,
  currentPhase,
  currentVolumeNo,
  currentChapterNo,
  currentVolumeKind,
  currentIsLastChapter,
  isReviewingVolume,
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
  'mainline-brief': '蓝星 · 章前规划',
  desk: '日更',
  settle: '杀青',
  interlude: '蓝星间章',
  'mainline-waiting': '蓝星开篇',
  'writing-desk': '全局写作台',
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

// ---- 新建卷 Dialog ----
const newVolumeDlgVisible = ref(false)
function onNewVolumeCreated(no: number) {
  newVolumeDlgVisible.value = false
  ElMessage.success(`已创建第 ${no} 卷`)
  spineVolumesRefreshKey.value += 1
  loadPhase()
}

// ---- 自由视图（非 phase 推进，手动覆盖当前面板） ----
const freeView = ref<'writing-desk' | null>(null)
function enterWritingDesk() {
  freeView.value = 'writing-desk'
}
function exitFreeView() {
  freeView.value = null
  loadPhase() // 回到当前 phase
}

// phase 切到 briefing / mainline-brief 时自动弹 brief modal
watch(
  () => currentPhase.value,
  (p) => {
    if (p === 'briefing' || p === 'mainline-brief') {
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

async function loadPhase(targetVolume?: number) {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  try {
    const url = new URL(`/api/projects/${pid}/phase`, window.location.origin)
    if (targetVolume != null && targetVolume > 0) {
      url.searchParams.set('volume', String(targetVolume))
    }
    const r = await fetch(url.toString())
    if (!r.ok) throw new Error(`phase ${r.status}`)
    const d = await r.json()
    // 返回字段预期：{phase, volume_no, chapter_no, volume_kind, is_last_chapter, is_reviewing, book_title?}
    currentPhase.value = d.phase ?? ''
    currentVolumeNo.value = d.volume_no ?? 0
    currentChapterNo.value = d.chapter_no ?? 0
    currentVolumeKind.value = d.volume_kind ?? 'dungeon'
    currentIsLastChapter.value = !!d.is_last_chapter
    isReviewingVolume.value = !!d.is_reviewing
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

// 回到"最新进度"（不带 volume 参数）
function backToLatest() {
  loadPhase()
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
      // 主线/间章优先按 chapter_kind 匹配（因为它们的 field_no 可能与 dungeon 卷号冲突）
      if (c.chapter_kind === 'mainline') {
        const ml = orderedVols.find((v: any) => v.volume_kind === 'mainline')
        if (ml) targetVol = ml.volume_no
      } else if (c.is_interlude) {
        // 匹配一个 interlude 卷：优先按 field_no，否则取最后一个 interlude 卷
        const cand = orderedVols.find((v: any) => v.volume_kind === 'interlude' && v.field_no === c.field_no)
          || [...orderedVols].reverse().find((v: any) => v.volume_kind === 'interlude')
        if (cand) targetVol = cand.volume_no
      } else if (c.field_no != null && fieldToVolNo.has(c.field_no)) {
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
  newVolumeDlgVisible.value = true
}

async function onDeleteVolume(no: number) {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    await ElMessageBox.confirm(
      `确定删除第 ${no} 卷？该卷下所有章节（含未定稿内容）都会被删除，此操作不可恢复。`,
      '删除卷',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    const r = await volumesApi.remove(pid, no)
    ElMessage.success('已删除')
    spineVolumesRefreshKey.value += 1
    if (r.phase) {
      currentPhase.value = r.phase.phase ?? ''
      currentVolumeNo.value = r.phase.volume_no ?? 0
      currentChapterNo.value = r.phase.chapter_no ?? 0
      currentVolumeKind.value = r.phase.volume_kind ?? 'dungeon'
      currentIsLastChapter.value = !!r.phase.is_last_chapter
      isReviewingVolume.value = !!r.phase.is_reviewing
    } else {
      await loadPhase()
    }
  } catch (e: any) {
    ElMessage.error(`删除失败：${e?.message ?? e}`)
  }
}

function onGoSetup() {
  currentPhase.value = 'setup'
}

// mainline 创建/跳过/新建卷后都需要刷新 spine 列表
async function onMainlineDone() {
  spineVolumesRefreshKey.value += 1
  await loadPhase()
}

function onSelectVolume(no: number) {
  loadPhase(no)
}
function onSelectChapter(vn: number, _cn: number) {
  // 切到该卷，后端会自动定位到该章或阶段
  loadPhase(vn)
}

function onChatRefresh() {
  console.debug('[AIChatFloat] refresh requested')
  ElMessage.info('已请求重新生成')
}

async function onBackToAdapt() {
  const pid = currentProjectId.value
  const vn = currentVolumeNo.value
  if (!pid || !vn) return
  try {
    // 调用后端专用接口：清空 simulated_run、重置 stage_completed.simulate 为 False、取消运行中任务
    const resp = await fetch(`/api/projects/${pid}/workbench/volumes/${vn}/simulate/back-to-adapt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    })
    if (!resp.ok) {
      const t = await resp.text().catch(() => '')
      throw new Error(`HTTP ${resp.status} ${t.slice(0, 120)}`)
    }
    await loadPhase(vn)
    ElMessage.success('已返回剧本改编阶段，可以重新选版')
  } catch (e: any) {
    ElMessage.error('返回失败：' + (e?.message ?? e))
  }
}

async function onBackToSimulate() {
  // EmbodyView 内部已经 POST 过 back-to-simulate 端点清了魂穿产出，
  // 这里只要按当前卷号刷新阶段状态即可，不能 loadPhase()（会跳到最新活跃卷）。
  const vn = currentVolumeNo.value
  if (!vn) {
    await loadPhase()
    return
  }
  await loadPhase(vn)
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
        @delete-volume="onDeleteVolume"
        @go-setup="onGoSetup"
        @open-desk="enterWritingDesk"
      />
      <main class="home-main">
        <!-- 自由视图覆盖：手动进入（如写作台），顶部带返回按钮 -->
        <div v-if="freeView === 'writing-desk'" class="free-view-wrap">
          <div class="free-view-bar">
            <el-button size="small" @click="exitFreeView">⬅ 返回工作流</el-button>
            <span class="free-view-title">全局写作台</span>
          </div>
          <WritingDeskView />
        </div>
        <template v-else>
        <div v-if="loading" class="home-loading">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>加载中…</span>
        </div>
        <template v-else>
          <!-- setup 阶段：开书设定面板 -->
          <BookSetupView v-if="phase === 'setup'" @done="loadPhase" />

          <!-- mainline-waiting: 蓝星开篇引导页 -->
          <MainlineLandingView v-else-if="phase === 'mainline-waiting'" @done="onMainlineDone" />

          <!-- writing-desk: 全局写作台（phase 推进进入） -->
          <WritingDeskView v-else-if="phase === 'writing-desk'" />

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
            @back-to-adapt="onBackToAdapt"
          />

          <!-- embody 阶段：唐迟魂穿 4 步 -->
          <EmbodyView
            v-else-if="phase === 'embody'"
            :vol-no="currentVolumeNo"
            @done="loadPhase"
            @back-to-simulate="onBackToSimulate"
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

          <!-- briefing / desk / mainline-brief 阶段：章前 5 分钟卡 Modal + 章台 -->
          <div v-else-if="phase === 'briefing' || phase === 'desk' || phase === 'mainline-brief'" class="desk-container">
            <div v-if="(phase === 'briefing' || phase === 'mainline-brief') && !briefModalVisible" class="briefing-placeholder">
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

          <!-- 回看旧卷：只读模式 -->
          <div v-else-if="isReviewingVolume" class="review-view">
            <el-alert
              type="info"
              :closable="false"
              show-icon
              title="旧卷回看"
              description="你正在查看历史卷。该卷内容只读，无法修改。点下方按钮回到最新进度。"
              style="margin-bottom:16px"
            />
            <VolumeReviewPanel :vol-no="currentVolumeNo" :phase="phase" />
            <div style="text-align:center; margin-top:24px">
              <el-button type="primary" @click="backToLatest">⬅ 回到最新进度</el-button>
            </div>
          </div>

          <!-- 其他阶段：占位提示 -->
          <div v-else class="placeholder-view">
            <div class="placeholder-icon">🚧</div>
            <h2>当前阶段：{{ phaseLabel }}</h2>
            <p>该阶段视图正在开发中，敬请期待。</p>
          </div>
        </template>
        </template>
      </main>
    </div>

    <NewVolumeDialog v-model="newVolumeDlgVisible" @created="onNewVolumeCreated" />
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
.free-view-wrap {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.free-view-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 12px;
  margin-bottom: 12px;
  border-bottom: 1px solid #ebeef5;
}
.free-view-title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}
</style>
