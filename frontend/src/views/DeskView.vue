<script setup lang="ts">
// 今日章台：连载期日更微循环——剧情 → 正文 → 审稿 → 润色 → 定稿（保鲜飞轮）
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  LAYER_LABELS,
  LAYERS,
  adaptApi,
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
import { visibleBeats } from '../plotBeats'
import { provideChatContext } from '../chatContext'
import PrevStepPanel from '../components/PrevStepPanel.vue'

const props = defineProps<{
  /** 指定章节号；不传则自动选 todayNo */
  chapterNo?: number
  /** 中台/自由编辑模式：不写入全局 currentChapterNo/currentVolumeNo，避免污染 phase 流程 */
  freeStanding?: boolean
}>()

/** 当前卷号（中台模式从章节详情取 field_no，流程模式走全局 currentVolumeNo） */
const activeVolumeNo = computed(() => {
  if (props.freeStanding) {
    const fn = (detail.value?.chapter as any)?.field_no
    if (fn != null) return fn
  }
  return currentVolumeNo.value
})

const emit = defineEmits<{
  (e: 'reopen-brief'): void
  (e: 'next-chapter', no: number): void
  (e: 'to-settle'): void
}>()

const loading = ref(false)
const overview = ref<DeskDetail | null>(null)
const currentNo = ref(0)
const detail = ref<DeskChapterDetail | null>(null)
const curVolume = ref<any>(null)

function wordStatus(chars: number | null | undefined): { cls: string; tip: string } {
  const n = Number(chars) || 0
  if (!n) return { cls: 'wc-none', tip: '' }
  if (n < 3500) return { cls: 'wc-short', tip: `过短（<3500）` }
  if (n > 5500) return { cls: 'wc-long', tip: `过长（>5500）` }
  if (n >= 4000 && n <= 5000) return { cls: 'wc-ok', tip: '达标（4000-5000）' }
  if (n < 4000) return { cls: 'wc-near-low', tip: '略短（3500-3999）' }
  return { cls: 'wc-near-high', tip: '略长（5001-5500）' }
}

async function loadCurVolume() {
  const pid = currentProjectId.value
  const vn = activeVolumeNo.value
  if (!pid || !vn) { curVolume.value = null; return }
  try {
    const r = await adaptApi.getVolume(pid, vn)
    curVolume.value = r.volume
  } catch { curVolume.value = null }
}

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
/** 卷末章开关：null=自动；true=强制杀青；false=压制杀青继续写 */
const endChapterOverride = ref<boolean | null>(null)
/** 上一次 advance 返回（展示 at_end/force_state） */
const lastAdvance = ref<{ at_end?: boolean; auto_at_end?: boolean; force_state?: string; next_chapter?: number; phase?: string } | null>(null)

// 注册章台聊天上下文
provideChatContext(() => {
  const ch = detail.value
  const rev = review.value
  return {
    phase: '✍️ 章台写作',
    system_hint:
      '作者在章台写作：plot（剧情提纲）→draft（正文）→polish（润色）→review（九项审稿）→finalize（定稿）。'
      + '当前正在写的章节可能只完成了部分阶段。回答时先看 context 里各阶段文本长度/状态定位问题，'
      + '如果作者在吐槽白描/逻辑/伏笔/能力长线复用，直接给可替换的段落/句子，不要空泛建议。'
      + '九项审稿结果已在 context.review 里，不稳项就是作者最需要改的地方；cliche_hits 列出白描锁命中的空泛词。',
    context: {
      volume_no: activeVolumeNo.value,
      chapter_no: currentNo.value || currentChapterNo.value,
      finalized_at: ch?.finalized_at || null,
      is_finalized: !!ch?.finalized_at,
      tab: activeTab.value || null,
      text_length: {
        plot: text.plot?.length || 0,
        draft: text.draft?.length || 0,
        polish: text.polish?.length || 0,
      },
      // 避免超长：正文只截最近 2000 字（作者通常在吐槽最近写的部分）
      plot_tail: (text.plot || '').slice(-2000),
      draft_tail: (text.draft || '').slice(-2000),
      polish_tail: (text.polish || '').slice(-2000),
      directive: { plot: directive.plot, draft: directive.draft, polish: directive.polish },
      review: rev ? {
        blocked: rev.blocked,
        summary: rev.summary,
        items: rev.items || [],
        cliche_hits: rev.cliche_hits || [],
        overall_assessment: rev.overall_assessment,
      } : null,
      chapter: ch ? {
        title: ch.title || null,
        chapter_title: (ch as any).chapter_title || null,
        word_count: (ch as any).word_count || null,
      } : null,
    },
  }
})

