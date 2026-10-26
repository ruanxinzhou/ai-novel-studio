<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SushiCards from '../components/SushiCards.vue'
import PrevStepPanel from '../components/PrevStepPanel.vue'
import { simulateApi } from '../api'
import type { Job } from '../api'
import { currentProjectId } from '../state'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
  (e: 'back-to-adapt'): void
}>()

type UiState = 'loading' | 'ready' | 'running' | 'result' | 'completed'

const uiState = ref<UiState>('loading')
const volume = ref<any>(null)
const job = ref<Job | null>(null)
const running = ref(false)
const pickedVariantId = ref<string | null>(null)
// 本地追加的自定义/编辑变体（不来自 job 结果）
const extraVariants = ref<any[]>([])
// 跑团实况（KP/PL 日志）折叠/展开状态，默认收起，只展示梗概
const expandedTranscript = ref<string | null>(null)
function toggleTranscript(vid: string) {
  expandedTranscript.value = expandedTranscript.value === vid ? null : vid
}
// 已完成态：跑团实况折叠
const showRaw = ref(false)
// 通稿折叠（第二步）：通稿很长，默认收起，作者要主动展开才看
const expandedNarrative = ref<string | null>(null)
// 已完成态的通稿折叠开关
const showNarrative = ref(false)
function toggleNarrative(vid: string) {
  expandedNarrative.value = expandedNarrative.value === vid ? null : vid
}
/** 从 full_log 中拆分出「小说化梗概」与「通稿」部分（去掉前置 KP/PL 日志）；
 *  无分隔符时回退到 full_log 本身。通稿段是第二步新增的，位置在梗概之后、调试信息之前。 */
function splitFullLog(full: string): { summary: string; transcript: string; narrative: string } {
  const raw = String(full || '')
  const SUMMARY_MARKER = '===== 小说化梗概（原剧本经过）====='
  const NARRATIVE_MARKER = '===== 通稿（本场完整故事）====='
  const DEBUG_MARKER = '===== 调试信息 ====='
  const i = raw.indexOf(SUMMARY_MARKER)
  if (i < 0) return { summary: raw.trim(), transcript: '', narrative: '' }
  const transcript = raw.slice(0, i).trim()
  const afterSummary = raw.slice(i + SUMMARY_MARKER.length)
  // 通稿段（可能不存在——老数据没有这一段）
  const nIdx = afterSummary.indexOf(NARRATIVE_MARKER)
  let summary = afterSummary
  let narrative = ''
  if (nIdx >= 0) {
    summary = afterSummary.slice(0, nIdx)
    const afterNarr = afterSummary.slice(nIdx + NARRATIVE_MARKER.length)
    const dIdx = afterNarr.indexOf(DEBUG_MARKER)
    narrative = (dIdx >= 0 ? afterNarr.slice(0, dIdx) : afterNarr).trim()
  }
  // 摘要段要剥掉尾部的通稿/调试标记残留
  const dIdx2 = summary.indexOf(DEBUG_MARKER)
  if (dIdx2 >= 0) summary = summary.slice(0, dIdx2)
  return { summary: summary.trim(), transcript, narrative }
}
const completedSummary = computed(() => {
  const sv = completed.value?.selected_variant
  if (sv?.final_summary) return String(sv.final_summary)
  return splitFullLog(completed.value?.full_log || '').summary
})

// ---- 失败可见性 ----
/** 该场是否失败。**独立字段判定**，不靠 final_summary 有没有内容——
 *  旧实现把 traceback 写进 final_summary，那正是失败看起来像成功的根源。 */
function isFailed(v: any): boolean {
  return v?.run_status === 'failed'
}
const FAIL_STAGE_LABEL: Record<string, string> = {
  thread: '线程崩溃',
  bootstrap: '未能启动（0 回合）',
  finalize: '梗概生成失败',
}
function failStageLabel(v: any): string {
  return FAIL_STAGE_LABEL[v?.fail_stage] || '未知阶段'
}
/** 失败原因：优先 fail_reason，退回 state.error，都没有就说「未知原因」。 */
function failReason(v: any): string {
  return String(v?.fail_reason || v?.state_error || '未知原因')
}
/** 有几场失败——顶部汇总条要用。 */
const failedCount = computed(() => allVariants.value.filter(isFailed).length)
const completedTranscript = computed(() => {
  const sv = completed.value?.selected_variant
  if (sv?.transcript) return String(sv.transcript)
  return splitFullLog(completed.value?.full_log || '').transcript
})

// ---- beat 进度与终止原因（第二步）----
//
// 作者选底本前必须知道两件事：这一场**讲完了没有**，以及**讲到哪一段了**。
// 旧的「回合 24」看不出故事有没有收住——回合数跟故事长度无关。
/** 终止原因 → 作者可读文案 + 严重度。hard_cap 要显式警告：后半段没写完。 */
const ENDING_REASON_META: Record<string, { text: string; level: 'ok' | 'warn' | 'info' }> = {
  story_complete: { text: '自然讲完', level: 'ok' },
  transition_done: { text: '转折后收束', level: 'ok' },
  hard_cap: { text: '撞预算截断', level: 'warn' },
  max_turns: { text: '触顶回合上限', level: 'warn' },
  dead: { text: '全员死亡', level: 'warn' },
  insane: { text: '全员疯狂', level: 'warn' },
  no_beat: { text: '未跑出任何一拍', level: 'info' },
  stopped_by_author: { text: '手动停止', level: 'info' },
}
function endingReasonMeta(v: any) {
  const key = String(v?.ending_reason || '')
  return ENDING_REASON_META[key] || { text: '', level: 'info' as const }
}
/** beat 进度百分比：预算为 0 时不显示进度条，避免出现除零/空条。 */
function beatPercent(v: any): number {
  const done = Number(v?.beats_done || 0)
  const budget = Number(v?.beat_budget || 0)
  if (budget <= 0) return 0
  return Math.min(100, Math.round((done / budget) * 100))
}
/** 通稿：优先独立字段；老数据没有该字段时回退到 full_log 里拆。 */
function narrativeOf(v: any): string {
  if (v?.full_narrative) return String(v.full_narrative)
  return ''
}
/** 有几场是「讲完了」，汇总条要提示作者哪几场其实没写完。 */
const truncatedCount = computed(
  () => allVariants.value.filter((v: any) => endingReasonMeta(v).level === 'warn').length,
)
/** 已完成态的通稿：优先独立字段，老数据回退到 full_log 拆分。 */
const completedNarrative = computed(() => {
  const sv = completed.value?.selected_variant
  if (sv?.full_narrative) return String(sv.full_narrative)
  return splitFullLog(completed.value?.full_log || '').narrative
})

// ---- 启动参数 ----
const userDirective = ref('')
// 默认值只作为「上限」：真实拍数由后端按模组体量自动估算。
// 以前默认 6 会把预算钉死在6~8 拍，三场全部 hard_cap 收场。
const maxPairs = ref(40)
const maxTurns = ref(120)
const useMock = ref(false)
const optionsOpen = ref(false)

