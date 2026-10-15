<script setup lang="ts">
// 今日章台：连载期日更微循环——剧情 → 正文 → 审稿 → 润色 → 定稿（保鲜飞轮）
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  LAYER_LABELS,
  LAYERS,
  briefApi,
  deskApi,
  projectsApi,
  refineApi,
  skillsApi,
  type ChapterReview,
  type DeskChapterDetail,
  type DeskDetail,
  type FinalizeResult,
  type ProjectMeta,
  type SkillModule,
} from '../api'
import { activeTab, currentChapterNo, currentProjectId, currentVolumeNo } from '../state'

const props = defineProps<{
  /** 指定章节号；不传则自动选 todayNo */
  chapterNo?: number
}>()

const emit = defineEmits<{
  (e: 'reopen-brief'): void
  (e: 'next-chapter', no: number): void
  (e: 'to-settle'): void
}>()

const loading = ref(false)
const overview = ref<DeskDetail | null>(null)
const currentNo = ref(0)
const detail = ref<DeskChapterDetail | null>(null)

const STAGES = [
  { key: 'plot', label: '剧情', tip: '这一场戏谁要什么、遇到什么阻力' },
  { key: 'draft', label: '正文', tip: '按剧情展开成可读的章节正文' },
  { key: 'polish', label: '润色', tip: '收束节奏、强化章尾钩子' },
] as const
type StageKey = (typeof STAGES)[number]['key']

const text = reactive<Record<StageKey, string>>({ plot: '', draft: '', polish: '' })
const directive = reactive<Record<StageKey, string>>({ plot: '', draft: '', polish: '' })
const force = reactive<Record<StageKey, boolean>>({ plot: false, draft: false, polish: false })
const thinking = reactive<Record<StageKey, string>>({ plot: '', draft: '', polish: '' })
const stageNotes = reactive<Record<StageKey, string[]>>({ plot: [], draft: [], polish: [] })
const busy = ref('')
const review = ref<ChapterReview | null>(null)
const lastFinalize = ref<FinalizeResult | null>(null)

// 兑现四选一（P2③）：本章兑现规模，作者点选攒出自家节奏量纲
const PAYOFF_OPTIONS = [
  { value: 'none', label: '无' },
  { value: 'small', label: '小爽点' },
  { value: 'medium', label: '中兑现' },
  { value: 'climax', label: '卷高潮' },
] as const
const payoffChoice = ref('')

async function onPayoffChange(val: string | number | boolean | undefined) {
  if (typeof val !== 'string') return
  payoffChoice.value = val
  if (!currentProjectId.value || !currentNo.value) return
  try {
    await deskApi.setPayoff(currentProjectId.value, currentNo.value, val)
  } catch (e) {
    ElMessage.error(`兑现记录失败：${(e as Error).message}`)
  }
}

// 三线打卡（P3③）：本章推进 主线/冲突/羁绊 哪些线——开章任务书的三线近况与审稿的断档报警都吃这份账
const STRAND_OPTIONS = [
  { value: 'quest', label: '主线' },
  { value: 'fire', label: '冲突' },
  { value: 'bond', label: '羁绊' },
] as const
const strandChoice = ref<string[]>([])

async function onStrandsChange(val: string | number | boolean | undefined) {
  if (!Array.isArray(val)) return
  strandChoice.value = val
  if (!currentProjectId.value || !currentNo.value) return
  try {
    await deskApi.setStrands(currentProjectId.value, currentNo.value, val)
  } catch (e) {
    ElMessage.error(`三线记录失败：${(e as Error).message}`)
  }
}

// 写作模块（④摘要版知识卡）：默认按层自动带摘要版省上下文；勾选点名 = 替换默认集并注入完整规则
const moduleOptions = ref<SkillModule[]>([])
const chosenModules = ref<string[]>([])
const fullModules = ref(false)

async function loadModules() {
  try {
    moduleOptions.value = (await skillsApi.list()).modules
  } catch {
    moduleOptions.value = []
  }
}

const defaultModuleTitles = (stage: string) =>
  moduleOptions.value.filter((m) => m.default_for.includes(stage)).map((m) => m.title)

// 生成中的正文框自动滚到底
const stagePanes = reactive<Record<StageKey, HTMLElement | null>>({ plot: null, draft: null, polish: null })
watch(text, () => {
  if (!busy.value) return
  const el = stagePanes[busy.value as StageKey]
  if (el) el.scrollTop = el.scrollHeight
})

const finalizedCount = computed(() => overview.value?.finalized ?? 0)
const totalCount = computed(() => overview.value?.total ?? 0)
// 今日应写：第一章未定稿的
const todayNo = computed(
  () => overview.value?.chapters.find((c) => !c.finalized_at)?.no ?? 0,
)
// 当前章的章前卡（brief）
const currentBrief = computed(() => {
  const b = (detail.value?.chapter as any)?.brief
  return b && typeof b === 'object' ? b : null
})
const currentBriefSelected = computed(() => currentBrief.value?.selected || null)