/** 卷末自动判定。
 *
 *  **不要用 totalCount**：那是全书章数（len(chapters)），不是本卷章数。
 *  早期版本写成 `currentNo >= totalCount`，于是第 1 卷第 2 章
 *  （全书共 20 章）永远判不出卷末 —— 「本章已是本卷终章」勾不上，
 *  文案还打出「卷内计划共 20 章」。两处都是同一个根因。
 *
 *  判据是「当前章是不是本卷已建章里的最后一章」；
 *  节奏蓝图另有 suggested_chapter_range，那条由后端 _brief_advance 判定，
 *  前端不重复实现，只按章数近似。
 */
const autoAtEnd = computed(() => isVolumeLastByCount.value)

/** 当前卷有没有节奏蓝图。
 *  主线卷（蓝星开篇）不走 rhythm 流程，没有蓝图；这时说「按节奏蓝图」是骗人的，
 *  得区分措辞——否则作者会去找一份不存在的蓝图。 */
const hasRhythmBlueprint = computed(() => {
  const bp = curVolume.value?.rhythm_blueprint
  return !!(bp && Array.isArray(bp.chapter_plan) && bp.chapter_plan.length)
})
const endChapterChecked = computed({
  get: () => {
    if (endChapterOverride.value !== null) return endChapterOverride.value
    return autoAtEnd.value
  },
  set: (v: boolean) => {
    endChapterOverride.value = v
  },
})
const endChapterIndeterminate = computed(() => endChapterOverride.value === null && autoAtEnd.value === false)

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

/** 当前卷的章号列表（升序）。卷归属优先用 overview.volumes 里 volumes store 的真实分组，
 *  拿不到时退回章节自身的 field_no —— 两者不一致说明分组逻辑漏了章，按 field_no 更保险。 */
const currentVolumeChapters = computed<number[]>(() => {
  const ov = overview.value
  if (!ov) return []
  const fromVols = (ov.volumes || []).find(
    (v) => v.volume_no != null && v.volume_no === activeVolumeNo.value,
  )
  if (fromVols?.chapters?.length) return [...fromVols.chapters].sort((a, b) => a - b)
  // 回退：按 field_no 归组（field_no 才是副本卷的真实归属键）
  const cur = ov.chapters?.find((c) => c.no === currentNo.value)
  const fn = cur?.field_no ?? ((detail.value?.chapter as any)?.field_no ?? null)
  if (fn == null) return []
  return (ov.chapters || [])
    .filter((c) => c.field_no === fn)
    .map((c) => c.no)
    .sort((a, b) => a - b)
})

/** 当前卷共几章 */
const volumeTotalCount = computed(() => currentVolumeChapters.value.length)

/** 当前章在本卷内的序号（从 1 起）。第 1 卷第 2 章 → 2，不是 2/20。 */
const volumeSeqNo = computed(() => {
  const idx = currentVolumeChapters.value.indexOf(currentNo.value)
  return idx >= 0 ? idx + 1 : 0
})

/** 本章是不是本卷最后一章（按已建章数判定，不依赖节奏蓝图）。
 *  主线卷（蓝星开篇）不走 rhythm，没有 suggested_chapter_range，
 *  autoAtEnd 永远为 false —— 这类卷只能靠「已建章数」判卷末。 */
const isVolumeLastByCount = computed(() => {
  const list = currentVolumeChapters.value
  if (!list.length) return false
  return currentNo.value === Math.max(...list)
})
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
    const fallbackChapter = props.freeStanding
      ? (todayNo.value || overview.value.chapters[0]?.no || 0)
      : (currentChapterNo.value || todayNo.value || overview.value.chapters[0]?.no || 0)
    let targetNo = currentNo.value
    if (!keepSelection || !currentNo.value) {
      targetNo = props.chapterNo || fallbackChapter
    }
    if (targetNo && !overview.value.chapters.some((c) => c.no === targetNo)) {
      targetNo = props.chapterNo || fallbackChapter
    }
    if (targetNo && targetNo !== currentNo.value) {
      currentNo.value = targetNo
    }
    // 确保右栏加载当前章详情
    if (targetNo && (!detail.value || detail.value.chapter.no !== targetNo)) {
      await selectChapter(targetNo)
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
    lastAdvance.value = null
    endChapterOverride.value = null
    // 中台模式下章节详情加载后根据 field_no 重取卷信息
    if (props.freeStanding) loadCurVolume()
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
    }, stage)
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

/**
 * spec 4 · 写前风险确认（零 token）。
 *
 * 只在 `blocked` 时弹——每次必确认的确认门三章后就变成肌肉记忆，作者闭眼点确认，
 * 制造「我已经确认过了」的假安全感，比不做更糟。
 *
 * 展示的 brief 就是模型将看到的（同一个 build_chapter_checklist 产出），
 * 所以这是「透明」而非「拦截」：作者判断的依据和模型的依据是同一份。
 */