// ---- 拼贴参数 ----
const collageBase = ref(0)
// 段落预设建议（不是硬锁——作者可以改 label、增删任意段）
const SECTION_PRESETS: { key: string; label: string }[] = [
  { key: 'opening', label: '开场 / 入局' },
  { key: 'hook', label: '钩子 / 异兆初现' },
  { key: 'clue_gather', label: '线索汇集 / 第一次探查' },
  { key: 'first_death', label: '第一起死亡 / 第一个牺牲' },
  { key: 'mid_reveal', label: '中段反转 / 认知崩塌' },
  { key: 'climax', label: '高潮 / 真相揭露' },
  { key: 'escape', label: '逃出生天 / 代价结算' },
  { key: 'ending', label: '结局' },
  { key: 'npc_fates', label: 'NPC 命运总览' },
  { key: 'aftermath', label: '余波 / 尾声' },
]
// 拼贴段落：[{ id, key, label, from, note }]——可自由增删/改名
type CollageRow = { id: string; key: string; label: string; from: number; note: string }
const _cid = () => `s_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`
const collageRows = ref<CollageRow[]>([])
const addSecMenuVisible = ref(false)
function initCollageRows() {
  if (collageRows.value.length > 0) return
  // 默认放 4 段最通用的：开场 / 中段反转 / 高潮 / 结局；作者可自由改
  const defaultKeys = ['opening', 'climax', 'ending', 'npc_fates']
  collageRows.value = defaultKeys.map((k) => {
    const preset = SECTION_PRESETS.find((p) => p.key === k)
    return { id: _cid(), key: k, label: preset?.label || k, from: collageBase.value, note: '' }
  })
}
function addPresetSection(preset: { key: string; label: string }) {
  collageRows.value.push({ id: _cid(), key: preset.key, label: preset.label, from: collageBase.value, note: '' })
  addSecMenuVisible.value = false
}
function addCustomSection() {
  addSecMenuVisible.value = false
  const label = window.prompt('新段落名称（例如「警察介入」「旧信被截」）：', '')
  if (!label) return
  collageRows.value.push({ id: _cid(), key: 'custom_' + Date.now(), label: label.trim(), from: collageBase.value, note: '' })
}
function removeSection(id: string) {
  collageRows.value = collageRows.value.filter((r) => r.id !== id)
}
function renameSection(id: string) {
  const row = collageRows.value.find((r) => r.id === id)
  if (!row) return
  const label = window.prompt('新段落名：', row.label)
  if (label && label.trim()) row.label = label.trim()
}

// 选项标签更丰富：场号 + 风格角标 + one_liner 首句
const FROM_OPTIONS = computed(() =>
  allVariants.value.map((v, i) => {
    const num = i + 1
    const flavor = v.flavor || ''
    const one = (v.one_liner || '').replace(/\s+/g, ' ').slice(0, 60)
    const label = `第${num}场${flavor ? ' · ' + flavor : ''}｜${one}`
    return { value: i, label, shortLabel: `第${num}场${flavor ? '·' + flavor : ''}`, one_liner: v.one_liner || '', flavor }
  }),
)
function optionOf(i: number) {
  return FROM_OPTIONS.value.find((o) => o.value === i) || FROM_OPTIONS.value[0]
}

// 兼容已完成态回读——把之前保存的 selections 数组转回 rows
function hydrateCollageFromSaved(saved: any) {
  if (!saved || collageRows.value.length > 0) return
  const sel = saved.selections
  if (Array.isArray(sel) && sel.length) {
    collageRows.value = sel.map((s: any) => ({
      id: _cid(),
      key: s.section || 'custom',
      label: s.label || SECTION_PRESETS.find((p) => p.key === s.section)?.label || s.section || '段落',
      from: Number(s.from_index) || 0,
      note: s.note || '',
    }))
    collageBase.value = Number(saved.base_index) || 0
  } else {
    initCollageRows()
  }
}
const collageUserNote = ref('')

// ---- 自定义/编辑 dialog ----
const dialogVisible = ref(false)
const dialogMode = ref<'custom' | 'edit'>('custom')
const dialogLabel = ref('')
const dialogText = ref('')
const dialogEditIndex = ref<number>(-1)  // index within allVariants; -1 for custom

let sse: { close: () => void } | null = null

/** job 返回的 AI 版本 */
const jobVariants = computed<any[]>(() => {
  if (!job.value) return []
  if (job.value.result && typeof job.value.result === 'object' && Array.isArray((job.value.result as any).variants)) {
    return (job.value.result as any).variants
  }
  if (Array.isArray(job.value.sub_results)) return job.value.sub_results as any[]
  return []
})

/** 结局倾向枚举 → 中文短标签 */
const BIAS_LABELS: Record<string, string> = {
  bad: '黑暗结局',
  bittersweet: '惨胜',
  costly: '代价型',
  tradeoff: '交换结局',
  narrow_escape: '险胜',
  good: '好运',
  distinct: '洗牌',
  mock: '示例',
}

/** 包含自定义卡的全部可选项（自定义卡追加在尾部） */
const allVariants = computed<any[]>(() => {
  const arr = [...jobVariants.value, ...extraVariants.value]
  // 把 ending_bias / flavor 的英文枚举映射成中文短标签，方便角标直接显示
  for (const v of arr) {
    const key = v.ending_bias || v.flavor
    if (key && BIAS_LABELS[key] && v.flavor !== BIAS_LABELS[key]) {
      v.flavor = BIAS_LABELS[key]
    }
    // 确保 one_liner 有值
    if (!v.one_liner && v.final_summary) {
      const firstLine = String(v.final_summary).trim().split('\n', 1)[0]
      v.one_liner = firstLine.slice(0, 80)
    }
  }
  return arr
})

const completed = computed(() => volume.value?.simulated_run ?? null)

// adapted_module 新版本是 dict（含 adapted_premise / cast / flavor 等），老版本是纯字符串。
// 这里兼容两种格式，把副本核文本+NPC 卡司拼成可读预览。
const adaptedPreview = computed(() => {
  const am = volume.value?.adapted_module
  if (!am) return ''
  if (typeof am === 'string') return am
  if (typeof am === 'object') {
    const premise = am.adapted_premise || ''
    const cast: any[] = Array.isArray(am.cast) ? am.cast
      : (Array.isArray(am.player_cards) ? am.player_cards : [])
    const flavor = am.flavor_full || am.flavor || ''
    const lines: string[] = []
    if (flavor) lines.push(`【风格】${flavor}`)
    if (premise) lines.push(premise)
    if (cast.length) {
      lines.push('\n【剧本 NPC 卡司】')
      for (const c of cast) {
        if (!c || typeof c !== 'object') continue
        const n = c.name || '?'
        const r = c.role ? `（${c.role}` : ''
        const o = c.occupation ? `${r ? '；' : '（'}${c.occupation}）` : (r ? '）' : '')
        const sec = c.secret ? `｜秘密：${c.secret}` : ''
        lines.push(`- ${n}${r}${o}${sec}`)
      }
    }
    return lines.join('\n')
  }
  return String(am)
})

async function loadVolume() {
  const pid = currentProjectId.value
  if (!pid || !props.volNo) return
  try {
    const r = await simulateApi.getVolume(pid, props.volNo)
    volume.value = r.volume
    if (r.volume?.simulated_run) {
      uiState.value = 'completed'
      // 若是拼贴模式则回读 selections，让「重跑」之前的选择不丢失
      if (r.volume.simulated_run.mode === 'collage') {
        hydrateCollageFromSaved(r.volume.simulated_run.collage)
      }
    } else {
      // 查已有 job
      try {
        const jr = await simulateApi.listJobs(pid, props.volNo)
        const jobs = jr.jobs || []
        // 找最新未完成 / 最新 done
        const runningJob = jobs.reverse().find((j: Job) => j.status === 'running' || j.status === 'pending')
        const doneJob = jobs.find((j: Job) => j.status === 'done')
        if (runningJob) {
          job.value = runningJob
          uiState.value = 'running'
          startSse()
        } else if (doneJob) {
          job.value = doneJob
          uiState.value = 'result'
        } else {
          uiState.value = 'ready'
        }
      } catch {
        uiState.value = 'ready'
      }
    }
  } catch (e: any) {
    ElMessage.error('加载卷失败：' + (e?.message ?? e))
    uiState.value = 'ready'
  }
}