// 分页：章节列表按 30/页切，全量拉取保证「今日应写」跨页计算正确
const deskPage = ref(1)
const deskPageSize = 30
const pagedChapters = computed(() =>
  overview.value
    ? overview.value.chapters.slice((deskPage.value - 1) * deskPageSize, deskPage.value * deskPageSize)
    : [],
)
// 按卷分组：本页内卷名相同的相邻章归一组（卷跨页时下页重复组头）
const chapterGroups = computed(() => {
  const groups: { volume: string; items: typeof pagedChapters.value }[] = []
  for (const c of pagedChapters.value) {
    const last = groups[groups.length - 1]
    if (last && last.volume === c.volume) last.items.push(c)
    else groups.push({ volume: c.volume, items: [c] })
  }
  return groups
})
const volumeNotes = computed(() => {
  const m = new Map<string, string>()
  for (const v of overview.value?.volumes ?? []) if (v.note) m.set(v.title, v.note)
  return m
})

async function loadOverview(keepSelection = true) {
  if (!currentProjectId.value) {
    overview.value = null
    return
  }
  loading.value = true
  try {
    overview.value = await deskApi.detail(currentProjectId.value)
    if (!keepSelection || !currentNo.value) {
      currentNo.value = props.chapterNo || currentChapterNo.value || todayNo.value || overview.value.chapters[0]?.no || 0
    }
    if (currentNo.value && !overview.value.chapters.some((c) => c.no === currentNo.value)) {
      currentNo.value = props.chapterNo || currentChapterNo.value || overview.value.chapters[0]?.no || 0
    }
  } catch (e) {
    ElMessage.error(`章台加载失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

async function selectChapter(no: number) {
  if (busy.value) {
    ElMessage.warning('有任务进行中，请稍候')
    return
  }
  currentNo.value = no
  if (!no) {
    detail.value = null
    return
  }
  try {
    detail.value = await deskApi.chapter(currentProjectId.value, no)
    const ch = detail.value.chapter
    text.plot = ch.plot?.text ?? ''
    text.draft = ch.draft?.text ?? ''
    text.polish = ch.polish?.text ?? ''
    directive.plot = ''
    directive.draft = ''
    directive.polish = ''
    review.value = ch.review
    payoffChoice.value = ch.payoff ?? ''
    strandChoice.value = ch.strands ?? []
    lastFinalize.value = null
  } catch (e) {
    ElMessage.error(`章节加载失败：${(e as Error).message}`)
  }
}

function setStagePane(key: StageKey) {
  return (el: unknown) => {
    const comp = el as { $el?: HTMLElement } | null
    stagePanes[key] = comp?.$el?.querySelector('textarea') ?? null
  }
}

const refiningStage = ref<StageKey | ''>('')

// 迭代润色：在当前文本基础上按附加指令调整——原文被替换但未保存，不满意可直接改回或重新生成
async function refineStage(stage: StageKey) {
  if (busy.value) return
  if (!text[stage].trim()) {
    ElMessage.warning('当前没有内容可调整')
    return
  }
  if (!directive[stage].trim()) {
    ElMessage.warning('先在指令框写调整要求，例：开头放慢、对话再口语化、加强结尾悬念')
    return
  }
  busy.value = stage
  refiningStage.value = stage
  thinking[stage] = ''
  stageNotes[stage] = ['正在按要求调整当前文本——改完还没保存，不满意可手动改回']
  const original = text[stage]
  text[stage] = ''
  try {
    const r = await refineApi.stream(original, directive[stage].trim(), {
      onThinking: (t) => {
        thinking[stage] += t
      },
      onDelta: (t) => {
        text[stage] += t
      },
      onNotice: (n) => {
        stageNotes[stage].push(n)
      },
    })
    text[stage] = r.y
    ElMessage.success(`已按要求调整（${r.y.length} 字）。满意点「保存修改」，不满意可继续调整或手动改`)
  } catch (e) {
    text[stage] = original // 失败还回原稿，绝不丢作者内容
    ElMessage.error(`调整失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
    refiningStage.value = ''
  }
}

// ---------- 项目参考（互为范文：把其他项目/拆书参考书的层注入三段生成） ----------
const refProjects = ref<ProjectMeta[]>([])
const refPid = ref('')
const refLayer = ref('')
const refLayerOptions = ref<string[]>([])
const addingRef = ref(false)
const injectedProjectLayers = ref<{ source_id: string; title: string; content: string; chars: number }[]>([])
const showRefs = ref<string[]>([])   // 参考资料默认收起

const refProjectOptions = computed(() =>
  refProjects.value.filter((p) => p.id !== currentProjectId.value))

async function loadRefProjects() {
  try {
    const r = await projectsApi.list()
    refProjects.value = r.projects
  } catch {
    refProjects.value = []
  }
}

async function onRefProjectChange() {
  refLayer.value = ''
  refLayerOptions.value = []
  if (!refPid.value) return
  try {
    const d = await projectsApi.detail(refPid.value)
    refLayerOptions.value = LAYERS.filter((l) => d.layers[l]?.current)
  } catch (e) {
    ElMessage.error(`读取项目层失败：${(e as Error).message}`)
  }
}

async function addProjectLayerRef() {
  if (!refPid.value || !refLayer.value) {
    ElMessage.warning('先选项目和层')
    return
  }
  addingRef.value = true
  try {
    const r = await projectsApi.layer(refPid.value, refLayer.value)
    if (!r.version) {
      ElMessage.warning('该层还没有内容')
      return
    }
    const p = refProjects.value.find((x) => x.id === refPid.value)!
    injectedProjectLayers.value.push({
      source_id: `project:${refPid.value}:${refLayer.value}:${r.version.seq}`,
      title: `《${p.title}》${LAYER_LABELS[refLayer.value] ?? refLayer.value}`,
      content: r.version.y,
      chars: r.version.y.length,
    })
  } catch (e) {
    ElMessage.error(`读取层内容失败：${(e as Error).message}`)
  } finally {
    addingRef.value = false
  }
}

function removeProjectLayerRef(i: number) {
  injectedProjectLayers.value.splice(i, 1)
}

const deskRefMaterials = () =>
  injectedProjectLayers.value.map((m) => ({
    source_id: m.source_id, family: 'reference', content: m.content, title: m.title,
  }))

// 开章任务书（P1①五段式）：剧情/正文生成时自动带上——开篇委托/本章故事/人物与设定/怎么写更顺/收在哪里
const useChecklist = ref(true)

async function runStage(stage: StageKey) {
  if (busy.value) return
  busy.value = stage
  // 流式生成：清空旧文，正文增量实时打进编辑框
  text[stage] = ''
  thinking[stage] = ''
  stageNotes[stage] = []
  try {
    const r = await deskApi.runStream(
      currentProjectId.value,
      currentNo.value,
      stage,
      {
        directive: directive[stage].trim() || undefined,
        force: force[stage],
        checklist: useChecklist.value,
        materials: deskRefMaterials(),
        modules: chosenModules.value.length ? [...chosenModules.value] : undefined,
        fullModules: fullModules.value,
      },
      {
        onThinking: (t) => {
          thinking[stage] += t
        },
        onDelta: (t) => {
          text[stage] += t
        },
        onResumed: () => {
          stageNotes[stage].push('命中收据，秒回（未重调模型）')
        },
        onNotice: (n) => {
          stageNotes[stage].push(n)
        },
      },
    )
    ElMessage.success(
      `${STAGES.find((s) => s.key === stage)!.label}已生成 ${r.y.length} 字` +
        (r.resumed_indices.length ? '（命中收据）' : ''),
    )
    if (r.checklist_injected) {
      stageNotes[stage].push('已带五段任务书：开篇委托 → 本章故事 → 人物与设定 → 怎么写更顺（含三线近况/到期伏笔）→ 收在哪里')
    }
    busy.value = '' // 先解锁再刷新，selectChapter 有 busy 守卫
    await loadOverview()
    if (currentNo.value) await selectChapter(currentNo.value)
  } catch (e) {
    ElMessage.error(`生成失败：${(e as Error).message}`)
    busy.value = ''
  }
}

async function saveStage(stage: StageKey) {
  if (!text[stage].trim()) {
    ElMessage.warning('内容为空，无需保存')
    return
  }
  try {
    await deskApi.saveText(currentProjectId.value, currentNo.value, stage, text[stage])
    ElMessage.success('已保存（手工内容不会被机器覆盖）')
    await loadOverview()
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  }
}

async function runReview() {
  if (busy.value) return
  busy.value = 'review'
  try {
    review.value = await deskApi.review(currentProjectId.value, currentNo.value)
    ElMessage.success(review.value.verdict === 'pass' ? '审稿通过' : '审稿发现不稳项，请处理后再定稿')
  } catch (e) {
    ElMessage.error(`审稿失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

// 定点修复（P3②）：只修审稿不稳项，其余一字不动；写回来源阶段（润色优先），不强制重审
async function runFix() {
  if (busy.value) return
  if (!review.value || review.value.verdict === 'pass' || review.value.fix_applied) return
  const stage: StageKey = text.polish ? 'polish' : 'draft'
  busy.value = 'fix'
  thinking[stage] = ''
  stageNotes[stage] = ['正在定点修复审稿不稳项——只改问题点，其余一字不动，改完直接写回不重审']
  const original = text[stage]
  text[stage] = ''
  try {
    const r = await deskApi.fixStream(currentProjectId.value, currentNo.value, {
      onThinking: (t) => {
        thinking[stage] += t
      },
      onDelta: (t) => {
        text[stage] += t
      },
      onNotice: (n) => {
        stageNotes[stage].push(n)
      },
    })
    text[stage] = r.y
    if (review.value) {
      review.value = { ...review.value, fix_applied: true, fix_summary: `已按 ${r.fixed} 条问题定点修复（未复审）` }
    }
    ElMessage.success(`已修复 ${r.fixed} 处不稳项并写回${stage === 'polish' ? '润色' : '正文'}——可复核后直接定稿，要复审再点「运行审稿」`)
    busy.value = ''
    await loadOverview()
  } catch (e) {
    text[stage] = original
    ElMessage.error(`定点修复失败：${(e as Error).message}`)
    busy.value = ''
  }
}

async function doFinalize() {
  if (busy.value) return
  const payoff = payoffChoice.value || 'none'
  try {
    await ElMessageBox.confirm(
      `定稿第 ${currentNo.value} 章？将自动执行保鲜飞轮：抽取实体、伏笔兑现问询、核验必现标记、整本合成采纳为新版本。` +
        `本章兑现将记为「${PAYOFF_OPTIONS.find((p) => p.value === payoff)!.label}」（可在上方四选一里改）。`,
      '定稿确认',
      { confirmButtonText: '定稿', cancelButtonText: '再改改', type: 'info' },
    )
  } catch {
    return
  }
  busy.value = 'finalize'
  try {
    lastFinalize.value = await deskApi.finalize(currentProjectId.value, currentNo.value, true, payoff)
    payoffChoice.value = payoff
    ElMessage.success(`第 ${currentNo.value} 章已定稿，飞轮运转完成`)
    // 调 briefApi.advance 判断下一章 / 杀青
    try {
      const adv = await briefApi.advance(currentProjectId.value, currentVolumeNo.value, currentNo.value)
      await loadOverview()
      if (adv.next_chapter != null) {
        currentChapterNo.value = adv.next_chapter
        emit('next-chapter', adv.next_chapter)
      } else if (adv.phase === 'settle') {
        emit('to-settle')
      } else {
        const next = overview.value?.chapters.find((c) => !c.finalized_at)
        if (next) await selectChapter(next.no)
      }
    } catch (advE: any) {
      console.warn('advance 失败，停留本章：', advE)
      await loadOverview()
    }
  } catch (e) {
    ElMessage.error(`定稿失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  await loadOverview(false)
  loadRefProjects()
  loadModules()
})

watch(currentProjectId, () => {
  currentNo.value = 0
  detail.value = null
  overview.value = null
  deskPage.value = 1
  loadOverview(false)
})

// tab 用 v-show 常驻：切回章台要重拉总览
watch(activeTab, (tab) => {
  if (tab === 'desk' && currentProjectId.value && !busy.value) loadOverview()
})
</script>

<template>
  <div v-if="!currentProjectId">
    <el-empty description="先选择或创建一个小说项目">
      <el-button type="primary" @click="activeTab = 'projects'">去项目管理</el-button>
    </el-empty>
  </div>

  <div v-else-if="!overview">
    <el-empty :description="loading ? '章台加载中…' : '章台加载失败'">
      <el-button v-if="!loading" @click="loadOverview(false)">重试</el-button>
    </el-empty>
  </div>

  <div v-else-if="!overview.chapters.length">
    <el-empty description="还没有章节——请先完成节奏骨架推演，系统会自动创建第一章">
      <p style="font-size:12px;color:#999;margin-top:6px">若节奏蓝图已完成但仍看不到章节，请刷新页面</p>
    </el-empty>
  </div>

  <el-row v-else :gutter="16">
    <!-- 左栏：章节列表 -->
    <el-col :span="7">
      <el-card shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>章节进度</span>
            <el-tag type="success" size="small">{{ finalizedCount }} / {{ totalCount }} 章定稿</el-tag>
          </div>
        </template>
        <div v-if="todayNo" style="margin-bottom: 10px; font-size: 13px; color: #666">
          今天写到第 <b style="color: var(--el-color-primary)">{{ todayNo }}</b> 章
        </div>
        <el-scrollbar max-height="560px">
          <template v-for="g in chapterGroups" :key="g.volume || `__g${g.items[0]?.no ?? 0}`">
            <div
              v-if="g.volume"
              class="volume-head"
              :title="volumeNotes.get(g.volume) || ''"
            >
              {{ g.volume }}
              <span v-if="volumeNotes.get(g.volume)" style="font-weight: 400; opacity: 0.7">
                {{ volumeNotes.get(g.volume)!.slice(0, 24) }}…
              </span>
            </div>
            <div
              v-for="c in g.items"
              :key="c.no"
              class="chapter-item"
              :class="{ active: c.no === currentNo, today: c.no === todayNo }"
              @click="selectChapter(c.no)"
            >
              <div style="display: flex; justify-content: space-between; align-items: center; gap: 6px">
                <span>第 {{ c.no }} 章 · {{ c.title || '（未命名）' }}</span>
                <el-tag v-if="c.finalized_at" type="success" size="small">定稿</el-tag>
              </div>
              <div style="display: flex; gap: 4px; margin-top: 4px; align-items: center">
                <el-tag :type="c.plot_done ? 'success' : 'info'" size="small" effect="plain">剧情</el-tag>
                <el-tag :type="c.draft_done ? 'success' : 'info'" size="small" effect="plain">正文</el-tag>
                <el-tag :type="c.polish_done ? 'success' : 'info'" size="small" effect="plain">润色</el-tag>
                <el-tag
                  v-if="c.review_verdict"
                  :type="c.review_verdict === 'pass' ? 'success' : 'danger'"
                  size="small"
                  effect="plain"
                >
                  审{{ c.review_verdict === 'pass' ? '过' : '卡' }}
                </el-tag>
                <el-tag v-if="c.strands.length" size="small" effect="plain">
                  {{ c.strands.map((s) => (s === 'quest' ? '主' : s === 'fire' ? '冲' : '羁')).join('+') }}
                </el-tag>
                <el-tag
                  v-if="c.payoff === 'climax'"
                  type="danger"
                  size="small"
                  effect="plain"
                  >卷高潮</el-tag>
                <el-tag v-else-if="c.payoff === 'medium'" type="warning" size="small" effect="plain">中兑现</el-tag>
                <el-tag v-else-if="c.payoff === 'small'" type="success" size="small" effect="plain">爽</el-tag>
                <span v-if="c.chars" style="font-size: 12px; color: #999">{{ c.chars }} 字</span>
              </div>
            </div>
          </template>
        </el-scrollbar>
        <el-pagination
          v-if="totalCount > deskPageSize"
          v-model:current-page="deskPage"
          :page-size="deskPageSize"
          :total="totalCount"
          layout="prev, pager, next"
          size="small"
          style="margin-top: 8px; justify-content: center"
        />
      </el-card>
    </el-col>

    <!-- 右栏：单章微循环 -->
    <el-col :span="17">
      <template v-if="detail">
        <!-- 章前卡摘要条 -->
        <el-card v-if="currentBriefSelected" shadow="never" style="margin-bottom: 14px" class="brief-summary-card">
          <div style="display: flex; align-items: flex-start; gap: 10px">
            <div style="flex-shrink: 0">
              <el-tag type="warning" effect="dark" size="small">章前方向</el-tag>
            </div>
            <div style="flex: 1; min-width: 0">
              <div style="font-weight: 600; color: #e6a23c; margin-bottom: 4px">
                {{ currentBriefSelected.title || '方向已定' }}
                <el-tag v-if="currentBriefSelected.beat_type" size="small" type="info" effect="plain" style="margin-left: 6px">
                  {{ { hook:'钩子', sell:'卖设定', engine:'推进', climax:'高潮', cooldown:'余韵' }[currentBriefSelected.beat_type] || currentBriefSelected.beat_type }}
                </el-tag>
                <el-tag v-if="currentBrief.is_custom" size="small" type="warning" effect="plain" style="margin-left: 4px">自编</el-tag>
              </div>
              <div style="font-size: 13px; color: #606266; line-height: 1.7; white-space: pre-wrap">
                {{ currentBriefSelected.summary }}
              </div>
              <div v-if="currentBriefSelected.hooks" style="font-size: 12px; color: #e6a23c; margin-top: 4px">
                🪝 {{ currentBriefSelected.hooks }}
              </div>
            </div>
            <el-button size="small" plain @click="emit('reopen-brief')">✏️ 换方向</el-button>
          </div>
        </el-card>
        <el-card v-else shadow="never" style="margin-bottom: 14px">
          <div style="display: flex; align-items: center; gap: 10px">
            <el-tag type="warning" effect="plain">尚未选定本章方向</el-tag>
            <span style="font-size: 12px; color: #999">先花 5 分钟选好方向再动笔，效率更高</span>
            <div style="flex: 1" />
            <el-button size="small" type="primary" @click="emit('reopen-brief')">📝 选方向卡</el-button>
          </div>
        </el-card>

        <el-card shadow="never" style="margin-bottom: 14px">
          <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
            <h3 style="margin: 0">第 {{ detail.chapter.no }} 章 · {{ detail.chapter.title || '（未命名）' }}</h3>
            <el-tag v-if="detail.chapter.finalized_at" type="success">已定稿 {{ detail.chapter.finalized_at.slice(0, 16) }}</el-tag>
            <el-tag v-else-if="currentNo === todayNo" type="primary" effect="plain">今日目标</el-tag>
          </div>
          <el-collapse v-model="showRefs" style="margin-top: 10px">
            <el-collapse-item :name="'refs'">
              <template #title>
                参考资料注入（其他项目 / 拆书参考书）
                <el-tag v-if="injectedProjectLayers.length" size="small" type="info" style="margin-left: 8px">
                  已挂 {{ injectedProjectLayers.length }} 层
                </el-tag>
              </template>
              <div style="font-size: 12px; color: #999; margin-bottom: 8px">
                挂载后本章三段生成（剧情/正文/润色）都会带上这些层作参考——写新书时可挂拆书参考书的大纲，连载时可挂自己旧书的正文
              </div>
              <el-form inline>
                <el-form-item>
                  <el-select
                    v-model="refPid"
                    filterable
                    clearable
                    placeholder="选项目（含参考书）"
                    style="width: 200px"
                    @change="onRefProjectChange"
                  >
                    <el-option
                      v-for="p in refProjectOptions"
                      :key="p.id"
                      :value="p.id"
                      :label="(p.kind === 'reference' ? '〔参考书〕' : '') + p.title"
                    />
                  </el-select>
                </el-form-item>
                <el-form-item>
                  <el-select v-model="refLayer" placeholder="选层" style="width: 100px" :disabled="!refPid">
                    <el-option v-for="l in refLayerOptions" :key="l" :value="l" :label="LAYER_LABELS[l] ?? l" />
                  </el-select>
                </el-form-item>
                <el-form-item>
                  <el-button size="small" :loading="addingRef" :disabled="!refPid || !refLayer" @click="addProjectLayerRef">加入</el-button>
                </el-form-item>
              </el-form>
              <div v-for="(m, i) in injectedProjectLayers" :key="m.source_id" style="display: flex; align-items: center; gap: 8px; margin-bottom: 6px">
                <el-tag size="small" type="info" closable @close="removeProjectLayerRef(i)">{{ m.title }}</el-tag>
                <span style="font-size: 12px; color: #999">{{ m.chars.toLocaleString() }} 字</span>
              </div>
            </el-collapse-item>
            <el-collapse-item name="modules">
              <template #title>
                写作模块注入
                <el-tag v-if="chosenModules.length" size="small" type="warning" style="margin-left: 8px">
                  点名 {{ chosenModules.length }} 个·完整规则
                </el-tag>
                <el-tag v-else size="small" type="info" style="margin-left: 8px">按层默认·摘要版</el-tag>
              </template>
              <div style="font-size: 12px; color: #999; margin-bottom: 8px; line-height: 1.8">
                不勾选时按层自动带摘要版知识卡（省上下文）：剧情默认「{{ defaultModuleTitles('plot').join('、') || '无' }}」，
                正文/润色默认「{{ defaultModuleTitles('draft').join('、') || '无' }}」。
                勾选即点名替换默认集，并注入完整规则——点得越多上下文越满，按需勾。
              </div>
              <el-checkbox-group v-model="chosenModules" size="small">
                <el-checkbox v-for="m in moduleOptions" :key="m.name" :value="m.name" style="margin-right: 14px">
                  {{ m.title }}
                </el-checkbox>
              </el-checkbox-group>
              <div style="margin-top: 6px">
                <el-checkbox v-model="fullModules" size="small">
                  不点名时默认集也注入完整规则（默认摘要版）
                </el-checkbox>
              </div>
            </el-collapse-item>
          </el-collapse>
          <div style="margin-top: 8px; font-size: 12px">
            <el-checkbox v-model="useChecklist" size="small">
              生成剧情/正文时带五段开章任务书（开篇委托 → 本章故事 → 人物与设定 → 怎么写更顺 → 收在哪里）
            </el-checkbox>
            <span style="color: #999">
              —— 从实体库与章台实时推导：接住上一章结尾，已出场实体不得重设，账龄最长的未回收伏笔优先兑现，三线断档会提醒安排
            </span>
          </div>
          <div style="margin-top: 6px; font-size: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <span style="flex-shrink: 0"><b>本章推进的线：</b></span>
            <el-checkbox-group :model-value="strandChoice" size="small" @update:model-value="onStrandsChange">
              <el-checkbox-button v-for="s in STRAND_OPTIONS" :key="s.value" :value="s.value">{{ s.label }}</el-checkbox-button>
            </el-checkbox-group>
            <span style="color: #999">可多选。主线推进情节、冲突制造对抗、羁绊养关系——顺手打卡，开章任务书的三线近况与审稿的断档报警都吃这份账</span>
          </div>
        </el-card>

        <!-- 三段生成：剧情 / 正文 / 润色 -->
        <el-card v-for="s in STAGES" :key="s.key" shadow="never" style="margin-bottom: 14px">
          <template #header>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <b>{{ s.label }}</b>
              <span style="color: #999; font-size: 12px">{{ s.tip }}</span>
              <span style="flex: 1" />
              <el-checkbox v-model="force[s.key]" size="small">忽略收据重生成</el-checkbox>
              <el-button size="small" type="primary" :loading="busy === s.key" @click="runStage(s.key)">
                {{ text[s.key] ? '重新生成' : `生成${s.label}` }}
              </el-button>
              <el-button
                size="small"
                type="warning"
                plain
                :loading="busy === s.key && refiningStage === s.key"
                :disabled="!text[s.key] || !!busy"
                @click="refineStage(s.key)"
              >
                按要求调整
              </el-button>
              <el-button size="small" :disabled="!text[s.key] || busy === s.key" @click="saveStage(s.key)">
                保存修改
              </el-button>
            </div>
          </template>
          <el-input
            v-model="directive[s.key]"
            size="small"
            :placeholder="`指令（生成用，或配合「按要求调整」改当前文本）：如「加强悬念」「开头放慢」「对话再口语化」`"
            style="margin-bottom: 10px"
            :disabled="busy === s.key"
          />
          <div v-if="thinking[s.key]" class="thinking-pane">
            <div style="font-size: 12px; color: #909399; margin-bottom: 4px">
              思考过程{{ busy === s.key ? '（生成中，实时）' : '' }}
            </div>
            <pre style="margin: 0; white-space: pre-wrap; font-family: inherit; line-height: 1.7">{{ thinking[s.key] }}</pre>
          </div>
          <div v-for="(n, i) in stageNotes[s.key]" :key="i" style="font-size: 12px; color: #e6a23c; margin-bottom: 6px">
            {{ n }}
          </div>
          <el-input
            v-model="text[s.key]"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 16 }"
            :placeholder="`尚未生成${s.label}。可直接粘贴你自己的内容后点「保存修改」——手写和机器生成同等地位。`"
            :ref="setStagePane(s.key)"
          />
          <div v-if="detail!.chapter[s.key]?.updated_at" style="margin-top: 6px; font-size: 12px; color: #999">
            最近更新 {{ detail!.chapter[s.key]!.updated_at.slice(0, 16).replace('T', ' ') }}
          </div>
        </el-card>

        <!-- 审稿 -->
        <el-card shadow="never" style="margin-bottom: 14px">
          <template #header>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <b>审稿</b>
              <span style="color: #999; font-size: 12px">七项一致性检查：情节逻辑 / 人物目标 / 情感关系 / 物理信息 / 场景过渡 / 章尾钩子 / 跨章联结</span>
              <span style="flex: 1" />
              <el-button
                v-if="review && review.verdict !== 'pass' && !review.fix_applied"
                size="small"
                type="danger"
                plain
                :loading="busy === 'fix'"
                :disabled="!!busy"
                @click="runFix"
              >
                定点修复不稳项
              </el-button>
              <el-button size="small" :loading="busy === 'review'" :disabled="!text.draft && !text.polish" @click="runReview">
                运行审稿
              </el-button>
            </div>
          </template>
          <template v-if="review">
            <div style="margin-bottom: 8px">
              <el-tag :type="review.verdict === 'pass' ? 'success' : 'danger'">
                {{ review.verdict === 'pass' ? '通过' : `发现 ${review.unstable_count} 处不稳` }}
              </el-tag>
              <span style="margin-left: 8px; color: #666; font-size: 13px">{{ review.summary }}</span>
            </div>
            <div v-for="it in review.items" :key="it.name" style="margin-bottom: 6px; font-size: 13px">
              <el-tag :type="it.status === '稳' ? 'success' : 'danger'" size="small" effect="plain">{{ it.name }}：{{ it.status }}</el-tag>
              <span style="margin-left: 8px; color: #666">{{ it.problem }}</span>
            </div>
            <el-alert
              v-if="review.fix_applied"
              type="success"
              :closable="false"
              :title="`${review.fix_summary ?? '已定点修复'}——修复后不重审，可复核正文直接定稿；要复审再点「运行审稿」`"
              style="margin-top: 8px"
            />
            <div v-if="review.rhythm_warning" style="margin-top: 8px; font-size: 12px; color: #e6a23c">
              ⚠ {{ review.rhythm_warning }}
            </div>
            <div v-for="w in review.strand_warnings ?? []" :key="w" style="margin-top: 8px; font-size: 12px; color: #e6a23c">
              ⚠ {{ w }}
            </div>
            <div v-if="review.verdict !== 'pass' && !review.fix_applied" style="margin-top: 8px; font-size: 12px; color: #e6a23c">
              处理方式：「定点修复」只修不稳项不重审；或自己改完重新审稿；或到「审稿与标记」页把问题转成下一章必现标记
            </div>
          </template>
          <el-empty v-else description="生成正文后先审稿，通过再定稿" :image-size="60" />
        </el-card>

        <!-- 定稿 -->
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <b>定稿</b>
              <span style="color: #999; font-size: 12px">定稿后进入保鲜飞轮：抽取实体 → 伏笔兑现问询 → 核验必现标记 → 整本合成采纳</span>
              <span style="flex: 1" />
              <el-button type="success" :loading="busy === 'finalize'" :disabled="!!detail.chapter.finalized_at" @click="doFinalize">
                {{ detail.chapter.finalized_at ? '已定稿' : `定稿第 ${currentNo} 章` }}
              </el-button>
            </div>
          </template>
          <div style="margin-bottom: 10px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
            <span style="font-size: 13px"><b>本章兑现：</b></span>
            <el-radio-group :model-value="payoffChoice" size="small" @update:model-value="onPayoffChange">
              <el-radio-button v-for="p in PAYOFF_OPTIONS" :key="p.value" :value="p.value">
                {{ p.label }}
              </el-radio-button>
            </el-radio-group>
            <span style="font-size: 12px; color: #999">
              顺手点选，攒 20 章即有自家节奏量纲——开章核对单的「节奏位置」与审稿的「节奏饥饿」都吃这份账
            </span>
          </div>
          <template v-if="lastFinalize">
            <div style="font-size: 13px; line-height: 2">
              <div>正文版本：v{{ lastFinalize.draft_version }}（全 {{ lastFinalize.total_chapters }} 章）</div>
              <div v-if="lastFinalize.entities">
                实体飞轮：
                <template v-if="lastFinalize.entities.error">
                  <el-tag type="warning" size="small">未完成：{{ lastFinalize.entities.error }}</el-tag>
                </template>
                <template v-else>
                  新建 {{ (lastFinalize.entities.created ?? []).length }} ·
                  更新 {{ (lastFinalize.entities.updated ?? []).length }} ·
                  丢弃 {{ lastFinalize.entities.dropped ?? 0 }}
                </template>
              </div>
              <div v-if="lastFinalize.foreshadow">
                伏笔回账：
                <template v-if="lastFinalize.foreshadow.error">
                  <el-tag type="warning" size="small">未完成：{{ lastFinalize.foreshadow.error }}</el-tag>
                </template>
                <template v-else>
                  <el-tag v-if="!lastFinalize.foreshadow.paid?.length && !lastFinalize.foreshadow.advanced?.length"
                          type="info" size="small">本章无兑现也无推进</el-tag>
                  <el-tag v-for="n in lastFinalize.foreshadow.paid" :key="'p' + n" type="success" size="small"
                          style="margin-right: 6px">已兑现：{{ n }}</el-tag>
                  <el-tag v-for="n in lastFinalize.foreshadow.advanced" :key="'a' + n" type="warning" size="small"
                          style="margin-right: 6px">已推进：{{ n }}</el-tag>
                </template>
                <div v-if="lastFinalize.foreshadow.warning" style="color: #e6a23c">
                  ⚠ {{ lastFinalize.foreshadow.warning }}
                </div>
              </div>
              <div v-if="lastFinalize.marker_hits.length">
                必现标记核验：
                <el-tag
                  v-for="h in lastFinalize.marker_hits"
                  :key="h.marker.id"
                  :type="h.hit ? 'success' : 'danger'"
                  size="small"
                  style="margin-right: 6px"
                >
                  {{ h.hit ? '已兑现' : '未兑现' }}：{{ h.marker.content.slice(0, 20) }}
                </el-tag>
              </div>
            </div>
          </template>
          <el-empty v-else description="定稿即归档：实体库与全书版本随更新，越写越准" :image-size="60" />
        </el-card>
      </template>
      <el-empty v-else description="从左侧选择一章开始今天的更新" />
    </el-col>
  </el-row>
</template>

<style scoped>
.chapter-item {
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
  font-size: 13px;
}
.chapter-item:hover {
  background: #f5f7fa;
}
.chapter-item.active {
  background: var(--el-color-primary-light-9);
}
.chapter-item.today {
  border-left: 3px solid var(--el-color-primary);
}
.outline-preview {
  white-space: pre-wrap;
  font-size: 13px;
  color: #555;
  line-height: 1.8;
}
.thinking-pane {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 10px;
  max-height: 200px;
  overflow: auto;
  font-size: 12px;
  color: #909399;
}
</style>