async function confirmPreWrite(stage: StageKey): Promise<boolean> {
  let pv
  try {
    pv = await deskApi.gatePreview(currentProjectId.value, currentNo.value, stage)
  } catch {
    return true // 预演失败不阻断写作——它是辅助信息，不是门禁
  }
  if (!pv.blocked) return true

  const riskHtml = pv.risks
    .map(
      (r) =>
        `<div style="margin:6px 0"><b style="color:#c45656">${escapeHtml(r.label)}</b>` +
        `<div style="font-size:13px;color:#606266;margin-top:2px">${escapeHtml(r.detail)}</div></div>`,
    )
    .join('')
  const briefBlock = pv.brief
    ? `<details style="margin-top:12px"><summary style="cursor:pointer;font-size:13px">` +
      `看看模型将收到的开章任务书（${pv.brief.length} 字）</summary>` +
      `<pre style="max-height:280px;overflow:auto;background:#f6f7f9;padding:10px;border-radius:4px;` +
      `font-size:12px;line-height:1.6;white-space:pre-wrap;margin-top:6px">${escapeHtml(pv.brief)}</pre></details>`
    : ''

  try {
    await ElMessageBox.confirm(
      `<div>第 ${pv.chapter} 章「${STAGES.find((s) => s.key === stage)!.label}」开写前，` +
        `账上有 ${pv.risks.length} 项需要你知道：</div>${riskHtml}${briefBlock}`,
      '写前风险确认',
      {
        confirmButtonText: '知道，开写',
        cancelButtonText: '先处理一下',
        dangerouslyUseHTMLString: true,
        type: 'warning',
        distinguishCancelAndClose: true,
      },
    )
  } catch {
    return false // 取消 = 作者要先回去补账，不该硬着头皮开写
  }
  // 表态入账（记录失败不阻断——决定已经做了）
  void deskApi
    .gateDecide(currentProjectId.value, currentNo.value, {
      gate: 'pre_write',
      decision: 'accept',
      stage,
      risks: pv.risks.map((r) => r.kind),
      detail: pv.risks.map((r) => r.detail).join('；').slice(0, 280),
    })
    .catch(() => undefined)
  return true
}