function startSse() {
  const pid = currentProjectId.value
  if (!pid || !props.volNo) return
  sse?.close()
  sse = simulateApi.subscribeJobs(pid, props.volNo, (ev) => {
    if (ev.type === 'job_update' && ev.job && job.value && ev.job.job_id === job.value.job_id) {
      applyJob(ev.job)
    } else if (ev.type === 'job_update_batch' && ev.jobs) {
      // **必须锁定当前 job**：同一 session 里堆着历次跑团的 job，
      // 取「最后一个」会拿到旧任务，把界面回退到上一轮结果。
      const mine = job.value
        ? ev.jobs.find((j: any) => j.job_id === job.value?.job_id)
        : [...ev.jobs].reverse().find((j: any) => j.kind === 'coc_batch')
      if (mine) applyJob(mine)
    } else if (ev.type === 'job_idle') {
      // SSE 即将关闭时**主动拉一次最终状态**。真实事故：任务在服务端
      // 已经 done，但长连接在最后一刻断掉，done 事件没送达，
      // 页面永远停在 96% 的running 态，结果区一片空白。
      void refreshJob()
    }
  })
}

/** 兜底拉取：SSE 不可靠时的唯一真相来源。 */
async function refreshJob() {
  const pid = currentProjectId.value
  if (!pid || !props.volNo) return
  try {
    const r = await simulateApi.listJobs(pid, props.volNo)
    const jobs = r.jobs || []
    const mine = job.value
      ? jobs.find((j: any) => j.job_id === job.value?.job_id)
      : [...jobs].reverse().find((j: any) => j.kind === 'coc_batch')
    if (mine) applyJob(mine)
  } catch {
    // 拉取失败就保持原状态，下次SSE 事件会再纠正
  }
}

// ---- 实时日志（第四步）----
// 作者等的是「模型此刻在干什么」。后端把 LLM 的 thinking / delta 流
// 实时写进 job.live_log，这里按场次分栏渲染。
//
// **为什么分栏**：三场是并行的独立支线，混在一个滚动框里会互相穿插
// ——A 场的思考和 B 场的裁决交替出现，读起来像一场在胡言乱语。
// 三栏并列后，每场的推演脉络各自连续，支线如何分叉一眼可见。
const liveLog = ref<Array<{ kind: string; text: string; session?: number }>>([])
/** 每场的最新一行判定结果（更有用：一眼看到这条支线讲成什么样了） */
const liveBeats = ref<Record<number, string>>({})
/** 每栏独立的滚动容器 ref（三场各一个） */
const livePaneRefs = ref<Record<number, HTMLElement | null>>({})

/** 按场次把事件分桶；每桶只保留最近 N 行，避免长跑把 DOM 撑爆 */
const LIVE_PER_PANE = 40

const liveBySession = computed(() => {
  const buckets: Record<number, Array<{ kind: string; text: string }>> = {}
  for (const row of liveLog.value) {
    const s = Number(row.session || 0)
    if (!buckets[s]) buckets[s] = []
    buckets[s].push({ kind: row.kind, text: row.text })
  }
  for (const k of Object.keys(buckets)) {
    buckets[k as unknown as number] = buckets[k as unknown as number].slice(-LIVE_PER_PANE)
  }
  return buckets
})

/** 三栏的渲染模型（空栏也渲染，保持布局稳定不跳动） */
const livePanes = computed(() =>
  [1, 2, 3].map((s) => ({
    session: s,
    // 场次名与下方候选卡保持同一来源（sub_results[i].label），
    // 作者在两处看到的是同一个名字，不用建立心智映射。
    label: (job.value?.sub_results as any[])?.[s - 1]?.label || `平行宇宙 #${s}`,
    rows: liveBySession.value[s] || [],
  }))
)

/** 该栏最新一条非 thinking 文本，作为栏头状态（作者最关心「讲到哪了」） */
function paneStatus(s: number): string {
  const beats = liveBeats.value[s]
  if (beats) return beats
  const rows = liveBySession.value[s] || []
  for (let i = rows.length - 1; i >= 0; i--) {
    if (rows[i].kind === 'verdict' || rows[i].kind === 'delta') return rows[i].text
  }
  return ''
}

function setPaneRef(s: number, el: any) {
  if (el) livePaneRefs.value[s] = el as HTMLElement
}

function applyLiveLog(j: Job) {
  const rows: any[] = (j as any).live_log || []
  if (!rows.length) return
  // 合并 delta：同一场连续的 delta 拼成一行，避免刷屏
  const merged: Array<{ kind: string; text: string; session?: number }> = []
  for (const r of rows) {
    const last = merged[merged.length - 1]
    if (
      last && last.kind === 'delta' && last.session === r.session &&
      last.text.length + (r.text?.length || 0) < 900
    ) {
      last.text += r.text
    } else {
      merged.push({ kind: r.kind, text: r.text, session: r.session })
    }
  }
  liveLog.value = merged.slice(-120)
  // 判定结果单独收集
  const beats: Record<number, string> = {}
  for (const r of rows) {
    if (r.kind === 'verdict' && r.session != null) beats[r.session] = r.text
  }
  if (Object.keys(beats).length) liveBeats.value = beats
  // 各栏分别滚到底
  nextTick(() => {
    for (const el of Object.values(livePaneRefs.value)) {
      if (el) el.scrollTop = el.scrollHeight
    }
  })
}

function applyJob(j: Job) {
  job.value = j
  applyLiveLog(j)
  if (j.status === 'done') {
    running.value = false
    extraVariants.value = []
    uiState.value = 'result'
    pickedVariantId.value = null
    // 初始化拼贴段落（只在第一次 done 时初始化，避免覆盖用户已编辑的 rows）
    if (collageRows.value.length === 0) initCollageRows()
  } else if (j.status === 'error') {
    running.value = false
    uiState.value = 'ready'
    ElMessage.error('跑团失败：' + (j.error || '未知错误'))
  } else {
    uiState.value = 'running'
  }
}

async function onStart(directive = '') {
  const pid = currentProjectId.value
  if (!pid) return
  running.value = true
  extraVariants.value = []
  try {
    const r = await simulateApi.startSim(pid, props.volNo, {
      user_directive: directive || userDirective.value,
      max_pairs_per_session: maxPairs.value,
      max_turns_per_session: maxTurns.value,
      use_mock: useMock.value,
    })
    job.value = r.job
    pickedVariantId.value = null
    uiState.value = 'running'
    startSse()
  } catch (e: any) {
    running.value = false
    ElMessage.error('启动失败：' + (e?.message ?? e))
  }
}

/** 正在重跑的卡片索引（-1=无）。当前后端只支持整批重跑，
 *  所以这里记录的是「用户点了哪张失败卡」，实际动作是整批重来。 */
const rerunningIdx = ref(-1)

/**
 * 重跑失败场。
 *
 * **诚实说明**：后端目前只有 `startSim`（整批启动），没有单场重跑接口，
 * 所以这里触发的是**整批重跑**，而不是只补这一场。作者会重新消耗
 * 已成功那几场的额度——所以必须先确认，不能让作者以为只花一场的钱。
 *
 * 单场重跑要做的话需要后端补一个 `retry_session(vid)`，本step 不做。
 */
