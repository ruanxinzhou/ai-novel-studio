<script setup lang="ts">
// 创作会话页（P5）：左栏会话列表 + 右栏对话 + 任务卡
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Delete, ArrowDown } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AI_MODE_LABELS,
  PHASE_TITLES,
  sessionsApi,
  jobsApi,
  type SessionMeta,
  type SessionMessage,
  type SessionState,
  type Job,
  type JobKind,
  type Pdf5Variant,
  type CocVariant,
  type Outline5Variant,
} from '../api'
import { activeTab, currentProjectId } from '../state'
import SushiCards from '../components/SushiCards.vue'
import JobProgressBar from '../components/JobProgressBar.vue'
import VariantPicker from '../components/VariantPicker.vue'

// 回转寿司支持的卡片（M2+杀青卡）
const SUSHI_KEYS = new Set(['core', 'obituary_card', 'entity_card', 'wrapup'])
const SUSHI_LABELS: Record<string, string> = {
  core: '副本核',
  obituary_card: '讣告卡',
  entity_card: '实体卡',
  wrapup: '杀青/残页',
}
const SUSHI_AXIS: Record<string, string> = {
  core: '五版差异化副本骨架：题材/诡计/替换角色/恐怖来源/节奏自由组合，不必受分类限制',
  obituary_card: '三版讣告：送达/死因写法/进场钩子自由组合',
  entity_card: '一次出 3 个功能互补的角色/邪物，可反复加',
  wrapup: '三版杀青：冷收/余味/刀糖（可混合）',
}
const SUSHI_PLATE_COUNT: Record<string, number> = {
  core: 5,
  obituary_card: 3,
  entity_card: 3,
  wrapup: 3,
}
// kindle 阶段的 core_pitch 在回转寿司里用 card_key=core 生成，后端 lock 时会自动把文本写到 outputs.core_pitch
const KINDLE_CORE_ALIAS = { outputKey: 'core_pitch', cardKey: 'core' }

const sessions = ref<SessionMeta[]>([])
const activeId = ref<string>('')
const state = ref<SessionState | null>(null)
const sending = ref(false)
const input = ref('')
const draftWordCount = ref(0)
const scopeFilter = ref<'all' | 'field' | 'chapter'>('all')
const chatRef = ref<HTMLElement | null>(null)

// chapter_plan 本地编辑副本（数组），同步后回写 gate
type PlanRow = { no: number; title: string; day: string; summary: string }
const chapterPlan = ref<PlanRow[]>([])

function syncPlanFromState() {
  const raw = state.value?.outputs?.chapter_plan
  if (Array.isArray(raw)) chapterPlan.value = raw.map((x: any) => ({
    no: Number(x.no) || 0, title: String(x.title || ''),
    day: String(x.day || ''), summary: String(x.summary || ''),
  }))
  else chapterPlan.value = []
}
watch(state, syncPlanFromState, { immediate: true })

function addPlanRow() {
  const nextNo = chapterPlan.value.reduce((m, r) => Math.max(m, r.no || 0), 0) + 1
  chapterPlan.value.push({ no: nextNo, title: '', day: '', summary: '' })
}
function removePlanRow(i: number) { chapterPlan.value.splice(i, 1) }
async function saveChapterPlan() {
  // 去掉空标题/空 no 的行
  const clean = chapterPlan.value.filter(r => r.no > 0)
  await setGate('chapter_plan', clean)
}

async function refreshList() {
  if (!currentProjectId.value) { sessions.value = []; return }
  try {
    const filter = scopeFilter.value === 'all' ? {} : { scope: scopeFilter.value }
    sessions.value = (await sessionsApi.list(currentProjectId.value, filter)).sessions
  } catch (e) {
    ElMessage.error(`会话列表加载失败：${(e as Error).message}`)
  }
}

async function openSession(sid: string) {
  if (!currentProjectId.value || !sid) return
  activeId.value = sid
  try {
    state.value = (await sessionsApi.get(currentProjectId.value, sid)).session
    scrollToBottom()
  } catch (e) {
    ElMessage.error(`打开会话失败：${(e as Error).message}`)
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (chatRef.value) chatRef.value.scrollTop = chatRef.value.scrollHeight
  })
}

watch(currentProjectId, () => { refreshList(); activeId.value=''; state.value=null })
watch(scopeFilter, refreshList)
onMounted(refreshList)
// 从章台跳过来时 __sessionFocus 带了 sid，自动打开
watch(
  () => (window as any).__sessionFocus as string | undefined,
  async (sid) => {
    if (!sid || !currentProjectId.value) return
    await refreshList()
    await openSession(sid)
    ;(window as any).__sessionFocus = ''
  },
  { immediate: true },
)

// ---- 新建会话 ----
const createDialog = ref(false)
const newScope = ref<'field' | 'chapter'>('field')
const newFieldNo = ref<number>(1)
const newChapter = ref<number>(1)
const newTitle = ref('')

async function doCreate() {
  if (!currentProjectId.value) return
  try {
    const body: Record<string, unknown> = { scope: newScope.value, title: newTitle.value || undefined }
    if (newScope.value === 'field') body.field_no = newFieldNo.value
    else body.chapter = newChapter.value
    const r = await sessionsApi.create(currentProjectId.value, body as any)
    state.value = r.session
    activeId.value = r.session.id
    createDialog.value = false
    newTitle.value = ''
    await refreshList()
  } catch (e) {
    ElMessage.error(`创建失败：${(e as Error).message}`)
  }
}

function openCreate(scope: 'field' | 'chapter') {
  newScope.value = scope
  createDialog.value = true
}

async function startFreeChat() {
  if (!currentProjectId.value) return
  // 自由聊：创建一个不绑具体阶段/章节的副本级会话，直接放 kindle 态（不设 field_no 走不下去，先放个 999 的占位）
  // 提示：自由聊里没有门控，想进正式流程点"新建月规划"。
  try {
    const r = await sessionsApi.create(currentProjectId.value, {
      scope: 'field',
      field_no: 999,
      title: '自由聊·' + new Date().toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '-'),
    })
    // 把开场的 settle_up welcome 换成自由聊提示
    state.value = r.session
    activeId.value = r.session.id
    await refreshList()
    ElMessage.info('自由聊已开，想到啥说啥。想进正式流程点左侧「+ 新建→月规划」。')
  } catch (e) {
    ElMessage.error(`创建失败：${(e as Error).message}`)
  }
}