function escapeHtml(s: string): string {
  return (s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * spec 4 · 门禁后表态。
 *
 * 重试用尽后仍有 P0 时后端不再静默落库，而是带 `gate.required=true`。
 * 这里问作者三件事：收下（默认，与旧行为等价）/ 再重写一次 / 我自己改。
 *
 * 注意这不是「生成失败」——正文已经落库了，弹窗只是让作者知道它被扣过分。
 */
async function handlePostGate(stage: StageKey, rounds: number, gate: {
  required: boolean
  pending: string[]
  demoted: string[]
}): Promise<'accept' | 'rewrite' | 'manual'> {
  const lines = gate.pending
    .map((p) => `<li style="margin:3px 0">${escapeHtml(p)}</li>`)
    .join('')
  const demoted = gate.demoted.length
    ? `<div style="font-size:12px;color:#909399;margin-top:8px">` +
      `另有 ${gate.demoted.length} 项重写多轮未改善已自动降级为建议：<br/>` +
      gate.demoted.map((d) => escapeHtml(d)).join('<br/>') +
      `</div>`
    : ''

  let decision: 'accept' | 'rewrite' | 'manual' = 'accept'
  try {
    await ElMessageBox.confirm(
      `<div>这一版已经生成并存库，但<b>硬门禁跑了 ${rounds} 轮后仍有 ${gate.pending.length} 项未达标</b>：</div>` +
        `<ul style="padding-left:20px;margin:8px 0;font-size:13px">${lines}</ul>${demoted}` +
        `<div style="font-size:12px;color:#909399;margin-top:10px">` +
        `这些是硬门禁判出的硬问题，收下不会丢，但正文确实没达标。</div>`,
      '质量门禁 · 需要你表态',
      {
        confirmButtonText: '收下这一版',
        cancelButtonText: '再重写一次',
        distinguishCancelAndClose: true,
        dangerouslyUseHTMLString: true,
        type: 'warning',
      },
    )
  } catch (a: any) {
    // 区分「取消（想重写）」与「关闭（想自己改）」
    decision = a === 'cancel' ? 'rewrite' : 'manual'
  }

  void deskApi
    .gateDecide(currentProjectId.value, currentNo.value, {
      gate: 'post_gate',
      decision,
      stage,
      detail: gate.pending.join('；').slice(0, 280),
      p0: gate.pending,
    })
    .catch(() => undefined)
  return decision
}

async function runStage(stage: StageKey, opts?: { skipPreGate?: boolean }) {
  if (busy.value) return
  // spec 4：开写前的风险确认（仅风险触发时弹，见 confirmPreWrite 的注释）
  if (!opts?.skipPreGate) {
    const go = await confirmPreWrite(stage)
    if (!go) return
  }
  busy.value = stage
  // 流式生成：清空旧文，正文增量实时打进编辑框
  text[stage] = ''
  thinking[stage] = ''
  stageNotes[stage] = []
  /** spec 1：本轮硬门禁实际跑了几轮（后端一直在发，前端从来没接） */
  let qualityRounds = 0
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
          // spec 2 的护栏回滚通知走这里——静默回滚会让人以为规则生效了其实没有
          stageNotes[stage].push(n)
        },
        // spec 1：质量数据一直是「算了就发、前端从不看」的状态。
        // 接上之后作者能看到这一版被扣了哪些分、重试了几轮。
        onQuality: (q) => {
          if (q.kind === 'hard') {
            qualityRounds = q.round + 1
            if (q.round > 0) {
              stageNotes[stage].push(
                `硬门禁第 ${q.round} 轮：P0 ${(q.p0 || []).length} 项 / P1 ${(q.p1 || []).length} 项`,
              )
            }
            // 气息长度（2026-10-26 定稿反推）：段内逗号数
            // 人工定稿 1.5-2.1 个/段，机器稿 1.08 —— 全章都 <1.2 是摄影机切法
            const rh = q.rhythm
            if (rh && typeof rh['段内逗号均数'] === 'number') {
              const breath = rh['段内逗号均数'] as number
              if (breath < 1.2) {
                stageNotes[stage].push(
                  `气息偏碎：段内逗号 ${breath} 个/段（人工定稿实测 1.5-2.1）` +
                    `——每段基本只有一句，是摄影机切法。`,
                )
              }
            }
          }
        },
        // spec 1：描写账本提示（Top6 重复表达）
        // 只有真正构成模板复读的语义场才会进 top（后端已过滤掉「用一次也叫复读」的噪音），
        // 这里只提示复读最严重的那一条，避免刷屏。
        onLexicon: (l) => {
          const top = (l.top || [])[0]
          if (!top) return
          const n = top.violations
          stageNotes[stage].push(
            `描写账本：${top.field} 在 500 字窗内复读 ${n} 次` +
            (top.min_gap != null ? `（最近间隔 ${top.min_gap} 字）` : ''),
          )
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
    // 作者批注（【】标注）处理报告
    if (r.annotations?.count) {
      const ann = r.annotations
      stageNotes[stage].push(`已识别 ${ann.count} 条【】批注并按最高优先级落实`)
      if (ann.unmet.length) {
        stageNotes[stage].push(
          `⚠ 有 ${ann.unmet.length} 条批注看起来没落实：` +
          ann.unmet.slice(0, 3).map((a) => a.text.slice(0, 20)).join('；') +
          (ann.unmet.length > 3 ? '…' : '') +
          '（请检查，或再润色一次）',
        )
      }
    }
    busy.value = '' // 先解锁再刷新，selectChapter 有 busy 守卫
    await loadOverview()
    if (currentNo.value) await selectChapter(currentNo.value)

    // spec 4：门禁后表态——正文已落库，这里问作者怎么处置残留 P0
    if (r.gate?.required) {
      const decision = await handlePostGate(stage, qualityRounds, r.gate)
      if (decision === 'rewrite') {
        ElMessage.info('正在带上门禁问题重写一次')
        await runStage(stage, { skipPreGate: true })
        return
      }
      if (decision === 'manual') {
        stageNotes[stage].push('你选择自己改这一版——改完点「保存修改」即可')
      }
    }
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

/** spec 4：审稿判不稳的项（review.items 里 status==='不稳'） */
function unstableItems(): { name: string; problem: string }[] {
  return (review.value?.items || [])
    .filter((i) => i.status === '不稳')
    .map((i) => ({ name: i.name || '（未命名项）', problem: (i.problem || '').trim() }))
}

async function doFinalize() {
  if (busy.value) return
  const payoff = payoffChoice.value || 'none'
  const forceEnd = endChapterOverride.value
  const isEarlyEnd = (forceEnd === true && !autoAtEnd.value)
  const isSuppressEnd = (forceEnd === false && autoAtEnd.value)
  let confirmMsg =
    `定稿第 ${currentNo.value} 章？将自动执行保鲜飞轮：抽取实体、伏笔兑现问询、核验必现标记、整本合成采纳为新版本。` +
    `本章兑现将记为「${PAYOFF_OPTIONS.find((p) => p.value === payoff)!.label}」（可在上方四选一里改）。`
  if (isEarlyEnd) {
    confirmMsg += `\n\n⚠ 注意：当前章号未到计划章末，你勾选了「本章为本卷终章」——定稿后将直接进入杀青面板，卷内后续章节将不再续写。确定提前杀青？`
  } else if (isSuppressEnd) {
    confirmMsg += `\n\n⚠ 注意：这是本卷按计划的最后一章，但你未勾选「本章为本卷终章」——定稿后不会进入杀青面板，会继续追加下一章。确定不杀青、继续写？`
  }

  // spec 4：审稿有「不稳」项时必须逐条确认——后端在 blocked 且未带 acknowledge 时会 422。
  // 这里**不拦死**：作者有权明知不稳照样定稿，我们只负责让他知道。
  const unstable = unstableItems()
  let acknowledge: string[] | undefined
  if (unstable.length) {
    const itemsHtml = unstable
      .map(
        (i) =>
          `<li style="margin:4px 0"><b>${escapeHtml(i.name)}</b>` +
          (i.problem ? `<div style="font-size:12px;color:#606266;margin-top:2px">${escapeHtml(i.problem)}</div>` : '') +
          `</li>`,
      )
      .join('')
    const extra: string[] = []
    const rv = review.value as any
    if (rv?.book_warnings?.length) {
      extra.push(
        `<div style="margin-top:8px;font-size:12px;color:#c45656">书规报警 ${rv.book_warnings.length} 条：<br/>` +
          rv.book_warnings.slice(0, 3).map((w: string) => `· ${escapeHtml(w)}`).join('<br/>') +
          `</div>`,
      )
    }
    if (rv?.rhythm_warning) {
      extra.push(`<div style="font-size:12px;color:#e6a23c;margin-top:6px">${escapeHtml(rv.rhythm_warning)}</div>`)
    }
    try {
      await ElMessageBox.confirm(
        `<div><b>审稿判定本章有 ${unstable.length} 项不稳</b>，定稿前请确认你已知悉：</div>` +
          `<ul style="padding-left:20px;margin:8px 0;font-size:13px">${itemsHtml}</ul>` +
          extra.join('') +
          `<div style="font-size:12px;color:#909399;margin-top:10px">` +
          `确认知悉即可定稿——不稳项会记入账本，日后可回查。想先改就点「再改改」，` +
          `或对某一项跑「修复」后再定稿。</div>`,
        '定稿确认 · 有不稳项',
        {
          confirmButtonText: '我已知悉，仍要定稿',
          cancelButtonText: '再改改',
          dangerouslyUseHTMLString: true,
          type: 'warning',
          distinguishCancelAndClose: true,
        },
      )
    } catch {
      return
    }
    acknowledge = unstable.map((i) => i.name)
  } else {
    try {
      await ElMessageBox.confirm(confirmMsg, '定稿确认', {
        confirmButtonText: isEarlyEnd ? '确定提前杀青' : (isSuppressEnd ? '确定继续写' : '定稿'),
        cancelButtonText: '再改改',
        type: isEarlyEnd ? 'warning' : 'info',
        distinguishCancelAndClose: true,
      })
    } catch {
      return
    }
  }

  busy.value = 'finalize'
  try {
    lastFinalize.value = await deskApi.finalize(
      currentProjectId.value, currentNo.value, true, payoff, acknowledge,
    )
    payoffChoice.value = payoff
    ElMessage.success(`第 ${currentNo.value} 章已定稿，飞轮运转完成`)
    // 调 briefApi.advance 判断下一章 / 杀青；按用户勾选传 force_volume_end
    try {
      const advBody: any = { finalized: true }
      if (forceEnd === true) advBody.force_volume_end = true
      else if (forceEnd === false) advBody.force_volume_end = false
      const adv = await briefApi.advance(currentProjectId.value, activeVolumeNo.value, currentNo.value, advBody)
      lastAdvance.value = adv
      await loadOverview()
      if (adv.phase === 'settle' || adv.at_end) {
        emit('to-settle')
      } else if (adv.next_chapter != null) {
        if (!props.freeStanding) currentChapterNo.value = adv.next_chapter
        emit('next-chapter', adv.next_chapter)
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

async function doUnfinalize() {
  if (busy.value) return
  try {
    await ElMessageBox.confirm(
      `确定要把第 ${currentNo.value} 章从定稿状态撤回吗？正文/润色/审稿结果都会保留，只是退出定稿态，允许修改后重新定稿。`,
      '反定稿确认',
      { confirmButtonText: '反定稿', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  busy.value = 'unfinalize'
  try {
    await deskApi.unfinalize(currentProjectId.value, currentNo.value)
    ElMessage.success(`第 ${currentNo.value} 章已退出定稿态，可以继续修改`)
    await loadOverview()
    if (currentNo.value) await selectChapter(currentNo.value)
  } catch (e) {
    ElMessage.error(`反定稿失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  await loadOverview(false)
  loadRefProjects()
  loadModules()
  loadCurVolume()
})

watch(currentProjectId, () => {
  currentNo.value = 0
  detail.value = null
  overview.value = null
  deskPage.value = 1
  loadOverview(false)
  loadCurVolume()
})

watch(currentVolumeNo, () => {
  loadCurVolume()
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
                <span
                  v-if="c.chars"
                  class="wc-tag"
                  :class="wordStatus(c.chars).cls"
                  :title="wordStatus(c.chars).tip"
                >{{ c.chars }} 字</span>
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
        <!-- 上一步产物：节奏蓝图（本章对应的 day beat + 章前卡） -->
        <PrevStepPanel
          step-label="章台（剧情→正文→润色→定稿）"
          prev-step-label="节奏骨架 + 章前方向卡"
          :sections="[
            {
              title: '本章节奏蓝图（Day beat）',
              body: (() => {
                const bp = curVolume?.rhythm_blueprint
                const dayNo = detail?.chapter?.day_no
                const beats = (bp?.day_beats || []).find((d: any) => (d.day ?? d.day_no) === dayNo)
                  || (bp?.day_beats || [])[Math.min(detail?.chapter?.no - 1, 6)]
                if (!beats) return ''
                return [
                  `Day${beats.day || beats.day_no || '?'} · ${beats.title || ''}`,
                  `气氛：${beats.mood || ''}`,
                  '',
                  beats.summary || beats.events_text || ''
                ].filter(Boolean).join('\n')
              })(),
              bodyType: 'text'
            },
            {
              title: '本章大纲（节奏骨架自动生成）',
              body: detail?.outline || '',
              bodyType: 'text'
            }
          ]"
        />

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
                <el-tag v-if="currentBriefSelected.chapter_words_target" size="small" type="info" effect="plain" style="margin-left: 4px">
                  ~{{ currentBriefSelected.chapter_words_target }}字
                </el-tag>
                <el-tag v-if="currentBrief.is_custom" size="small" type="warning" effect="plain" style="margin-left: 4px">自编</el-tag>
              </div>
              <div style="font-size: 13px; color: #606266; line-height: 1.7; white-space: pre-wrap">
                {{ currentBriefSelected.summary }}
              </div>
              <div v-if="currentBriefSelected.hooks" style="font-size: 12px; color: #e6a23c; margin-top: 4px">
                🪝 {{ currentBriefSelected.hooks }}
              </div>

              <!-- 六要素（节奏骨架生成的章前细纲） -->
              <div
                v-if="currentBriefSelected.time_window || currentBriefSelected.location || (currentBriefSelected.entities_on_stage && currentBriefSelected.entities_on_stage.length) || currentBriefSelected.sensory_hook"
                style="margin-top: 10px; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 6px 14px; font-size: 12px; color: #606266; line-height: 1.6"
              >
                <div v-if="currentBriefSelected.time_window">
                  <span style="color:#909399">⏰ 时间</span>
                  <span style="margin-left:4px">{{ currentBriefSelected.time_window }}</span>
                </div>
                <div v-if="currentBriefSelected.location">
                  <span style="color:#909399">📍 地点</span>
                  <span style="margin-left:4px">{{ currentBriefSelected.location }}</span>
                </div>
                <div v-if="currentBriefSelected.day">
                  <span style="color:#909399">📅 日序</span>
                  <span style="margin-left:4px">入戏第{{ currentBriefSelected.day }}天</span>
                </div>
                <div v-if="currentBriefSelected.sensory_hook">
                  <span style="color:#909399">👁 感官</span>
                  <span style="margin-left:4px">{{ currentBriefSelected.sensory_hook }}</span>
                </div>
                <div v-if="currentBriefSelected.entities_on_stage && currentBriefSelected.entities_on_stage.length" style="grid-column: 1 / -1">
                  <span style="color:#909399">👥 登场</span>
                  <span style="margin-left:4px">
                    <el-tag v-for="c in currentBriefSelected.entities_on_stage" :key="c" size="small" effect="plain" style="margin: 2px 4px 2px 0">{{ c }}</el-tag>
                  </span>
                </div>
              </div>

              <!-- 起承转合：竖排成推进轨迹，读起来就是本章怎么走 -->
              <div
                v-if="visibleBeats(currentBriefSelected.plot_beats).length"
                style="margin-top: 10px; display: flex; flex-direction: column; gap: 4px"
              >
                <div
                  v-for="b in visibleBeats(currentBriefSelected.plot_beats)"
                  :key="b.key"
                  :style="{ background: b.bg, borderLeft: '3px solid ' + b.color, padding: '6px 10px', borderRadius: '3px' }"
                >
                  <div style="font-size: 12px; line-height: 1.6">
                    <b :style="{ color: b.color }">{{ b.label }}</b>
                    <span style="color: #a8abb2; font-size: 11px; margin-left: 4px">{{ b.hint }}</span>
                    <div style="color: #606266; margin-top: 2px; white-space: pre-wrap">{{ currentBriefSelected.plot_beats![b.key] }}</div>
                  </div>
                </div>
                <div
                  v-if="visibleBeats(currentBriefSelected.plot_beats).length < 4"
                  style="font-size: 11px; color: #e6a23c; line-height: 1.6"
                >
                  ⚠ 起承转合只填了 {{ visibleBeats(currentBriefSelected.plot_beats).length }}/4 段，缺段会导致正文节奏塌腰，点「换方向」重抽一版补齐。
                </div>
              </div>

              <!-- 伏笔 -->
              <div
                v-if="(currentBriefSelected.foreshadow_seeds && currentBriefSelected.foreshadow_seeds.length) || (currentBriefSelected.foreshadow_advance && currentBriefSelected.foreshadow_advance.length)"
                style="margin-top: 10px; font-size: 12px; line-height: 1.7"
              >
                <div v-if="currentBriefSelected.foreshadow_seeds && currentBriefSelected.foreshadow_seeds.length" style="color:#606266">
                  <span style="color:#67c23a">🌱 新埋伏笔</span>
                  <ul style="margin:4px 0 6px 20px; padding:0">
                    <li v-for="(s, i) in currentBriefSelected.foreshadow_seeds" :key="'seed'+i">{{ s }}</li>
                  </ul>
                </div>
                <div v-if="currentBriefSelected.foreshadow_advance && currentBriefSelected.foreshadow_advance.length" style="color:#606266">
                  <span style="color:#e6a23c">🔁 回收/推进</span>
                  <ul style="margin:4px 0 0 20px; padding:0">
                    <li v-for="(s, i) in currentBriefSelected.foreshadow_advance" :key="'pay'+i">{{ s }}</li>
                  </ul>
                </div>
              </div>

              <!-- 卷首/卷尾钩子（仅首章/末章显示） -->
              <div v-if="currentBriefSelected.volume_opening_hook" style="margin-top: 10px; padding:8px 10px; background:linear-gradient(135deg, #fff4e6 0%, #ffe4c4 100%); border-radius:4px; font-size:12px; line-height:1.7">
                <b style="color:#e6a23c">🎬 卷首钩子</b>
                <div style="color:#606266; margin-top:3px; white-space:pre-wrap">{{ currentBriefSelected.volume_opening_hook }}</div>
              </div>
              <div v-if="currentBriefSelected.volume_ending_hook" style="margin-top: 8px; padding:8px 10px; background:linear-gradient(135deg, #ecf5ff 0%, #d9ecff 100%); border-radius:4px; font-size:12px; line-height:1.7">
                <b style="color:#409eff">🎞️ 卷尾钩子</b>
                <div style="color:#606266; margin-top:3px; white-space:pre-wrap">{{ currentBriefSelected.volume_ending_hook }}</div>
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
          <!-- 作者批注（【】标注）用法说明：只在润色卡显示 -->
          <el-alert
            v-if="s.key === 'polish'"
            type="info"
            :closable="false"
            show-icon
            style="margin-bottom: 10px"
          >
            <template #title>可以直接在正文里写【】批注，润色时会被识别并优先落实</template>
            <div style="font-size: 12px; line-height: 1.8">
              <div>· <b>改这一段</b>：把【】写在<b>下一行</b> —— <code>【这一段有问题，改成他踹开门】</code>（作用于上面那段）</div>
              <div>· <b>在这里加内容</b>：<code>【这里要增加他检查弹匣的动作】</code>（写完批注后再补一段正文，批注会挂在这段之后）</div>
              <div>· <b>行内批注</b>：跟在句末 —— <code>他推开门。【改成踹开门】</code>（作用于本段）</div>
              <div>· <b>指远处的段</b>：<code>【第5段：改成这里要有一场雨】</code></div>
              <div>· <b>删掉某段</b>：<code>【删掉这一段】</code></div>
              <div style="color: #909399; margin-top: 4px">
                写完点「保存修改」，再点「重新生成」润色——批注会被剥离并转成指令，成稿里不会留下【】。
              </div>
            </div>
          </el-alert>
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
              <span style="color: #999; font-size: 12px">九项一致性检查：情节逻辑 / 人物目标 / 情感关系 / 物理信息 / 场景过渡 / 章尾钩子 / 跨章联结 / 信息揭露节奏 / 克苏鲁白描（空泛套话锁）</span>
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
              v-if="(review.cliche_hits?.length ?? 0) > 0"
              type="error"
              :closable="false"
              style="margin-top: 6px"
              title="白描锁命中空泛克苏鲁套话（强制不稳）——需改为温度/触觉、气味、解剖错位、光影声延迟、程序员量化观察之一"
            >
              <div v-for="(h, i) in review.cliche_hits" :key="i" style="margin-top: 4px; font-size: 12px; font-family: monospace">
                <b style="color: #f56c6c">{{ h.word }}</b>：{{ h.context }}
              </div>
            </el-alert>
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
              <el-button
                v-if="detail.chapter.finalized_at"
                type="warning"
                plain
                size="small"
                :loading="busy === 'unfinalize'"
                :disabled="!!busy"
                @click="doUnfinalize"
              >
                反定稿
              </el-button>
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
          <div style="margin-bottom: 10px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px">
            <el-checkbox
              :model-value="endChapterChecked"
              :indeterminate="endChapterIndeterminate"
              size="small"
              :disabled="!!detail.chapter.finalized_at"
              @change="(v: any) => { endChapterChecked = !!v }"
            >
              本章已是本卷终章（定稿后直接进入杀青面板）
            </el-checkbox>
            <el-tag v-if="autoAtEnd && hasRhythmBlueprint" size="small" type="success" effect="plain">按节奏蓝图，本章即卷末</el-tag>
            <el-tag v-else-if="autoAtEnd" size="small" type="success" effect="plain">
              本卷共 {{ volumeTotalCount }} 章，本章是本卷最后一章
            </el-tag>
            <el-tag v-else-if="volumeTotalCount" size="small" type="info" effect="plain">
              本卷共 {{ volumeTotalCount }} 章，当前第 {{ volumeSeqNo }} 章
            </el-tag>
            <el-tag v-if="endChapterOverride === true && !autoAtEnd" size="small" type="warning" effect="plain">将提前杀青</el-tag>
            <el-tag v-if="endChapterOverride === false && autoAtEnd" size="small" type="warning" effect="plain">将压制杀青，继续写下一章</el-tag>
            <span style="font-size: 12px; color: #999">
              默认按节奏蓝图自动判定；提前勾选 = 强制杀青；计划章末不勾选 = 继续写
            </span>
          </div>
          <div v-if="lastAdvance" style="margin-bottom: 10px; font-size: 12px; color: #909399">
            上次推进结果：
            <el-tag size="small" :type="lastAdvance.at_end ? 'success' : 'info'" effect="plain">
              {{ lastAdvance.at_end ? '已到卷末' : '未到卷末' }}
            </el-tag>
            <el-tag v-if="lastAdvance.force_state" size="small" type="info" effect="plain" style="margin-left: 4px">
              判定：{{ { auto: '按蓝图自动', forced: '强制杀青', suppressed: '压制杀青' }[lastAdvance.force_state] || lastAdvance.force_state }}
            </el-tag>
            <el-tag v-if="lastAdvance.next_chapter != null" size="small" type="info" effect="plain" style="margin-left: 4px">
              下一章：第 {{ lastAdvance.next_chapter }} 章
            </el-tag>
            <el-tag v-else-if="lastAdvance.phase" size="small" type="info" effect="plain" style="margin-left: 4px">
              下一阶段：{{ lastAdvance.phase }}
            </el-tag>
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
              <!-- 新伏笔检出：定稿回账的另一半。上一块只问「账上这些还了没有」，
                   这一块补的是「本章正文里现挖了哪些账上没有的坑」——
                   不补登记的话，这些坑永远不在任何清单里，也就永远不会被催、不会被回收。 -->
              <div v-if="lastFinalize.foreshadow_detected">
                本章新埋：
                <template v-if="lastFinalize.foreshadow_detected.error">
                  <el-tag type="warning" size="small">未完成：{{ lastFinalize.foreshadow_detected.error }}</el-tag>
                </template>
                <template v-else>
                  <el-tag
                    v-if="!(lastFinalize.foreshadow_detected.planted ?? []).length"
                    type="info" size="small"
                  >未检出新伏笔</el-tag>
                  <el-tag
                    v-for="p in lastFinalize.foreshadow_detected.planted"
                    :key="'np-' + p.name"
                    type="primary" size="small"
                    style="margin-right: 6px"
                    :title="p.evidence || ''"
                  >🪝 {{ p.name }}（已入账）</el-tag>
                </template>
                <div v-if="lastFinalize.foreshadow_detected.warning" style="color: #e6a23c">
                  ⚠ {{ lastFinalize.foreshadow_detected.warning }}
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
.wc-tag {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
}
.wc-none { color: #c0c4cc; }
.wc-short { color: #b88230; background: #fdf6ec; }   /* 过短 <3500 — 黄 */
.wc-near-low { color: #b88230; }                     /* 略短 3500-3999 */
.wc-ok { color: #67c23a; background: #f0f9eb; }      /* 达标 4000-5000 — 绿 */
.wc-near-high { color: #b88230; }                    /* 略长 5001-5500 */
.wc-long { color: #ff8c00; background: #fff3e6; }    /* 过长 >5500 — 橙 */
</style>