async function onRerunFailed(idx: number) {
  const it = allVariants.value[idx]
  if (!it) return
  rerunningIdx.value = idx
  const okCount = allVariants.value.length - failedCount.value
  try {
    await ElMessageBox.confirm(
      `第${idx + 1}场失败（${failStageLabel(it)}）。\n\n` +
        `目前只能整批重跑——已成功的 ${okCount} 场也会一并重来，` +
        `全部跑团额度重新消耗一次。\n\n确定继续？`,
      '重跑跑团',
      { confirmButtonText: '整批重跑', cancelButtonText: '算了', type: 'warning' },
    )
  } catch {
    rerunningIdx.value = -1
    return
  }
  rerunningIdx.value = -1
  onStart('')
}

async function onPickOne(id: string | null, idx?: number) {
  if (id == null || idx == null) return
  const pid = currentProjectId.value
  if (!pid) return
  const picked = allVariants.value[idx]
  if (!picked) return
  // 失败场不能选作创作底本——它没有梗概。作者要的是「读懂后挑一个」，
  // 挑一个空梗概毫无意义。与其在弹窗里拦，不如这里直接拒绝并给出动作入口。
  if (isFailed(picked)) {
    ElMessage.warning(
      `第${idx + 1}场跑团失败（${failStageLabel(picked)}），没有梗概可选。请先重跑这一场。`,
    )
    return
  }
  pickedVariantId.value = id
  // 自定义/编辑卡 → 走 save-custom
  if (picked.is_custom) {
    try {
      const r = await simulateApi.saveCustomVariant(pid, props.volNo, picked)
      volume.value = r.volume
      uiState.value = 'completed'
      ElMessage.success('已保存自定义跑团走向')
      emit('done')
    } catch (e: any) {
      ElMessage.error('保存失败：' + (e?.message ?? e))
    }
    return
  }
  if (!job.value) return
  try {
    const r = await simulateApi.pickOne(pid, props.volNo, job.value.job_id, idx)
    volume.value = r.volume
    uiState.value = 'completed'
    ElMessage.success('已选定本场为主跑团记录，进入下一阶段')
    emit('done')
  } catch (e: any) {
    ElMessage.error('选定失败：' + (e?.message ?? e))
  }
}

async function onCollage() {
  const pid = currentProjectId.value
  if (!pid || !job.value) return
  if (collageRows.value.length === 0) initCollageRows()
  const selections = collageRows.value.map((r) => ({
    section: r.key,
    label: r.label,
    from_index: r.from ?? collageBase.value,
    note: r.note ?? '',
  }))
  try {
    const r = await simulateApi.collage(pid, props.volNo, {
      job_id: job.value.job_id,
      collage: {
        base_index: collageBase.value,
        selections,
        user_note: collageUserNote.value,
      },
    })
    volume.value = r.volume
    uiState.value = 'completed'
    ElMessage.success('拼贴合成完成，进入下一阶段')
    emit('done')
  } catch (e: any) {
    ElMessage.error('拼贴失败：' + (e?.message ?? e))
  }
}

async function onReset() {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const r = await simulateApi.resetRun(pid, props.volNo)
    volume.value = r.volume
    job.value = null
    pickedVariantId.value = null
    extraVariants.value = []
    collageRows.value = []
    uiState.value = 'ready'
    ElMessage.success('已清空跑团结果，可以重跑')
  } catch (e: any) {
    ElMessage.error('清空失败：' + (e?.message ?? e))
  }
}

// ---- SushiCards 迭代事件 ----
function onRerun(idx: number | null, directive: string) {
  const extra = directive
    ? (idx != null ? `参考第${idx + 1}场风格，调整方向：${directive}` : `重抽提示：${directive}`)
    : ''
  onStart(extra)
}

function onCustomWrite() {
  dialogMode.value = 'custom'
  dialogLabel.value = ''
  dialogText.value = ''
  dialogEditIndex.value = -1
  dialogVisible.value = true
}

function onEditCard(idx: number) {
  const it = allVariants.value[idx]
  if (!it) return
  dialogMode.value = 'edit'
  dialogLabel.value = it.label || ''
  dialogText.value = it.full_log || it.content || ''
  dialogEditIndex.value = idx
  dialogVisible.value = true
}

function onDialogSave() {
  const text = dialogText.value.trim()
  if (!text) { ElMessage.warning('跑团日志内容不能为空'); return }
  const label = dialogLabel.value.trim() || (dialogMode.value === 'edit' ? '已编辑' : '自定义')
  const newCard: any = {
    is_custom: true,
    label,
    full_log: text,
    content: text,
    one_liner: text.slice(0, 80).replace(/\n/g, ' '),
    flavor: dialogMode.value === 'edit' ? '已编辑' : '自定义',
  }
  if (dialogMode.value === 'edit' && dialogEditIndex.value >= 0) {
    const jobN = jobVariants.value.length
    if (dialogEditIndex.value < jobN) {
      // 编辑 AI 卡 → 把修改后的版本追加到 extraVariants（原卡保留，便于对比）
      const base = jobVariants.value[dialogEditIndex.value] || {}
      newCard.label = `✏️ ${label}`
      newCard._editBaseIdx = dialogEditIndex.value
      extraVariants.value = [...extraVariants.value, { ...base, ...newCard }]
    } else {
      // 编辑自定义卡 → 就地替换
      const localIdx = dialogEditIndex.value - jobN
      const base = extraVariants.value[localIdx] || {}
      extraVariants.value[localIdx] = { ...base, ...newCard }
      extraVariants.value = [...extraVariants.value]
    }
  } else {
    newCard.label = `✏️ ${label}`
    extraVariants.value = [...extraVariants.value, newCard]
  }
  dialogVisible.value = false
  ElMessage.success(dialogMode.value === 'edit' ? '已生成编辑版，可选定保存' : '已添加自定义版本，可选定保存')
}

onMounted(() => {
  loadVolume()
})

onUnmounted(() => {
  sse?.close()
})

watch(() => props.volNo, () => {
  if (props.volNo) loadVolume()
})
</script>