// ---- 发送消息 ----
async function send(text?: string, outputKey?: string) {
  if (!currentProjectId.value || !activeId.value) return
  const t = (text ?? input.value).trim()
  if (!t) return
  sending.value = true
  try {
    const r = await sessionsApi.send(currentProjectId.value, activeId.value, {
      text: t, ...(outputKey ? { output_key: outputKey } : {}),
    })
    state.value = r.session
    input.value = ''
    scrollToBottom()
    await refreshList()
  } catch (e) {
    ElMessage.error(`发送失败：${(e as Error).message}`)
  } finally {
    sending.value = false
  }
}

// ---- gate ----
async function setGate(key: string, value: unknown) {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await sessionsApi.gate(currentProjectId.value, activeId.value, key, value)
    state.value = r.session
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function advance() {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await sessionsApi.advance(currentProjectId.value, activeId.value)
    state.value = r.session
    // schedule 锁定后自动跳到章台
    const created = (r.session as any).messages?.findLast(
      (m: any) => m.meta?.created_chapters
    )?.meta?.created_chapters as { no: number }[] | undefined
    if (created && created.length) {
      ElMessage.success(`已写入章台，跳去写第 ${created[0].no} 章`)
      ;(window as any).__deskFocus = { value: created[0].no }
      activeTab.value = 'desk'
      return
    }
    ElMessage.success(`已进入：${r.session.phase_meta.title}`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function rollback() {
  if (!currentProjectId.value || !state.value) return
  try {
    const { value } = await ElMessageBox.prompt('回退到哪个阶段？(settle_up/kindle/scaffold/walkthrough/schedule/daily_write)', '回退', {
      confirmButtonText: '回退',
      cancelButtonText: '取消',
      inputValue: state.value.current_phase,
    })
    const r = await sessionsApi.rollback(currentProjectId.value, activeId.value, value)
    state.value = r.session
  } catch { /* cancelled */ }
}

async function abandon() {
  if (!currentProjectId.value || !activeId.value) return
  try {
    await ElMessageBox.confirm('确定放弃这个会话吗？', '提示', { type: 'warning' })
    const r = await sessionsApi.abandon(currentProjectId.value, activeId.value)
    state.value = r.session
    await refreshList()
  } catch { /* cancelled */ }
}

// ---- actions ----
const actionLoading = ref(false)
async function doAction(action: string, args?: Record<string, unknown>, nVariants?: number) {
  if (!currentProjectId.value || !activeId.value) return
  actionLoading.value = true
  try {
    const r = await sessionsApi.action(currentProjectId.value, activeId.value,
      { action, args, n_variants: nVariants })
    state.value = r.session
    ElMessage.success('完成')
    scrollToBottom()
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    actionLoading.value = false
  }
}

// ---- 回转寿司（M2）----
const sushiLoading = ref<string>('')   // 当前正在生成的 card_key
const pdfUploading = ref(false)
const pdfUploadStage = ref('')
const pdfHint = ref('')

// ---- 后台长任务（jobs）状态 ----
const jobs = ref<Job[]>([])
let jobsSse: { close: () => void } | null = null

function upsertJob(job: Job) {
  const idx = jobs.value.findIndex((j) => j.job_id === job.job_id)
  if (idx >= 0) jobs.value[idx] = job
  else jobs.value = [job, ...jobs.value]
}
function replaceJobs(list: Job[]) {
  jobs.value = list
}
function findActiveJob(kind: JobKind): Job | undefined {
  return jobs.value.find((j) => j.kind === kind && (j.status === 'pending' || j.status === 'running'))
}
function findDoneJob(kind: JobKind): Job | undefined {
  return [...jobs.value]
    .filter((j) => j.kind === kind && j.status === 'done')
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))[0]
}

async function loadJobs() {
  if (!currentProjectId.value || !activeId.value) { jobs.value = []; return }
  try {
    const r = await jobsApi.list(currentProjectId.value, activeId.value)
    replaceJobs(r.jobs ?? [])
  } catch (e) {
    // 不打断主流程
    console.warn('loadJobs failed', e)
  }
}

function subscribeJobsSSE() {
  if (!currentProjectId.value || !activeId.value) return
  jobsSse?.close()
  jobsSse = jobsApi.subscribe(currentProjectId.value, activeId.value, (ev) => {
    if (ev.type === 'job_init' && ev.jobs) {
      replaceJobs(ev.jobs)
    } else if (ev.type === 'job_update' && ev.job) {
      const wasDone = jobs.value.find((j) => j.job_id === ev.job!.job_id)?.status === 'done'
      upsertJob(ev.job)
      // done 时刷新 session 以便 UI 同步 outputs（如 PDF 改编选中后写回 source_material）
      if (!wasDone && ev.job.status === 'done') {
        sessionsApi.get(currentProjectId.value!, activeId.value!)
          .then((r) => { state.value = r.session })
          .catch(() => {})
        ElMessage.success(`任务完成：${ev.job.kind}`)
      }
      if (ev.job.status === 'error') {
        ElMessage.error(`任务失败：${ev.job.error || ev.job.message || ev.job.kind}`)
      }
    }
  })
}

function closeJobsSSE() {
  jobsSse?.close()
  jobsSse = null
}

// 切换会话时重载 jobs + 重连 SSE
watch(
  () => activeId.value,
  async () => {
    closeJobsSSE()
    jobs.value = []
    if (activeId.value && currentProjectId.value) {
      await loadJobs()
      subscribeJobsSSE()
    }
  },
)
watch(currentProjectId, () => { closeJobsSSE(); jobs.value = [] })
onUnmounted(closeJobsSSE)

// 正在跑（pending/running）的 jobs 列表，用于底部进度区
const activeJobs = computed(() =>
  jobs.value.filter((j) => j.status === 'pending' || j.status === 'running'),
)

// PDF 5 版上传：改用 jobsApi.uploadPdf5，进度由 SSE 驱动
const pdfUploadPct = ref(0)
async function doUploadPdf5(file: File) {
  if (!currentProjectId.value || !activeId.value) return
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.error('只支持 .pdf 文件')
    return
  }
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.error('PDF 过大（>20MB），请先压缩')
    return
  }
  pdfUploading.value = true
  pdfUploadPct.value = 0
  pdfUploadStage.value = '正在上传 PDF…'
  try {
    const r = await jobsApi.uploadPdf5(
      currentProjectId.value, activeId.value, file, pdfHint.value,
      (pct) => { pdfUploadPct.value = pct },
    )
    upsertJob(r.job)
    // SSE 会推送后续进度；先给个立即可见的 job
    ElMessage.info('已提交 5 版改编任务，进度见下方任务栏')
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    pdfUploading.value = false
    pdfUploadStage.value = ''
  }
}

// 最近一次 pdf5 完成的 job → 渲染 VariantPicker
const pdf5DoneJob = computed(() => findDoneJob('pdf5'))
const pdf5PickerItems = computed<Pdf5Variant[]>(() => {
  const r = pdf5DoneJob.value?.result
  return Array.isArray(r) ? (r as Pdf5Variant[]) : []
})
// 是否已选中（session.source_material 已回写即视为已选）
const pdf5Picked = computed(() => !!state.value?.source_material?.premise)

async function pickPdf5Variant(idx: number) {
  if (!currentProjectId.value || !activeId.value || !pdf5DoneJob.value) return
  try {
    const r = await jobsApi.pick(currentProjectId.value, activeId.value, pdf5DoneJob.value.job_id, idx)
    upsertJob(r.job)
    const sr = await sessionsApi.get(currentProjectId.value, activeId.value)
    state.value = sr.session
    ElMessage.success('已选定该版改编稿')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// COC 批量模拟按钮 + 表单
const cocDialog = ref(false)
const cocTangchiReplaceIdx = ref<number | null>(null)  // null = 不替换
const cocUserDirective = ref('')
const cocMaxPairs = ref(8)
const cocMaxTurns = ref(18)

const cocPlayerCards = computed<{ name: string; idx: number }[]>(() => {
  const raw = state.value?.source_material?.renamed_cast ?? []
  return raw.map((c: any, i: number) => ({
    name: typeof c === 'string' ? c : (c?.name ?? c?.alias ?? `角色${i + 1}`),
    idx: i,
  }))
})
const cocDoneJob = computed(() => findDoneJob('coc_batch'))
const cocPickerItems = computed<CocVariant[]>(() => {
  const r = cocDoneJob.value?.result
  return Array.isArray(r) ? (r as CocVariant[]) : []
})
const cocPickedIdx = ref<number>(-1)

async function submitCocJob() {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await jobsApi.startCoc(currentProjectId.value, activeId.value, {
      tangchi_replace_idx: cocTangchiReplaceIdx.value,
      user_directive: cocUserDirective.value || undefined,
      max_pairs_per_session: cocMaxPairs.value,
      max_turns_per_session: cocMaxTurns.value,
    })
    upsertJob(r.job)
    cocDialog.value = false
    cocPickedIdx.value = -1
    ElMessage.info('已提交 COC 三场模拟，进度见下方任务栏')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function pickCocVariant(idx: number) {
  cocPickedIdx.value = idx
  ElMessage.success(`已标记 COC 方案 #${idx + 1}，下方可启动「唐迟大纲 5 版」`)
}

// 唐迟大纲 5 版
const outlineDialog = ref(false)
const outlineTangchiRole = ref('唐迟式悬疑推进者')
const outlineMustHave = ref('')
const outlineUserDirective = ref('')

const outlineDoneJob = computed(() => findDoneJob('outline5'))
const outlinePickerItems = computed<Outline5Variant[]>(() => {
  const r = outlineDoneJob.value?.result
  return Array.isArray(r) ? (r as Outline5Variant[]) : []
})

async function submitOutlineJob() {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await jobsApi.startOutline5(currentProjectId.value, activeId.value, {
      tangchi_role: outlineTangchiRole.value || undefined,
      must_have: outlineMustHave.value || undefined,
      user_directive: outlineUserDirective.value || undefined,
      selected_coc_idx: cocPickedIdx.value >= 0 ? cocPickedIdx.value : null,
      n: 5,
    })
    upsertJob(r.job)
    outlineDialog.value = false
    ElMessage.info('已提交唐迟大纲 5 版任务')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function pickOutlineVariant(idx: number) {
  if (!currentProjectId.value || !activeId.value || !outlineDoneJob.value) return
  try {
    const r = await jobsApi.pick(currentProjectId.value, activeId.value, outlineDoneJob.value.job_id, idx)
    upsertJob(r.job)
    const sr = await sessionsApi.get(currentProjectId.value, activeId.value)
    state.value = sr.session
    ElMessage.success('已选定该版大纲，已写入会话')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function cancelJobById(jobId: string) {
  if (!currentProjectId.value || !activeId.value) return
  try {
    await jobsApi.cancel(currentProjectId.value, activeId.value, jobId)
    ElMessage.success('已发送取消请求')
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

function pickPdf() {
  const inp = document.createElement('input')
  inp.type = 'file'
  inp.accept = '.pdf,application/pdf'
  inp.onchange = async () => {
    const f = inp.files?.[0]
    if (!f) return
    await doUploadPdf5(f)
  }
  inp.click()
}

// 计算属性：改编源（带空值保护）
const pdfSrc = computed(() => state.value?.source_material ?? null)

// @ts-expect-error 保留供未来调试使用
async function _doUploadPdfLegacy(file: File) {
  if (!currentProjectId.value || !activeId.value) return
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    ElMessage.error('只支持 .pdf 文件')
    return
  }
  if (file.size > 20 * 1024 * 1024) {
    ElMessage.error('PDF 过大（>20MB），请先压缩')
    return
  }
  pdfUploading.value = true
  pdfUploadStage.value = '正在上传 PDF…'
  try {
    const r = await sessionsApi.uploadSourcePdf(
      currentProjectId.value, activeId.value, file, pdfHint.value,
      (stage: string) => { pdfUploadStage.value = stage },
    )
    state.value = r.session
    ElMessage.success(`改编完成：${r.adapt.renamed_cast.length} 个角色改名，${r.adapt.changed_elements.length} 处事件改动`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    pdfUploading.value = false
    pdfUploadStage.value = ''
  }
}

function sushiCardKeysForPhase(): string[] {
  // kindle: core；scaffold: obituary_card + entity_card；settle_up: wrapup
  if (!state.value) return []
  if (state.value.current_phase === 'kindle') return [KINDLE_CORE_ALIAS.cardKey]
  if (state.value.current_phase === 'scaffold') return ['obituary_card', 'entity_card']
  if (state.value.current_phase === 'settle_up') return ['wrapup']
  return []
}

// outputKey ↔ cardKey 映射：core↔core_pitch；其他同名
function toCardKey(outputKey: string): string {
  if (outputKey === KINDLE_CORE_ALIAS.outputKey) return KINDLE_CORE_ALIAS.cardKey
  return outputKey
}
function toOutputKey(cardKey: string): string {
  if (cardKey === KINDLE_CORE_ALIAS.cardKey) return KINDLE_CORE_ALIAS.outputKey
  return cardKey
}

function isEditingThisCard(cardKey: string) {
  return state.value?.editing?.card_key === cardKey
}
function sushiCardOpacity(cardKey: string): number {
  const s = state.value
  if (!s) return 1
  if (!s.editing) return 1
  if (isEditingThisCard(cardKey)) return 1
  // 其他卡在编辑中时压暗（只有被编辑的那张高亮）
  return 0.45
}
function lockedTextOf(cardKey: string): string | null {
  if (!state.value) return null
  const msgId = state.value.locked_cards?.[cardKey]
  if (!msgId) return null
  // 锁定后 outputs 里有最终文本
  const v = state.value.outputs[toOutputKey(cardKey)]
  return typeof v === 'string' && v.trim() ? v : null
}

async function sushiGenerate(cardKey: string, hint?: string) {
  if (!currentProjectId.value || !activeId.value || !state.value) return
  sushiLoading.value = cardKey
  try {
    const r = await sessionsApi.generateCandidates(currentProjectId.value, activeId.value, {
      card_key: cardKey,
      phase: state.value.current_phase,
      context_hint: hint || '',
    })
    state.value = r.session
    ElMessage.success(`已上 ${r.candidates.length} 盘`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  } finally {
    sushiLoading.value = ''
  }
}

async function sushiPick(cardKey: string, candidateId: string | null) {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await sessionsApi.pickCandidate(currentProjectId.value, activeId.value, {
      card_key: cardKey,
      candidate_id: candidateId ?? null,
    })
    state.value = r.session
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function sushiUpdateText(text: string) {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await sessionsApi.editingUpdate(currentProjectId.value, activeId.value, text)
    state.value = r.session
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function sushiAiSuggest(instruction: string, apply: boolean) {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await sessionsApi.editingAiSuggest(currentProjectId.value, activeId.value, {
      instruction,
      apply,
    })
    state.value = r.session
    if (apply) ElMessage.success('AI 改稿已应用')
    else ElMessage.success('已出建议')
    scrollToBottom()
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function sushiLock() {
  if (!currentProjectId.value || !activeId.value || !state.value?.editing) return
  const cardKey = state.value.editing.card_key
  const text = state.value.editing.current_text
  try {
    const r = await sessionsApi.lockCard(currentProjectId.value, activeId.value, {
      card_key: cardKey,
      text,
      scope: state.value.scope,
      field_no: state.value.field_no ?? null,
      chapter: state.value.chapter ?? null,
    })
    state.value = r.session
    ElMessage.success(`「${SUSHI_LABELS[cardKey] || cardKey}」已锁定`)
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

async function sushiEndEditing() {
  if (!currentProjectId.value || !activeId.value) return
  try {
    const r = await sessionsApi.editingEnd(currentProjectId.value, activeId.value)
    state.value = r.session
  } catch (e) {
    ElMessage.error((e as Error).message)
  }
}

// ---- helpers ----
const requiredOutputs = computed(() => {
  if (!state.value) return [] as { key: string; desc: string; filled: boolean }[]
  const req = state.value.phase_meta.required_outputs || {}
  return Object.entries(req).map(([k, desc]) => ({
    key: k,
    desc,
    filled: (() => {
      const v = state.value!.outputs[k]
      if (v == null) return false
      if (typeof v === 'string') return !!v.trim()
      if (typeof v === 'boolean') return v
      if (Array.isArray(v)) return v.length > 0
      if (typeof v === 'object') return Object.keys(v as object).length > 0
      return true
    })(),
  }))
})

const missingList = computed(() => state.value?.missing_outputs ?? [])
const canAdvance = computed(() => state.value?.can_advance ?? false)
const isActive = computed(() => state.value?.status === 'active')

function bubbleClass(m: SessionMessage) {
  return {
    'bubble': true,
    'bubble-author': m.role === 'author',
    'bubble-ai': m.role === 'ai',
    'bubble-sys': m.role === 'system',
  }
}
</script>

<template>
  <div class="session-view-root">
  <el-row :gutter="12" v-if="!currentProjectId" style="padding: 40px; color: #888; text-align: center">
    请先在顶部选一个小说项目
  </el-row>

  <el-row :gutter="12" v-else>
    <!-- 左：会话列表 -->
    <el-col :span="6">
      <el-card shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>创作会话</span>
            <el-dropdown trigger="click">
              <el-button size="small" type="primary">+ 新建<el-icon class="el-icon--right"><arrow-down /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item @click="openCreate('field')">月规划（副本级）</el-dropdown-item>
                  <el-dropdown-item @click="openCreate('chapter')">日更对话（章节级）</el-dropdown-item>
                  <el-dropdown-item divided @click="startFreeChat">自由聊（不上流程）</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <el-radio-group v-model="scopeFilter" size="small" style="margin-top: 6px">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="field">月规划</el-radio-button>
            <el-radio-button value="chapter">日更</el-radio-button>
          </el-radio-group>
        </template>
        <div v-if="!sessions.length" style="color: #aaa; font-size: 13px">暂无会话，点右上角「新建」。</div>
        <div
          v-for="s in sessions"
          :key="s.id"
          class="ses-item"
          :class="{ active: s.id === activeId }"
          @click="openSession(s.id)"
        >
          <div style="display: flex; justify-content: space-between; align-items: center">
            <b>{{ s.title || '未命名' }}</b>
            <el-tag size="small" :type="s.scope === 'field' ? 'primary' : 'success'">
              {{ s.scope === 'field' ? `第${s.field_no}场规划` : `第${s.chapter}章日更` }}
            </el-tag>
          </div>
          <div style="font-size: 12px; color: #888; margin-top: 4px">
            {{ PHASE_TITLES[s.current_phase] ?? s.current_phase }}
            <el-tag size="small" v-if="s.status !== 'active'" type="info" style="margin-left: 4px">{{ s.status }}</el-tag>
            <el-tag size="small" v-else-if="s.can_advance" type="success" style="margin-left: 4px">可推进</el-tag>
          </div>
          <div style="font-size: 11px; color: #bbb; margin-top: 2px">{{ s.updated_at }}</div>
        </div>
      </el-card>
    </el-col>

    <!-- 中：对话区 -->
    <el-col :span="12">
      <el-card v-if="!state" shadow="never" style="text-align: center; color: #aaa; padding: 40px 0">
        从左侧选择一个会话，或新建一个。
      </el-card>
      <el-card v-else shadow="never" :body-style="{ padding: 0 }">
        <div style="padding: 12px 16px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between; align-items: center">
          <div>
            <b>{{ state.title || '会话' }}</b>
            <el-tag size="small" style="margin-left: 8px" type="warning">{{ state.phase_meta.title }}</el-tag>
            <el-tag size="small" style="margin-left: 6px">AI · {{ AI_MODE_LABELS[state.phase_meta.ai_mode] ?? state.phase_meta.ai_mode }}</el-tag>
          </div>
          <div>
            <el-button size="small" @click="rollback" :disabled="!isActive">回退</el-button>
            <el-button size="small" type="danger" plain @click="abandon">放弃</el-button>
          </div>
        </div>
        <div ref="chatRef" class="chat-area">
          <div v-for="m in state.messages" :key="m.id" :class="bubbleClass(m)">
            <div class="bubble-meta">
              <span class="bubble-role">{{ m.role === 'author' ? '我' : m.role === 'ai' ? 'AI' : '系统' }}</span>
              <span class="bubble-kind" v-if="m.kind !== 'chat'">· {{ m.kind }}</span>
              <span class="bubble-time">{{ m.created_at }}</span>
            </div>
            <div class="bubble-text" v-html="(m.text || '').replace(/\n/g, '<br>')" />
          </div>
        </div>
        <div style="padding: 10px 16px; border-top: 1px solid #eee">
          <!-- kindle 阶段：保留 COC 仿真作为灵感刺激；副本核在右栏回转寿司里写 -->
          <div v-if="state.current_phase === 'kindle' && isActive" style="margin-bottom: 8px; display: flex; gap: 8px; flex-wrap: wrap">
            <el-button size="small" :loading="actionLoading" @click="doAction('kindle_diverge', undefined, 3)">
              跑 3 场 COC 仿真（灵感素材·旧）
            </el-button>
            <span style="color: #888; font-size: 12px; align-self: center">给方向刺激，推荐使用右栏「PDF 5版改编」进入新流程</span>
          </div>
          <div v-if="state.current_phase === 'walkthrough' && isActive" style="margin-bottom: 8px">
            <el-button size="small" :loading="actionLoading" @click="doAction('walkthrough_step')">朗读下一段</el-button>
          </div>
          <div v-if="state.current_phase === 'schedule' && isActive" style="margin-bottom: 8px; display: flex; gap: 8px; flex-wrap: wrap">
            <el-button size="small" type="primary" :loading="actionLoading" @click="doAction('plan_chapters')">AI 拆章（给模板）</el-button>
            <span style="color: #888; font-size: 12px; align-self: center">按 Day1-7 + 间章给个初表，你改完再锁定</span>
          </div>
          <div v-if="state.current_phase === 'daily_write' && isActive" style="margin-bottom: 8px">
            <el-button size="small" :loading="actionLoading" @click="doAction('daily_summon')">召唤（3 个方向）</el-button>
          </div>
          <div style="display: flex; gap: 8px">
            <el-input v-model="input" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }"
                      placeholder="说点什么…（Enter 发送，Shift+Enter 换行）"
                      @keydown.enter.exact.prevent="send()" :disabled="!isActive || sending" />
            <el-button type="primary" :loading="sending" @click="send()" :disabled="!isActive">发送</el-button>
          </div>
        </div>
      </el-card>
    </el-col>

    <!-- 右：任务卡 / 门控 -->
    <el-col :span="6" v-if="state">
      <el-card shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>任务卡</span>
            <el-button size="small" type="success" :disabled="!canAdvance || !isActive" @click="advance">推进到下一阶段</el-button>
          </div>
        </template>
        <el-alert
          :title="state.phase_meta.title"
          :description="state.phase_meta.prompt"
          type="info"
          :closable="false"
          style="margin-bottom: 12px"
        />
        <div v-if="missingList.length" style="margin-bottom: 8px">
          <div style="font-size: 12px; color: #c00; margin-bottom: 6px">还差：</div>
          <ul style="margin: 0; padding-left: 18px; font-size: 13px; color: #555">
            <li v-for="m in missingList" :key="m">{{ m }}</li>
          </ul>
        </div>
        <el-divider content-position="left">必填产物</el-divider>

        <!-- 回转寿司区（kindle: core；scaffold: obituary_card + entity_card） -->
        <template v-if="sushiCardKeysForPhase().length">
          <!-- kindle 阶段：跑团 PDF 素材上传（5 版版权规避改编） -->
          <div v-if="state.current_phase === 'kindle'" class="pdf-source-box">
            <template v-if="!pdf5Picked">
              <div class="pdf-source-title">上传跑团剧本（5 版改编）</div>
              <div class="pdf-source-desc">上传 .pdf 跑团模组/剧本，AI 同步产出 5 版差异化改编，你挑一版写入副本。</div>
              <div style="display: flex; gap: 8px; margin-top: 8px; align-items: stretch">
                <el-input
                  v-model="pdfHint"
                  size="small"
                  placeholder="改编方向（可选），如：把日式民俗改成西南侗族；主角换成女大学生……"
                  style="flex: 1"
                  :disabled="pdfUploading || !!findActiveJob('pdf5')"
                />
                <el-button
                  size="small"
                  type="primary"
                  :loading="pdfUploading || !!findActiveJob('pdf5')"
                  @click="pickPdf"
                >
                  {{ pdfUploading ? '上传中…' : (findActiveJob('pdf5') ? '改编中…' : '选择 PDF 出 5 版') }}
                </el-button>
              </div>
              <!-- 上传阶段进度条 -->
              <div v-if="pdfUploading && pdfUploadPct > 0 && pdfUploadPct < 100" class="pdf-stage-hint">
                上传中 {{ pdfUploadPct }}%
              </div>
              <!-- 注：后台进度条统一显示在下方「后台任务」区 -->
              <!-- 5 版对比面板：pdf5 job done 且尚未选定 -->
              <VariantPicker
                v-if="pdf5DoneJob && pdf5PickerItems.length && !pdf5Picked"
                title="请选择你喜欢的一版改编"
                :items="pdf5PickerItems"
                kind="pdf5"
                @pick="pickPdf5Variant"
              />
            </template>
            <template v-else-if="pdfSrc">
              <div class="pdf-source-title">
                已加载跑团改编素材
                <el-tag size="small" type="success" style="margin-left: 6px">{{ pdfSrc.filename || 'PDF' }}</el-tag>
                <el-button link size="small" style="margin-left: 8px" @click="state!.source_material = null">清除</el-button>
                <el-button link size="small" @click="pickPdf" :loading="pdfUploading">重新出 5 版</el-button>
              </div>
              <div class="pdf-source-desc">
                点击「上五盘」时 AI 会参考此改编稿生成副本核。
                <span v-if="pdfSrc.renamed_cast?.length">替换角色 {{ pdfSrc.renamed_cast.length }} 位；</span>
                <span v-if="pdfSrc.changed_elements?.length">事件改动 {{ pdfSrc.changed_elements.length }} 处；</span>
                <span v-if="pdfSrc.risk_flags?.length" style="color: #e6a23c">风险提示 {{ pdfSrc.risk_flags.length }} 条</span>
              </div>
              <details style="margin-top: 6px">
                <summary style="cursor: pointer; color: #409eff; font-size: 12px">展开改编稿预览</summary>
                <div class="pdf-source-preview" style="white-space: pre-wrap; font-size: 12px; color: #555; margin-top: 6px; max-height: 180px; overflow: auto; padding: 8px; background: #fafafa; border: 1px solid #eee; border-radius: 4px">{{ pdfSrc.premise }}</div>
                <div v-if="pdfSrc.risk_flags?.length" style="margin-top: 6px">
                  <div style="font-size: 12px; color: #e6a23c; font-weight: 600">需要继续规避的风险点：</div>
                  <ul style="margin: 4px 0 0 18px; font-size: 12px; color: #888">
                    <li v-for="(r, i) in pdfSrc.risk_flags" :key="i">{{ r }}</li>
                  </ul>
                </div>
              </details>
            </template>
          </div>
          <SushiCards
            v-for="ck in sushiCardKeysForPhase()"
            :key="ck"
            :card-key="ck"
            :card-label="SUSHI_LABELS[ck] || ck"
            :candidates="state.candidates || []"
            :editing="state.editing || null"
            :messages="state.messages"
            :axis-label="SUSHI_AXIS[ck]"
            :plate-count="SUSHI_PLATE_COUNT[ck] || 3"
            :phase="state.current_phase"
            :is-active="isActive"
            :locked-text="lockedTextOf(ck)"
            @generate="(hint?:string) => sushiGenerate(ck, hint)"
            @pick="(cid: string|null) => sushiPick(ck, cid)"
            @update-text="sushiUpdateText"
            @ai-suggest="sushiAiSuggest"
            @lock="sushiLock"
            @end-editing="sushiEndEditing"
            :style="{ opacity: sushiCardOpacity(ck) }"
          />
        </template>

        <!-- scaffold 阶段：COC 三场模拟 + 唐迟大纲 5 版（长任务驱动） -->
        <template v-if="state.current_phase === 'scaffold' && isActive">
          <el-divider content-position="left">长任务工作台</el-divider>

          <!-- COC 三场模拟 -->
          <div class="scaffold-job-box">
            <div class="scaffold-job-title">三场 COC 模拟对比</div>
            <div class="scaffold-job-desc">基于当前副本核，并发跑「同PL好运 / 同PL厄运 / 异PL反转」三场，对比后选一条进大纲。</div>
            <div style="margin-top: 8px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
              <el-button
                type="warning"
                size="small"
                :loading="!!findActiveJob('coc_batch')"
                :disabled="!!findActiveJob('coc_batch') || !!findActiveJob('outline5')"
                @click="cocDialog = true"
              >
                {{ findActiveJob('coc_batch') ? 'COC 模拟进行中…' : '跑 3 场 COC 模拟' }}
              </el-button>
              <span v-if="cocPickedIdx >= 0" class="scaffold-picked-tag">
                已选定方案 #{{ cocPickedIdx + 1 }}
              </span>
            </div>
            <!-- 进度条由下方「后台任务」区统一显示 -->
            <VariantPicker
              v-if="cocDoneJob && cocPickerItems.length"
              title="请对比三场 COC 走向，选定一条进大纲"
              :items="cocPickerItems"
              kind="coc"
              :picked-index="cocPickedIdx"
              @pick="pickCocVariant"
            />

            <!-- 选完 COC 后显示：唐迟大纲 5 版入口 -->
            <div v-if="cocPickedIdx >= 0" style="margin-top: 10px">
              <el-button
                type="primary"
                size="small"
                :loading="!!findActiveJob('outline5')"
                :disabled="!!findActiveJob('outline5')"
                @click="outlineDialog = true"
              >
                {{ findActiveJob('outline5') ? '大纲生成中…' : '跑唐迟大纲 5 版' }}
              </el-button>
              <span style="font-size: 12px; color: #888; margin-left: 8px; align-self: center">
                基于选定的 COC 走向，让「唐迟」风格产出 5 版大纲
              </span>
              <!-- 进度条统一走下方「后台任务」区 -->
              <VariantPicker
                v-if="outlineDoneJob && outlinePickerItems.length"
                title="请选择一版大纲（选定后将写入 session.outputs.tangchi_outline）"
                :items="outlinePickerItems"
                kind="outline5"
                @pick="pickOutlineVariant"
              />
            </div>
          </div>
        </template>

        <!-- 全局 jobs 进度区（回转寿司下方，所有阶段可见） -->
        <div v-if="activeJobs.length" class="jobs-dock">
          <el-divider content-position="left">后台任务</el-divider>
          <JobProgressBar
            v-for="j in activeJobs"
            :key="j.job_id"
            :job="j"
            @cancel="cancelJobById"
          />
        </div>

        <!-- 动态表单：非回转寿司的必填项按类型渲染 -->
        <div v-for="item in requiredOutputs" :key="item.key" style="margin-bottom: 10px">
          <!-- 回转寿司覆盖的 key 已在上面渲染，跳过 -->
          <template v-if="SUSHI_KEYS.has(toCardKey(item.key))"></template>
          <!-- settlement_confirmed / walkthrough_confirmed / chapter_plan_locked：单按钮 -->
          <template v-else-if="item.key.endsWith('_confirmed') || item.key === 'chapter_plan_locked'">
            <div style="font-size: 13px; margin-bottom: 4px">{{ item.desc }}</div>
            <el-button size="small" :type="item.filled ? 'success' : 'primary'" :disabled="item.filled || !isActive"
                       @click="setGate(item.key, true)">
              {{ item.filled ? '已锁定' : (item.key === 'chapter_plan_locked' ? '锁定并写入章台' : '确认') }}
            </el-button>
          </template>
          <!-- draft_word_count：数字 -->
          <template v-else-if="item.key === 'draft_word_count'">
            <div style="font-size: 13px; margin-bottom: 4px">{{ item.desc }}</div>
            <el-input-number v-model="draftWordCount" :min="0" size="small" @change="(v:any)=>setGate(item.key, v)" />
          </template>
          <!-- kindle 的 core_pitch：已被回转寿司 core 覆盖，不单独渲染（留一个提示） -->
          <template v-else-if="item.key === 'core_pitch'"></template>
          <!-- schedule 的 chapter_plan：章节列表编辑器 -->
          <template v-else-if="item.key === 'chapter_plan'">
            <div style="font-size: 13px; margin-bottom: 4px">{{ item.desc }}</div>
            <div style="border: 1px solid #eee; border-radius: 4px; padding: 8px; margin-bottom: 6px; max-height: 260px; overflow-y: auto">
              <div v-if="!chapterPlan.length" style="color: #aaa; font-size: 12px; text-align: center; padding: 10px">
                还没章。点下方「AI 拆章」生成模板，或「+ 添加一行」自己写。
              </div>
              <div v-for="(row, i) in chapterPlan" :key="i"
                   style="display: grid; grid-template-columns: 40px 50px 1fr 1fr auto; gap: 6px; margin-bottom: 6px; align-items: center">
                <el-input-number v-model="row.no" :min="1" size="small" :disabled="!isActive" controls-position="right" />
                <el-input v-model="row.day" size="small" placeholder="D1" :disabled="!isActive" />
                <el-input v-model="row.title" size="small" placeholder="章名" :disabled="!isActive" />
                <el-input v-model="row.summary" size="small" placeholder="一句话梗概" :disabled="!isActive" />
                <el-button size="small" type="danger" text :icon="Delete" @click="removePlanRow(i)" :disabled="!isActive" />
              </div>
            </div>
            <div style="display: flex; gap: 6px">
              <el-button size="small" @click="addPlanRow" :disabled="!isActive">+ 添加一行</el-button>
              <el-button size="small" type="primary" @click="saveChapterPlan" :disabled="!isActive || !chapterPlan.length">保存章节表</el-button>
            </div>
          </template>
          <!-- 其他字符串类：通用文本框（damage_note / truth_outline / tangchi_start / core_twist 等） -->
          <template v-else>
            <div style="font-size: 13px; margin-bottom: 4px">{{ item.desc }}</div>
            <el-input
              :model-value="(state.outputs[item.key] as string) ?? ''"
              type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" size="small" :disabled="!isActive"
              @change="(v: string) => setGate(item.key, v)"
            />
          </template>
        </div>
      </el-card>
    </el-col>
  </el-row>

  <!-- 新建会话对话框 -->
  <el-dialog v-model="createDialog" title="新建创作会话" width="420px">
    <el-form label-width="80px">
      <el-form-item label="类型">
        <el-radio-group v-model="newScope">
          <el-radio value="field">月规划（副本级）</el-radio>
          <el-radio value="chapter">日更（章节级）</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="场号" v-if="newScope === 'field'">
        <el-input-number v-model="newFieldNo" :min="1" />
      </el-form-item>
      <el-form-item label="章节" v-else>
        <el-input-number v-model="newChapter" :min="1" />
      </el-form-item>
      <el-form-item label="标题">
        <el-input v-model="newTitle" placeholder="可选，如「第七场·雨夜伞」" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="createDialog = false">取消</el-button>
      <el-button type="primary" @click="doCreate">开始</el-button>
    </template>
  </el-dialog>

  <!-- COC 三场模拟：参数对话框 -->
  <el-dialog v-model="cocDialog" title="跑 3 场 COC 模拟" width="480px">
    <el-form label-width="110px" size="small">
      <el-form-item label="唐迟替换谁">
        <el-select v-model="cocTangchiReplaceIdx" placeholder="不替换，先看原创 PL 走向" clearable style="width: 100%">
          <el-option :value="null" label="不替换，先看原创PL走向" />
          <el-option
            v-for="c in cocPlayerCards"
            :key="c.idx"
            :value="c.idx"
            :label="`替换第${c.idx + 1}位：${c.name}`"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="用户指令">
        <el-input
          v-model="cocUserDirective"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 5 }"
          placeholder="可选：希望特别测试的走向、想避开的雷点、额外约束……"
        />
      </el-form-item>
      <el-form-item label="每场 max_pairs">
        <el-input-number v-model="cocMaxPairs" :min="3" :max="20" />
      </el-form-item>
      <el-form-item label="每场 max_turns">
        <el-input-number v-model="cocMaxTurns" :min="5" :max="40" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="cocDialog = false">取消</el-button>
      <el-button type="warning" @click="submitCocJob">开始跑 3 场</el-button>
    </template>
  </el-dialog>

  <!-- 唐迟大纲 5 版：参数对话框 -->
  <el-dialog v-model="outlineDialog" title="跑唐迟大纲 5 版" width="520px">
    <el-form label-width="110px" size="small">
      <el-form-item label="唐迟角色定位">
        <el-input v-model="outlineTangchiRole" placeholder="如：唐迟式悬疑推进者" />
      </el-form-item>
      <el-form-item label="必现情节/伏笔">
        <el-input
          v-model="outlineMustHave"
          type="textarea"
          :autosize="{ minRows: 3, maxRows: 6 }"
          placeholder="希望大纲里一定要出现的情节、伏笔、回收点……（每行一条）"
        />
      </el-form-item>
      <el-form-item label="额外指令">
        <el-input
          v-model="outlineUserDirective"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 4 }"
          placeholder="可选：节奏偏好/情绪基调/禁忌……"
        />
      </el-form-item>
      <el-form-item label="参考 COC 方案">
        <el-tag v-if="cocPickedIdx >= 0" type="success">方案 #{{ cocPickedIdx + 1 }}</el-tag>
        <span v-else style="color: #999; font-size: 12px">未选定 COC 方案，将用当前副本核直接跑</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="outlineDialog = false">取消</el-button>
      <el-button type="primary" @click="submitOutlineJob">生成 5 版大纲</el-button>
    </template>
  </el-dialog>
  </div>
</template>

<style scoped>
.ses-item {
  padding: 10px 8px;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  margin-bottom: 6px;
}
.ses-item:hover { background: #f5f7fa; }
.ses-item.active {
  background: #ecf5ff;
  border-color: #b3d8ff;
}
.chat-area {
  height: 540px;
  overflow-y: auto;
  padding: 14px 16px;
  background: #fafafa;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.bubble {
  max-width: 85%;
  padding: 8px 12px;
  border-radius: 10px;
  line-height: 1.6;
  font-size: 14px;
  white-space: normal;
  word-break: break-word;
}
.bubble-author {
  align-self: flex-end;
  background: #409eff;
  color: white;
  border-bottom-right-radius: 2px;
}
.bubble-author .bubble-meta { color: rgba(255,255,255,0.8); }
.bubble-ai {
  align-self: flex-start;
  background: white;
  border: 1px solid #eee;
  border-bottom-left-radius: 2px;
}
.bubble-sys {
  align-self: center;
  background: #f0f0f0;
  color: #888;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 10px;
}
.bubble-sys .bubble-text { font-size: 12px; }
.bubble-meta {
  font-size: 11px;
  margin-bottom: 3px;
  display: flex;
  gap: 6px;
  color: #999;
}
.bubble-author .bubble-role { color: rgba(255,255,255,0.95); font-weight: 600; }
.bubble-ai .bubble-role { color: #409eff; font-weight: 600; }
.bubble-kind { color: #bbb; }
.bubble-time { margin-left: auto; }
.pdf-source-box {
  border: 1px dashed #b3d8ff;
  background: linear-gradient(180deg, #f0f7ff 0%, #fafcff 100%);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.pdf-source-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
}
.pdf-source-desc {
  font-size: 12px;
  color: #606266;
  margin-top: 4px;
  line-height: 1.6;
}
.pdf-stage-hint {
  margin-top: 8px;
  font-size: 12px;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #faecd8;
  border-radius: 4px;
  padding: 6px 10px;
  line-height: 1.5;
}
.scaffold-job-box {
  background: #16181d;
  border: 1px solid #2a2d35;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.scaffold-job-title {
  font-size: 13px;
  font-weight: 600;
  color: #d4a24b;
}
.scaffold-job-desc {
  font-size: 12px;
  color: #a9abaf;
  margin-top: 4px;
  line-height: 1.6;
}
.scaffold-picked-tag {
  font-size: 12px;
  color: #d4a24b;
  background: rgba(212,162,75,0.12);
  border: 1px solid rgba(212,162,75,0.4);
  border-radius: 3px;
  padding: 2px 8px;
}
.jobs-dock {
  margin-bottom: 8px;
}
:deep(.jobs-dock .el-divider__text) {
  color: #d4a24b;
  font-weight: 600;
  background: #16181d;
}
:deep(.jobs-dock .el-divider) {
  border-color: #2a2d35;
  margin: 12px 0 8px;
}
</style>