<template>
  <div class="coc-sim-view">
    <div class="sim-header">
      <div class="header-row">
        <h2>🎲 COC 并行跑团仿真</h2>
        <el-button size="small" @click="emit('back-to-adapt')">← 返回上一步（重选剧本）</el-button>
      </div>
      <p class="sub">
        同一模组 · 同一组外乡调查员（PL）· 3 个平行宇宙并行推演，只在骰子与 KP 自由长上产生分歧；
        挑一场作为主跑团记录，或跨场拼贴。本阶段尚未决定唐迟魂穿到谁身上（下一阶段再选）。
      </p>
      <div v-if="completed" class="completed-banner">
        <span>✓ 已选定跑团走向：{{ completed.summary || completed.label || '（已完成）' }}</span>
        <el-button size="small" @click="onReset">重跑</el-button>
      </div>
    </div>

    <!-- 上一步产物：剧本改编稿 -->
    <PrevStepPanel
      step-label="COC 并行跑团仿真"
      prev-step-label="剧本改编（改编后副本核）"
      :sections="[
        { title: '改编后副本核 / 模组前提', body: adaptedPreview, bodyType: 'text' }
      ]"
    />

    <!-- 准备态 -->
    <div v-if="uiState === 'ready'" class="sim-ready">
      <el-card shadow="never">
        <h3>启动 3 场并行 COC 跑团</h3>
        <el-input
          v-model="userDirective"
          type="textarea"
          :rows="5"
          placeholder="可选：对本场跑团的特别指令，例如「多死人」「让记者死在中段」「偏向黑暗结局」..."
          class="directive-input"
        />
        <el-collapse v-model="optionsOpen" class="options-collapse">
          <el-collapse-item title="高级选项" name="opts">
            <div class="opt-row">
              <label>每场最大 KP/PL 对数：{{ maxPairs }}（每对 = 1 段 KP 推进 + 1 段 PL 行动）</label>
              <el-slider v-model="maxPairs" :min="8" :max="120" :step="4" />
              <p class="opt-hint">仅作为上限：实际拍数由模组体量自动估算，通常高于此值时按估算走。</p>
            </div>
            <div class="opt-row">
              <label>每场硬上限回合数：{{ maxTurns }}</label>
              <el-slider v-model="maxTurns" :min="20" :max="200" :step="10" />
            </div>
            <div class="opt-row">
              <el-checkbox v-model="useMock">快速演示（跳过 LLM，1 秒出 mock 占位，仅用于 UI 调试）</el-checkbox>
            </div>
          </el-collapse-item>
        </el-collapse>
        <div class="actions">
          <el-button type="primary" size="large" :loading="running" @click="() => onStart()">
            🎲 开跑 3 场并行 COC
          </el-button>
        </div>
      </el-card>
    </div>

    <!-- 跑团中 -->
    <div v-else-if="uiState === 'running'" class="sim-running">
      <el-card shadow="never">
        <h3>跑团进行中…</h3>
        <el-progress :percentage="Math.round(job?.progress ?? 0)" :status="(job?.progress ?? 0) >= 100 ? 'success' : undefined" />
        <p class="msg">{{ job?.message || '等待开场…' }}</p>

        <!-- 实时推演：三场并行，各占一栏。分栏而不是混在一个框里，
             是因为三场是独立支线——混排时 A 场的思考和 B 场的裁决会交替
             出现，读起来像一场在胡言乱语。三栏并列后每场脉络各自连续。 -->
        <div class="live-panel">
          <div class="live-head">
            <span class="live-title">实时推演</span>
            <span class="live-hint">三场并行，各栏独立显示模型思考与逐拍裁决</span>
          </div>
          <div class="live-cols">
            <section
              v-for="pane in livePanes"
              :key="pane.session"
              class="live-col"
              :class="{ 'is-active': pane.rows.length > 0 }"
            >
              <header class="live-col-head">
                <span class="live-col-title">{{ pane.label }}</span>
                <span class="live-col-status">{{ paneStatus(pane.session) || '等待开场…' }}</span>
              </header>
              <div class="live-scroll" :ref="(el: any) => setPaneRef(pane.session, el)">
                <div v-if="!pane.rows.length" class="live-empty">等待模型开始输出…</div>
                <div
                  v-for="(row, i) in pane.rows"
                  :key="i"
                  class="live-row"
                  :class="`live-${row.kind}`"
                >
                  <span class="live-text">{{ row.text }}</span>
                </div>
              </div>
            </section>
          </div>
        </div>
        <div class="variant-cards-loading">
          <div class="var-loading-row">
            <div v-for="i in 3" :key="i" class="var-loading-card">
              <div class="var-label">
                {{ (job?.sub_results as any[])?.[i-1]?.label || `平行宇宙 #${i}` }}
                <el-tag v-if="(job?.sub_results as any[])?.[i-1]?.flavor" size="small" style="margin-left: 6px">
                  {{ (job?.sub_results as any[])[i-1].flavor }}
                </el-tag>
              </div>
              <div class="var-one-liner">
                <template v-if="(job?.sub_results || []).length >= i && (job?.sub_results as any[])[i-1]?.one_liner">
                  {{ (job?.sub_results as any[])[i-1].one_liner }}
                </template>
                <template v-else-if="(job?.sub_results || []).length >= i">
                  {{ (job?.sub_results as any[])[i-1]?.one_liner || '该场已完成，汇总中…' }}
                </template>
                <template v-else>
                  <span class="muted">等待开场…</span>
                </template>
              </div>
            </div>
          </div>
        </div>
        <div class="actions">
          <el-button size="small" @click="emit('back-to-adapt')">← 返回重选剧本（会保留本场任务）</el-button>
        </div>
      </el-card>
    </div>

    <!-- 结果对比 + 拼贴（用 SushiCards ensemble）-->
    <div v-else-if="uiState === 'result'" class="sim-result">
      <!-- 失败汇总条：作者不该靠逐张卡片去发现哪几场挂了 -->
      <el-alert
        v-if="failedCount > 0"
        type="error"
        show-icon
        :closable="false"
        class="fail-summary"
      >
        <template #title>
          {{ failedCount }} / {{ allVariants.length }} 场跑团失败
        </template>
        <div class="fail-summary-body">
          失败场没有产出梗概，不能选作创作底本。点失败卡上的按钮可整批重跑（已成功的场也会一并重来，额度重新消耗一次）。
        </div>
      </el-alert>

      <!-- 截断汇总：没失败的场也可能只是撞预算截断，同样不能当完整底本 -->
      <el-alert
        v-if="truncatedCount > 0"
        type="warning"
        show-icon
        :closable="false"
        class="fail-summary"
      >
        <template #title>
          {{ truncatedCount }} 场没有自然讲完
        </template>
        <div class="fail-summary-body">
          这些场撞到了拍数预算或硬约束（全员死亡 / 疯狂），后半段可能没写完。卡片上有橙色标记，选作底本前请先确认。
        </div>
      </el-alert>

      <SushiCards
        mode="ensemble"
        :items="allVariants"
        :picked-id="pickedVariantId"
        axis-label="3 个平行宇宙 · 同一模组同一组 PL，骰子与 KP 自由发挥导致分歧"
        item-title-field="label"
        item-subtitle-field="one_liner"
        item-content-field="final_summary"
        item-badge-field="flavor"
        :plate-count="3"
        :is-active="true"
        @pick="onPickOne"
        @rerun="onRerun"
        @custom-write="onCustomWrite"
        @edit-card="onEditCard"
      >
        <template #item="{ item: it, index: idx }">
          <!-- 失败态：红框 + 阶段 + 原因 + 单独重跑入口。
               失败场不提供「选这盘」——没有梗概的场不能当创作底本。 -->
          <div v-if="isFailed(it)" class="variant-failed">
            <div class="variant-failed-head">
              <el-tag type="danger" size="small" effect="dark">本场失败</el-tag>
              <span class="variant-failed-stage">{{ failStageLabel(it) }}</span>
            </div>
            <div class="variant-failed-reason">{{ failReason(it) }}</div>
            <div class="variant-failed-hint">
              这一场没有产出梗概，无法选作创作底本。
            </div>
            <el-button
              size="small"
              type="danger"
              plain
              :loading="rerunningIdx === idx"
              @click="onRerunFailed(idx)"
            >重跑（含已成功场）</el-button>
          </div>

          <div class="variant-meta-row">
            <span class="meta-chip">幸存者 {{ (it?.survivors || []).length }} 人</span>
            <span class="meta-chip" v-if="it?.turns">回合 {{ it.turns }}</span>
            <span class="meta-chip" v-if="it?.beat_budget">
              第 {{ it?.beats_done || 0 }} 拍 / 预算 {{ it.beat_budget }}
            </span>
            <span class="meta-chip phase-chip" v-if="it?.story_phase">{{ it.story_phase }}</span>
            <span
              class="meta-chip"
              :class="`ending-${endingReasonMeta(it).level}`"
              v-if="endingReasonMeta(it).text"
            >{{ endingReasonMeta(it).text }}</span>
            <el-button
              link
              type="info"
              size="small"
              @click="toggleTranscript(it?.vid)"
            >{{ expandedTranscript === it?.vid ? '收起跑团实况' : '查看跑团实况（素材）' }}</el-button>
          </div>
          <!-- beat 进度条：只看「回合 N」判断不出故事讲够了没，这里给出语义进度 -->
          <el-progress
            v-if="it?.beat_budget && Number(it?.beats_done || 0) > 0"
            :percentage="beatPercent(it)"
            :status="endingReasonMeta(it).level === 'ok' ? 'success' : undefined"
            :stroke-width="4"
            class="beat-progress"
          />
          <!-- 截断警告：撞预算的场后半段没写完，直接当底本会漏情节 -->
          <div
            class="ending-warn-note"
            v-if="endingReasonMeta(it).level === 'warn' && it?.ending_reason === 'hard_cap'"
          >
            本场撞到 beat 预算上限，后半段没有写完。选它当底本前请确认缺失部分可以自行补足。
          </div>
          <!-- 通稿：8000-12000 字完整故事，默认收起 -->
          <div class="variant-meta-row" v-if="narrativeOf(it)">
            <span class="meta-chip narrative-chip">通稿 {{ narrativeOf(it).length }} 字</span>
            <el-button
              link
              type="success"
              size="small"
              @click="toggleNarrative(it?.vid)"
            >{{ expandedNarrative === it?.vid ? '收起通稿' : '查看通稿（完整故事）' }}</el-button>
          </div>
          <el-collapse-transition>
            <div v-if="expandedNarrative === it?.vid && narrativeOf(it)" class="variant-narrative">
              <div class="mat-hint">本场完整故事通稿，可直接作为创作底本；梗概卡片是它的压缩版。</div>
              <pre class="narrative-pre">{{ narrativeOf(it) }}</pre>
            </div>
          </el-collapse-transition>
          <el-collapse-transition>
            <div v-if="expandedTranscript === it?.vid" class="variant-transcript">
              <div class="mat-hint">以下为完整 KP/PL 跑团日志，作为小说化梗概的原始素材；选版时以梗概为准。</div>
              <pre class="transcript-pre">{{ it?.transcript || '（无回合日志）' }}</pre>
              <div v-if="it?.state_error" class="state-error">state.error: {{ it.state_error }}</div>
            </div>
          </el-collapse-transition>
        </template>
      </SushiCards>

      <el-card shadow="never" class="collage-card">
        <h3>🧩 或：拼贴合成主跑团记录</h3>
        <p class="hint">
          拼贴模式：从几场 AI 版本里挑你喜欢的段落，按你自己命名的节点合并成一份主跑团记录。
          下方段落不是锁死的——你可以改段落名、加段、删段、换段。自定义版本请用「单选」。
        </p>
        <div class="collage-base-row">
          <label>主线骨架（其他段落未指定时默认跟它）：</label>
          <el-select v-model="collageBase" class="from-select" popper-class="collage-popper">
            <el-option
              v-for="o in FROM_OPTIONS"
              :key="'base-' + o.value"
              :value="o.value"
              :label="o.label"
            >
              <div class="opt-row">
                <span class="opt-num">第{{ o.value + 1 }}场</span>
                <el-tag v-if="o.flavor" size="small" type="warning" class="opt-flavor">{{ o.flavor }}</el-tag>
              </div>
              <div class="opt-one">{{ o.one_liner }}</div>
            </el-option>
          </el-select>
        </div>
        <div class="collage-base-one">
          <span class="chip">{{ optionOf(collageBase).shortLabel }}</span>
          <span class="one-liner-txt">{{ optionOf(collageBase).one_liner }}</span>
        </div>

        <div class="section-grid">
          <div v-for="(row, idx) in collageRows" :key="row.id" class="section-row">
            <span class="section-idx">{{ idx + 1 }}.</span>
            <el-input
              v-model="row.label"
              class="section-label-input"
              placeholder="段落名（可改）"
            />
            <el-select v-model="row.from" class="from-select" popper-class="collage-popper"
                       :placeholder="`默认：${optionOf(collageBase).shortLabel}`">
              <el-option
                v-for="o in FROM_OPTIONS"
                :key="row.id + '-' + o.value"
                :value="o.value"
                :label="o.label"
              >
                <div class="opt-row">
                  <span class="opt-num">第{{ o.value + 1 }}场</span>
                  <el-tag v-if="o.flavor" size="small" type="warning" class="opt-flavor">{{ o.flavor }}</el-tag>
                </div>
                <div class="opt-one">{{ o.one_liner }}</div>
              </el-option>
            </el-select>
            <el-input
              v-model="row.note"
              placeholder="备注（可选：想保留的细节/要删掉的枝节）"
              size="small"
              class="section-note"
            />
            <el-button link type="primary" size="small" @click="renameSection(row.id)" title="重命名">✎</el-button>
            <el-button link type="danger" size="small" @click="removeSection(row.id)" title="删除此段">✕</el-button>
          </div>
          <div class="selected-summary" v-for="row in collageRows" :key="'sum-' + row.id">
            <span class="sum-section">{{ row.label }}</span>
            <span class="chip chip-sm">{{ optionOf(row.from).shortLabel }}</span>
            <span class="one-liner-txt one-liner-txt-sm">{{ optionOf(row.from).one_liner }}</span>
          </div>
        </div>

        <div class="add-section-row">
          <el-popover
            v-model:visible="addSecMenuVisible"
            placement="bottom-start"
            :width="320"
            trigger="click"
          >
            <template #reference>
              <el-button size="small" plain>+ 添加段落</el-button>
            </template>
            <div class="preset-list">
              <div class="preset-tip">从常用段落里挑：</div>
              <el-button
                v-for="p in SECTION_PRESETS"
                :key="p.key"
                size="small"
                text
                class="preset-btn"
                @click="addPresetSection(p)"
              >{{ p.label }}</el-button>
              <el-divider style="margin: 8px 0" />
              <el-button size="small" type="primary" link @click="addCustomSection">✎ 自定义段落名…</el-button>
            </div>
          </el-popover>
        </div>

        <el-input
          v-model="collageUserNote"
          type="textarea"
          :rows="4"
          placeholder="作者备注：整体基调、想保留的细节、后续调整说明…"
          class="user-note"
        />
        <div class="actions">
          <el-button type="success" size="large" @click="onCollage">🧩 合成主跑团记录</el-button>
        </div>
      </el-card>
    </div>

    <!-- 已完成态 -->
    <div v-else-if="uiState === 'completed'" class="sim-completed">
      <el-card shadow="never">
        <div class="completed-head">
          <el-tag type="success" size="large">
            {{ completed?.mode === 'collage' ? '拼贴合成' : completed?.mode === 'custom' ? '自定义' : '单选主线' }}
          </el-tag>
          <span class="summary">{{ completed?.summary || '' }}</span>
        </div>
        <div class="completed-body">
          <div class="summary-panel">
            <h4>小说化梗概（原剧本经过）</h4>
            <el-scrollbar max-height="560">
              <pre class="summary-pre">{{ completedSummary }}</pre>
            </el-scrollbar>
          </div>
          <!-- 通稿（第二步）：完整故事，与梗概并列。作者主要看这一份。 -->
          <div class="raw-toggle" v-if="completedNarrative">
            <el-button link type="success" size="small" @click="showNarrative = !showNarrative">
              {{ showNarrative ? '收起通稿' : `查看通稿（完整故事，${completedNarrative.length} 字）` }}
            </el-button>
          </div>
          <el-collapse-transition>
            <div v-if="showNarrative && completedNarrative" class="raw-panel">
              <div class="mat-hint">本场完整故事通稿：每一拍都讲到了，可直接作为下一阶段的创作底本。</div>
              <el-scrollbar max-height="560">
                <pre class="narrative-pre">{{ completedNarrative }}</pre>
              </el-scrollbar>
            </div>
          </el-collapse-transition>
          <div class="raw-toggle">
            <el-button link type="info" size="small" @click="showRaw = !showRaw">
              {{ showRaw ? '收起跑团实况（素材）' : '查看跑团实况（原始素材）' }}
            </el-button>
          </div>
          <el-collapse-transition>
            <div v-if="showRaw" class="raw-panel">
              <div class="mat-hint">完整 KP/PL 对话日志，作为素材归档；正文写作以梗概为准。</div>
              <el-scrollbar max-height="400">
                <pre class="transcript-pre">{{ completedTranscript || '（无原始日志）' }}</pre>
              </el-scrollbar>
            </div>
          </el-collapse-transition>
        </div>
        <div class="actions">
          <el-button @click="onReset">重跑</el-button>
          <el-button type="primary" size="large" @click="emit('done')">下一阶段：魂穿唐迟</el-button>
        </div>
      </el-card>
    </div>

    <div v-else-if="uiState === 'loading'" class="loading">加载中…</div>

    <!-- 自定义/编辑 dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'custom' ? '✏️ 自写跑团版本' : '✏️ 编辑此版跑团'"
      width="720px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="dialogLabel"
        placeholder="版本标签（如「全员殒落·改」「我自己的线」）"
        style="margin-bottom: 10px"
      />
      <el-input
        v-model="dialogText"
        type="textarea"
        :rows="16"
        placeholder="在此撰写完整跑团日志（full_log），会作为本卷的主跑团记录直接保存。"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="onDialogSave">
          {{ dialogMode === 'custom' ? '添加此版' : '保存为编辑版' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.coc-sim-view {
  padding: 24px 32px;
  max-width: 1480px;
  margin: 0 auto;
}

.sim-header .header-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}
.sim-header h2 {
  margin: 0;
  font-size: 26px;
}
.sim-header .sub {
  color: #666;
  margin: 12px 0 18px 0;
  font-size: 14px;
  line-height: 1.7;
}
.completed-banner {
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  color: #67c23a;
  padding: 14px 18px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
  font-size: 14px;
}

.directive-input {
  margin: 16px 0;
}
.options-collapse {
  margin: 12px 0;
}
.opt-row {
  padding: 16px 0;
}
.opt-row label {
  display: block;
  margin-bottom: 10px;
  font-size: 14px;
  color: #444;
}
/* 解释「上限」语义：作者容易以为拖得越大越慢，需要说明实际由估算决定 */
.opt-hint {
  margin: 8px 0 0;
  font-size: 13px;
  color: #98a2b3;
  line-height: 1.6;
}
.actions {
  margin-top: 24px;
  text-align: center;
}

.msg {
  margin: 16px 0;
  font-size: 15px;
  line-height: 1.7;
  color: #555;
}

/* 跑中态：简化三卡（不带选定按钮） */
.variant-cards-loading {
  margin-top: 24px;
}
.var-loading-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 18px;
}
.var-loading-card {
  border: 1px solid #ebeef5;
  border-radius: 10px;
  padding: 20px;
  background: #fafafa;
  min-height: 160px;
  font-size: 14px;
}
.var-label {
  font-weight: 600;
  font-size: 16px;
  color: #409eff;
  margin-bottom: 12px;
}
.var-one-liner {
  font-size: 14px;
  color: #666;
  line-height: 1.7;
}
.var-one-liner .muted {
  color: #bbb;
}

/* 拼贴区域 */
.collage-card {
  margin-top: 24px;
  background: #fafafa;
  padding: 8px;
}
.collage-card h3 {
  margin: 8px 0 8px 0;
  font-size: 17px;
}
.collage-card .hint {
  color: #888;
  font-size: 13.5px;
  line-height: 1.7;
  margin: 0 0 20px 0;
}
.collage-base-row {
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.collage-base-row label {
  font-weight: 600;
  font-size: 14px;
}
.collage-base-one {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: #fff;
  border: 1px dashed #d9e3f0;
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 18px;
  font-size: 14px;
  line-height: 1.7;
}
.section-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}
.section-row {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #fff;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid #eee;
}
.section-idx {
  color: #999;
  font-size: 14px;
  width: 22px;
  flex-shrink: 0;
}
.section-label-input {
  width: 210px;
  flex-shrink: 0;
}
.section-row .from-select {
  width: 300px;
  flex-shrink: 0;
}
.section-note {
  flex: 1;
  min-width: 180px;
}
.selected-summary {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 6px 12px 6px 42px;
  font-size: 13px;
  color: #666;
}
.sum-section {
  color: #333;
  font-weight: 600;
  min-width: 100px;
}
.chip {
  background: #ecf5ff;
  color: #409eff;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 13px;
  white-space: nowrap;
  flex-shrink: 0;
}
.chip-sm { font-size: 12px; padding: 1px 8px; }
.one-liner-txt {
  color: #888;
  font-size: 13px;
  line-height: 1.6;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.one-liner-txt-sm { font-size: 12px; -webkit-line-clamp: 1; }

.add-section-row {
  margin-bottom: 14px;
}
.preset-list .preset-tip {
  font-size: 13px;
  color: #888;
  margin-bottom: 8px;
}
.preset-list .preset-btn {
  display: inline-block;
  margin: 3px 6px 3px 0;
  font-size: 13px;
}

/* 下拉选项的两行式展示 */
:global(.collage-popper) .el-select-dropdown__item {
  height: auto;
  line-height: 1.5;
  padding: 10px 14px;
}
.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.opt-num {
  font-weight: 600;
  color: #409eff;
  font-size: 14px;
}
.opt-flavor {
  font-size: 12px;
}
.opt-one {
  color: #888;
  font-size: 13px;
  line-height: 1.5;
  margin-top: 4px;
  white-space: normal;
  word-break: break-all;
}

.user-note {
  margin-top: 12px;
}

.completed-head {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 16px;
}
.completed-head .summary {
  font-size: 15px;
  color: #333;
}
.log-scroll {
  margin: 16px 0;
}
.full-log {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 14px;
  line-height: 1.75;
  background: #f6f8fa;
  padding: 16px;
  border-radius: 8px;
  margin: 0;
}

/* ---- 平行宇宙卡片：梗概为主，实况折叠 ---- */
.variant-meta-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 12px;
}
.meta-chip {
  font-size: 13px;
  color: #666;
  background: #f0f2f5;
  padding: 3px 10px;
  border-radius: 10px;
}
.variant-transcript {
  margin-top: 12px;
  border-top: 1px dashed #e0e4ea;
  padding-top: 12px;
}
.mat-hint {
  font-size: 13px;
  color: #888;
  margin-bottom: 10px;
  line-height: 1.6;
}
.transcript-pre {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.7;
  background: #fafbfc;
  border: 1px solid #eef0f3;
  padding: 16px;
  border-radius: 6px;
  margin: 0;
  max-height: 360px;
  overflow: auto;
}
.state-error {
  margin-top: 8px;
  font-size: 13px;
  color: #c0392b;
  background: #fdecea;
  padding: 10px 12px;
  border-radius: 6px;
  line-height: 1.6;
}

/* ---- 实时推演面板（第四步）---- */
.live-panel {
  margin-top: 18px;
  border: 1px solid #e6e9ef;
  border-radius: 10px;
  background: #fbfcfd;
  padding-bottom: 4px;
}
.live-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 16px 20px 12px;
  border-bottom: 1px solid #eef0f4;
}
.live-title {
  font-size: 16px;
  font-weight: 600;
  color: #2f3a4a;
}
.live-hint {
  font-size: 13px;
  color: #98a2b3;
}
/* 三栏并列：每栏一场，独立滚动。三场是并行支线，混排会互相穿插。
   用 auto-fit 而不是固定断点：左侧「书脊」面板展开时可用宽度会变，
   写死断点会让三栏在窄窗口被压成三条缝。auto-fit 宽够就并排，
   不够自动整行换栏，每栏始终保持可读宽度。 */
.live-cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 14px;
  padding: 14px 16px 16px;
}
.live-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid #eceff4;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.live-col.is-active {
  border-color: #dbe3ee;
}
.live-col-head {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px 10px;
  border-bottom: 1px solid #f0f2f6;
  background: #fafbfd;
  border-radius: 8px 8px 0 0;
}
.live-col-title {
  font-size: 14px;
  font-weight: 600;
  color: #2f3a4a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 栏头状态：这一场「讲到哪了」，作者最关心的一句 */
.live-col-status {
  font-size: 13px;
  color: #1f6b45;
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.live-scroll {
  height: 420px;
  overflow-y: auto;
  padding: 14px;
  font-size: 13.5px;
  line-height: 1.75;
}
.live-empty {
  color: #b8bfcc;
  padding: 16px 0;
  font-size: 13.5px;
}
.live-row {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.live-text {
  flex: 1 1 auto;
  color: #4a5568;
  min-width: 0;
}
/* 模型思考流：淡灰斜体，与正文区分但都可读 */
.live-thinking .live-text {
  color: #9aa4b2;
  font-style: italic;
}
/* 正在吐的正文 */
.live-delta .live-text {
  color: #2f3a4a;
}
/* 每拍裁决：这是作者最关心的一行，要突出 */
.live-verdict {
  margin: 8px 0;
  padding: 8px 12px;
  border-left: 3px solid #4c9a6a;
  background: #f2faf5;
  border-radius: 0 6px 6px 0;
}
.live-verdict .live-text {
  color: #1f6b45;
  font-weight: 500;
}
/* notice（门禁提示 / failover） */
.live-notice .live-text {
  color: #b26a12;
}
/* 结果态候选卡：SushiCards 是全局共用组件，这里只在本页把它的框放大，
   不去改共用组件，避免影响其它阶段的卡片布局。 */
:deep(.sushi-cards) {
  gap: 20px;
}
:deep(.sushi-cand) {
  padding: 20px 20px 16px;
  border-radius: 10px;
}
:deep(.sushi-cand-title) {
  font-size: 17px;
  margin-bottom: 6px;
}
:deep(.sushi-cand-subtitle) {
  font-size: 14px;
  margin-bottom: 10px;
}
:deep(.sushi-cand-text) {
  font-size: 15px;
  line-height: 1.85;
  height: 560px;
  padding: 12px 14px;
  border-radius: 8px;
}
:deep(.sushi-cand-text--truncated) {
  height: 560px;
}
:deep(.sushi-cand-axis) {
  font-size: 14px;
  margin-bottom: 10px;
  padding: 6px 0;
}
:deep(.sushi-cand-extra) {
  font-size: 14px;
  max-height: 340px;
  padding: 12px;
}
:deep(.sushi-cand-rationale) {
  font-size: 13px;
  padding: 10px 12px;
}
:deep(.sushi-cand-pick) {
  margin-top: 14px;
  padding-top: 14px;
}
:deep(.sushi-cand-pick .el-button) {
  font-size: 15px;
  padding: 10px 32px;
}
:deep(.sushi-cand-idx) {
  top: 14px;
  right: 16px;
  font-size: 13px;
}
:deep(.sushi-cand-actions) {
  top: 14px;
  left: 16px;
}
:deep(.sushi-cand-icon-btn) {
  width: 30px;
  height: 30px;
  font-size: 14px;
}
:deep(.sushi-axis) {
  font-size: 14px;
  padding: 10px 14px;
}
:deep(.sushi-title-row) {
  margin-bottom: 14px;
}

/* 窄屏降级：auto-fit 已经自动换栏，这里只保留高度微调——
   纵向堆叠时每栏可以矮一点，否则一屏装不下一场。 */
@media (max-width: 900px) {
  .live-scroll {
    height: 320px;
  }
  .var-loading-card {
    min-height: 110px;
  }
}

/* ---- beat 进度与终止原因（第二步）---- */
.beat-progress {
  margin-top: 6px;
}
.phase-chip {
  background: #e8f0fe;
  color: #3a6ea5;
}
/* 终止徽标：绿=自然讲完，橙=被截断/硬约束。颜色即语义，作者一眼能分。 */
.ending-ok {
  background: #e8f6ed;
  color: #2e7d4f;
}
.ending-warn {
  background: #fdf1e0;
  color: #b26a12;
}
.ending-info {
  background: #f0f2f5;
  color: #666;
}
.ending-warn-note {
  margin-top: 6px;
  font-size: 12px;
  color: #b26a12;
  background: #fdf6ec;
  border-left: 3px solid #e0a458;
  padding: 6px 8px;
  border-radius: 4px;
  line-height: 1.5;
}
.narrative-chip {
  background: #eaf7ef;
  color: #2e7d4f;
}
.variant-narrative {
  margin-top: 12px;
  border-top: 1px dashed #cfe3d6;
  padding-top: 12px;
}
.narrative-pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 14.5px;
  line-height: 1.9;
  background: #fbfdfc;
  border: 1px solid #e6efe9;
  padding: 20px;
  border-radius: 8px;
  margin: 0;
  max-height: 560px;
  overflow: auto;
}

/* ---- 失败可见性 ----
 * 失败必须「一眼看见」，而不是靠读内容发现这是一句报错。
 * 旧实现把 traceback 写进 final_summary，卡片看起来是「一份有内容的梗概」。 */
.fail-summary {
  margin-bottom: 18px;
}
.fail-summary-body {
  font-size: 13.5px;
  line-height: 1.7;
}
.variant-failed {
  margin-bottom: 16px;
  padding: 16px 18px;
  border: 1px solid #fbc4c4;
  border-left: 3px solid #c45656;
  border-radius: 8px;
  background: #fef0f0;
}
.variant-failed-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.variant-failed-stage {
  font-size: 13px;
  color: #c45656;
  font-weight: 600;
}
.variant-failed-reason {
  font-size: 13px;
  color: #606266;
  line-height: 1.7;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.variant-failed-hint {
  font-size: 12.5px;
  color: #909399;
  line-height: 1.6;
  margin: 10px 0 12px;
}

/* ---- 已完成态：梗概面板 + 折叠实况 ---- */
.completed-body {
  margin: 18px 0;
}
.summary-panel h4 {
  margin: 0 0 12px;
  font-size: 16px;
  color: #333;
  font-weight: 600;
}
.summary-pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 15px;
  line-height: 1.9;
  background: #fafbfc;
  padding: 20px 24px;
  border-radius: 8px;
  border-left: 3px solid #409eff;
  margin: 0;
}
.raw-toggle {
  margin-top: 12px;
  text-align: right;
}
.raw-panel {
  margin-top: 10px;
}

.loading {
  text-align: center;
  color: #888;
  padding: 60px;
  font-size: 15px;
}
</style>
