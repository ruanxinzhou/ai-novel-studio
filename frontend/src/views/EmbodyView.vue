<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElProgress } from 'element-plus'
import SushiCards from '../components/SushiCards.vue'
import { embodyApi } from '../api'
import { provideChatContext } from '../chatContext'
import type {
  EmbodyCandidate,
  EmbodyEntryPoint,
  EmbodyStance,
  EmbodyOutline,
  Job,
} from '../api'
import { currentProjectId } from '../state'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
  (e: 'back-to-simulate'): void
}>()

// ---- 步骤状态 ----
// 0 选身体 / 1 选切入点 / 2 选立场 / 3 生成大纲 / 4 完成
const step = ref(0)

const candidates = ref<EmbodyCandidate[]>([])
const loadingCandidates = ref(false)
const candidate = ref<EmbodyCandidate | null>(null)

const entryPoints = ref<EmbodyEntryPoint[]>([])
const loadingEntryPoints = ref(false)
const entryPoint = ref<EmbodyEntryPoint | null>(null)

const stances = ref<EmbodyStance[]>([])
const loadingStances = ref(false)
const stance = ref<EmbodyStance | null>(null)
const userDirective = ref('')

const outlines = ref<EmbodyOutline[]>([])
const outlineJob = ref<Job | null>(null)
const generating = ref(false)
const genProgress = ref(0)
const genMessage = ref('')
const pickedOutlineId = ref<string | null>(null)
const pickedOutline = ref<EmbodyOutline | null>(null)

// 注册聊天上下文：魂穿入戏三选一（宿主/切入点/反应）+ 最终五版大纲
provideChatContext(() => {
  const step_names = ['选宿主（candidates）', '选切入点（entry_points）', '选第一反应（stances）', '生成五版大纲', '完成']
  const picked = pickedOutline.value
  return {
    phase: '🫀 魂穿入戏',
    system_hint:
      '作者在 4 步魂穿入戏：选宿主→选切入点（三幕：假死因红鲱鱼→核心反转→规则利用破局）→选第一反应→生成五版三折死因大纲。'
      + '回答时先看 context.step / context.candidate / context.entry_point / context.stance 定位当前哪一步，'
      + '再给具体改法；尤其要遵守：主角能力必须长线复用（不能只针对单本剧本）、三折死因链必须完整。',
    context: {
      volume_no: props.volNo,
      step_idx: step.value,
      step_name: step_names[step.value] || '',
      candidates: candidates.value.map(c => ({
        id: c.id, name: c.name, role: c.role, death: c.death, summary: (c.summary || c.bio || '').slice(0, 400),
      })),
      selected_candidate: candidate.value ? {
        id: candidate.value.id, name: candidate.value.name, role: candidate.value.role,
        death: candidate.value.death, summary: candidate.value.summary || candidate.value.bio || '',
      } : null,
      entry_points: entryPoints.value.map(e => ({
        id: e.id, act: e.act, title: e.title, false_cause: e.false_cause,
        core_twist: e.core_twist, rule_exploit: e.rule_exploit, summary: (e.summary || '').slice(0, 400),
      })),
      selected_entry_point: entryPoint.value ? {
        id: entryPoint.value.id, act: entryPoint.value.act, title: entryPoint.value.title,
        false_cause: entryPoint.value.false_cause, core_twist: entryPoint.value.core_twist,
        rule_exploit: entryPoint.value.rule_exploit, summary: entryPoint.value.summary || '',
      } : null,
      stances: stances.value.map(s => ({
        id: s.id, label: s.label, tone: s.tone,
        summary: (s.summary || s.description || '').slice(0, 400),
      })),
      selected_stance: stance.value ? {
        id: stance.value.id, label: stance.value.label, tone: stance.value.tone,
        summary: stance.value.summary || stance.value.description || '',
      } : null,
      user_directive: userDirective.value || null,
      outlines_count: outlines.value.length,
      selected_outline: picked ? {
        id: picked.id, title: picked.title, tone: picked.tone,
        false_cause: picked.false_cause, core_twist: picked.core_twist, rule_exploit: picked.rule_exploit,
        text: picked.text || picked.summary || '',
        acts: Array.isArray((picked as any).acts) ? (picked as any).acts.map((a: any) => ({
          name: a?.name || a?.act, summary: (a?.summary || '').slice(0, 300),
        })) : [],
      } : null,
    },
  }
})

const completing = ref(false)
const completedHost = ref<EmbodyCandidate | null>(null)
const completedOutlineText = ref('')

let sse: { close: () => void } | null = null

// ---- 自定义/编辑 dialog ----
const dialogVisible = ref(false)
const dialogMode = ref<'custom' | 'edit'>('custom')
const dialogStep = ref<0 | 1 | 2 | 3>(0)
const dialogEditIndex = ref<number>(-1)
const dialogLabel = ref('')
const dialogText = ref('')

function openCustom(stepNo: 0 | 1 | 2 | 3) {
  dialogMode.value = 'custom'
  dialogStep.value = stepNo
  dialogEditIndex.value = -1
  dialogLabel.value = ''
  dialogText.value = ''
  dialogVisible.value = true
}

function openEdit(stepNo: 0 | 1 | 2 | 3, idx: number) {
  dialogMode.value = 'edit'
  dialogStep.value = stepNo
  dialogEditIndex.value = idx
  const list = [candidates.value, entryPoints.value, stances.value, outlines.value][stepNo] as any[]
  const it = list[idx]
  if (!it) return
  // Prefill
  if (stepNo === 0) {
    dialogLabel.value = (it.name || '').replace(/^✏️\s*/, '')
    dialogText.value =
      `【身体状态】${it.body_state || ''}\n` +
      `【求生钩子】${it.struggle_hook || ''}\n` +
      `【SAN】${it.san_loss || '未知'}`
  } else if (stepNo === 1) {
    dialogLabel.value = (it.name || '').replace(/^✏️\s*/, '')
    dialogText.value =
      `【场景】${it.description || ''}\n\n【信息差】${it.info_gap || ''}`
  } else if (stepNo === 2) {
    dialogLabel.value = (it.name || '').replace(/^✏️\s*/, '')
    dialogText.value = it.description || ''
  } else {
    dialogLabel.value = (it.title || '').replace(/^✏️\s*/, '')
    const days = (it.day_breakdown || []).map((d: any) => `Day${d.day}：${d.summary}`).join('\n')
    dialogText.value =
      (it.tangchi_entry ? `【魂穿设定】${it.tangchi_entry}\n\n` : '') +
      (days ? `【七日流程】\n${days}\n\n` : '') +
      (it.climax ? `【第七日高潮】\n${it.climax}\n\n` : '') +
      (it.blue_star_hook ? `【蓝星钩子】${it.blue_star_hook}\n` : '') +
      (it.outline_text ? `\n【完整大纲】\n${it.outline_text}` : '')
  }
  dialogVisible.value = true
}

function onDialogSave() {
  const text = dialogText.value.trim()
  if (!text) {
    ElMessage.warning('请填写内容')
    return
  }
  const label = dialogLabel.value.trim() || '自定义'
  const stepNo = dialogStep.value
  const idx = dialogEditIndex.value
  const isEdit = dialogMode.value === 'edit'

  let newItem: any
  if (stepNo === 0) {
    // Candidate
    newItem = {
      id: `custom-${Date.now()}`,
      is_custom: true,
      name: `✏️ ${label}`,
      role: '自定义',
      age: null,
      tag: '自编',
      body_state: text,
      struggle_hook: '',
      san_loss: '自定义',
      death_countdown: isEdit ? '已编辑' : '自定义',
    } as EmbodyCandidate
  } else if (stepNo === 1) {
    newItem = {
      id: `custom-${Date.now()}`,
      is_custom: true,
      name: `✏️ ${label}`,
      time_anchor: '自定义',
      description: text,
      info_gap: '',
    } as EmbodyEntryPoint
  } else if (stepNo === 2) {
    newItem = {
      id: `custom-${Date.now()}`,
      is_custom: true,
      name: `✏️ ${label}`,
      tendency: '自定义',
      cost: '自定义',
      description: text,
    } as EmbodyStance
  } else {
    // Outline
    newItem = {
      index: Date.now(),
      is_custom: true,
      title: `✏️ ${label}`,
      one_liner: text.slice(0, 60),
      flavor: '自定义',
      pacing: 'custom',
      tangchi_entry: '',
      day_breakdown: [],
      climax: '',
      blue_star_hook: '',
      outline_text: text,
    } as EmbodyOutline
  }

  const targetList = [candidates, entryPoints, stances, outlines][stepNo] as { value: any[] }
  const arr = targetList.value
  if (isEdit && idx >= 0) {
    const orig = arr[idx]
    if (orig?.is_custom) {
      arr.splice(idx, 1, newItem)
    } else {
      // Edit on AI card → append as new custom
      arr.push(newItem)
      // Also auto-select this new item? No; let user pick.
    }
  } else {
    arr.push(newItem)
  }
  dialogVisible.value = false
  ElMessage.success(isEdit ? '已保存编辑' : '已添加自定义卡片')
}

// ---- Rerun helpers ----
function onRerunStep(stepNo: 0 | 1 | 2 | 3, _idx: number | null, directive: string) {
  const extra = directive ? `（微调指令：${directive}）` : ''
  if (stepNo === 0) {
    ElMessage.info(`重新打捞魂穿候选${extra}…`)
    loadCandidates()
  } else if (stepNo === 1) {
    if (!candidate.value) return
    ElMessage.info(`重新推演切入点${extra}…`)
    loadEntryPoints(candidate.value)
  } else if (stepNo === 2) {
    ElMessage.info(`重新推演立场${extra}…`)
    loadStances()
  } else {
    // Outlines - re-run with directive appended
    const extraDirective = directive
      ? (userDirective.value ? userDirective.value + '\n' : '') + `【微调要求】${directive}`
      : userDirective.value
    userDirective.value = extraDirective
    startGenerate(true)
  }
}

// ---- SushiCards items 适配 ----
function decorateCustom<T extends Record<string, any>>(arr: T[], badgeCustom: string): any[] {
  return arr.map((c: any) => {
    if (c.is_custom && !c._is_decorated) {
      c._is_decorated = true
      // add ✏️ prefix if missing
      const nameField = c.name !== undefined ? 'name' : (c.title !== undefined ? 'title' : null)
      if (nameField && typeof c[nameField] === 'string' && !c[nameField].startsWith('✏️')) {
        c[nameField] = '✏️ ' + c[nameField]
      }
    }
    return c
  })
}

const candidateCards = computed(() =>
  decorateCustom(candidates.value, '自定义').map((c) => ({
    ...c,
    _title: c.name,
    _subtitle: `${c.role || ''}${c.age ? ` · ${c.age}岁` : ''}${c.tag ? ` · ${c.tag}` : ''}`,
    _badge: c.death_countdown || c.san_loss || (c.is_custom ? '自编' : ''),
    _content:
      `【身体状态】${c.body_state || ''}\n` +
      `【求生钩子】${c.struggle_hook || ''}\n` +
      `【SAN】${c.san_loss || '未知'}`,
  })),
)

const entryPointCards = computed(() =>
  decorateCustom(entryPoints.value, '自定义').map((ep) => ({
    ...ep,
    _title: ep.name,
    _subtitle: ep.time_anchor || '',
    _badge: ep.is_custom ? '自编' : '切入点',
    _content:
      `【场景】${ep.description || ''}\n\n` +
      `【信息差】${ep.info_gap || ''}`,
  })),
)

const stanceCards = computed(() =>
  decorateCustom(stances.value, '自定义').map((s) => ({
    ...s,
    _title: s.name,
    _subtitle: s.tendency || '',
    _badge: s.cost || (s.is_custom ? '自编' : ''),
    _content: s.description || '',
  })),
)

const outlineCards = computed(() => {
  return decorateCustom(outlines.value, '自定义').map((o) => {
    const days = (o.day_breakdown || []).map((d: any) => `Day${d.day}：${d.summary}`).join('\n')
    return {
      ...o,
      _title: o.title || `第${(o.index ?? 0) + 1}版`,
      _subtitle: o.one_liner || '',
      _badge: o.pacing || o.flavor || (o.is_custom ? '自编' : ''),
      _content:
        (o.tangchi_entry ? `【魂穿设定】${o.tangchi_entry}\n\n` : '') +
        (days ? `【七日流程】\n${days}\n\n` : '') +
        (o.climax ? `【第七日高潮】\n${o.climax}\n\n` : '') +
        (o.blue_star_hook ? `【蓝星钩子】${o.blue_star_hook}\n` : '') +
        (o.outline_text && !days ? o.outline_text : ''),
    }
  })
})

// ---- 加载候选 ----
async function loadCandidates() {
  const pid = currentProjectId.value
  if (!pid) return
  loadingCandidates.value = true
  candidates.value = []
  try {
    const r = await embodyApi.candidates(pid, props.volNo)
    candidates.value = r.candidates || []
  } catch (e: any) {
    ElMessage.error(`加载魂穿候选失败：${e?.message || e}`)
  } finally {
    loadingCandidates.value = false
  }
}

// ---- 选择候选 ----
async function onPickCandidate(id: string) {
  const c = candidates.value.find((x) => String(x.id) === id)
  if (!c) return
  candidate.value = c
  entryPoint.value = null
  stance.value = null
  outlines.value = []
  pickedOutlineId.value = null
  pickedOutline.value = null
  step.value = 1
  await loadEntryPoints(c)
}

async function loadEntryPoints(c: EmbodyCandidate) {
  const pid = currentProjectId.value
  if (!pid) return
  loadingEntryPoints.value = true
  entryPoints.value = []
  try {
    const r = await embodyApi.entryPoints(pid, props.volNo, { candidate: c })
    entryPoints.value = r.entry_points || []
  } catch (e: any) {
    ElMessage.error(`加载切入点失败：${e?.message || e}`)
  } finally {
    loadingEntryPoints.value = false
  }
}

// ---- 选择切入点 ----
async function onPickEntryPoint(id: string) {
  const ep = entryPoints.value.find((x) => String(x.id) === id)
  if (!ep) return
  entryPoint.value = ep
  stance.value = null
  outlines.value = []
  pickedOutlineId.value = null
  pickedOutline.value = null
  step.value = 2
  await loadStances()
}

async function loadStances() {
  const pid = currentProjectId.value
  if (!pid || !candidate.value || !entryPoint.value) return
  loadingStances.value = true
  stances.value = []
  try {
    const r = await embodyApi.stances(pid, props.volNo, {
      candidate: candidate.value,
      entry_point: entryPoint.value,
    })
    stances.value = r.stances || []
  } catch (e: any) {
    ElMessage.error(`加载立场失败：${e?.message || e}`)
  } finally {
    loadingStances.value = false
  }
}

// ---- 选择立场 ----
function onPickStance(id: string) {
  const s = stances.value.find((x) => String(x.id) === id)
  if (!s) return
  stance.value = s
}

// ---- 返回上一步 ----
function goBack() {
  if (step.value === 4) return
  if (step.value === 3 && outlines.value.length > 0) {
    step.value = 2
    return
  }
  if (step.value > 0) step.value -= 1
}

// ---- 返回上一阶段（跑团）----
async function goBackToSimulate() {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    await fetch(`/api/projects/${pid}/workbench/volumes/${props.volNo}/embody/back-to-simulate`, { method: 'POST' })
    emit('back-to-simulate')
  } catch (e: any) {
    ElMessage.error('返回失败：' + (e?.message ?? e))
  }
}

// ---- 启动 5 版大纲 ----
async function startGenerate(isRerun = false) {
  const pid = currentProjectId.value
  if (!pid || !candidate.value || !entryPoint.value || !stance.value) return
  generating.value = true
  genProgress.value = 3
  genMessage.value = isRerun ? '正在按新指令重新生成 5 版大纲…' : '正在启动 5 版大纲并行生成…'
  outlines.value = []
  pickedOutlineId.value = null
  pickedOutline.value = null
  step.value = 3
  try {
    const r = await embodyApi.generateOutline(pid, props.volNo, {
      candidate: candidate.value,
      entry_point: entryPoint.value,
      stance: stance.value,
      user_directive: userDirective.value,
      n: 5,
    })
    outlineJob.value = r.job
    openSSE()
  } catch (e: any) {
    generating.value = false
    ElMessage.error(`启动大纲生成失败：${e?.message || e}`)
  }
}

function openSSE() {
  const pid = currentProjectId.value
  if (!pid) return
  closeSSE()
  sse = embodyApi.subscribeJobs(pid, props.volNo, (ev) => {
    if (ev.type === 'job_update' && ev.job && outlineJob.value && ev.job.job_id === outlineJob.value.job_id) {
      applyJob(ev.job)
    } else if (ev.type === 'job_update_batch' && ev.jobs) {
      const mine = [...ev.jobs].reverse().find((j) => j.kind === 'embody_outline5')
      if (mine) applyJob(mine)
    } else if (ev.type === 'job_idle') {
      // 收尾
    }
  })
}

function applyJob(j: Job) {
  outlineJob.value = j
  genProgress.value = j.progress ?? 0
  genMessage.value = j.message || ''
  if (j.sub_results && Array.isArray(j.sub_results)) {
    outlines.value = j.sub_results as EmbodyOutline[]
  }
  if ((j.result as any)?.outlines) {
    outlines.value = (j.result as any).outlines as EmbodyOutline[]
  }
  if (j.status === 'done') {
    generating.value = false
    closeSSE()
  } else if (j.status === 'error') {
    generating.value = false
    closeSSE()
    ElMessage.error(`大纲生成失败：${j.error || '未知错误'}`)
  }
}

function closeSSE() {
  if (sse) {
    sse.close()
    sse = null
  }
}

// ---- 选版落盘 ----
async function pickOutlineById(id: string, _idx?: number) {
  const pid = currentProjectId.value
  if (!pid || !candidate.value || !entryPoint.value || !stance.value) return
  const idx = outlines.value.findIndex((o) => String(o.index) === id || id === String(o.index))
  const realIdx = idx >= 0 ? idx : Number(id)
  const target = outlines.value[realIdx]
  if (!target) return
  pickedOutlineId.value = String(target.index)
  pickedOutline.value = target
  completing.value = true
  try {
    const isCustom = !!(target as any).is_custom
    const body: any = {
      candidate: candidate.value,
      entry_point: entryPoint.value,
      stance: stance.value,
    }
    if (isCustom) {
      body.custom_data = { ...target }
    } else {
      if (!outlineJob.value) throw new Error('当前没有生成任务，无法选取 AI 版本')
      body.job_id = outlineJob.value.job_id
      body.pick_index = realIdx
    }
    const r = await embodyApi.pickOutline(pid, props.volNo, body)
    completedHost.value = candidate.value
    completedOutlineText.value = r.picked.outline_text || target.outline_text || ''
    outlines.value = (r.volume?.tangchi_story_outline ? [r.volume.tangchi_story_outline as EmbodyOutline] : outlines.value)
    step.value = 4
    ElMessage.success(isCustom ? '自定义魂穿大纲已落盘，embody 阶段完成。' : '魂穿设定已落盘，embody 阶段完成。')
  } catch (e: any) {
    ElMessage.error(`落盘失败：${e?.message || e}`)
  } finally {
    completing.value = false
  }
}

// ---- 重设（回到选身体）----
function resetAll() {
  step.value = 0
  candidate.value = null
  entryPoint.value = null
  stance.value = null
  userDirective.value = ''
  outlines.value = []
  outlineJob.value = null
  pickedOutlineId.value = null
  pickedOutline.value = null
  completedHost.value = null
  completedOutlineText.value = ''
  generating.value = false
}

onMounted(() => {
  loadCandidates()
})

onUnmounted(() => {
  closeSSE()
})

const stepLabels = ['选身体', '选切入点', '选立场', '生成大纲', '完成']
const activeStep = computed(() => Math.min(step.value, 4))
</script>

<template>
  <div class="embody-view">
    <div class="embody-header">
      <div class="embody-header-top">
        <h2>👻 唐迟魂穿·四步入壳</h2>
        <el-button size="small" @click="goBackToSimulate">← 返回跑团</el-button>
      </div>
      <p class="sub">
        在 3 个倒霉到骨子里的将死之人里选一个身体，挑准穿入时机、确立立场，让唐迟以一具被诅咒/已死/将死的皮囊，硬扛七日副本。
      </p>
      <el-steps :active="activeStep" finish-status="success" align-center class="embody-steps">
        <el-step v-for="(lb, i) in stepLabels" :key="i" :title="lb" />
      </el-steps>
    </div>

    <!-- Step 0：选身体 -->
    <div v-if="step === 0" class="embody-step">
      <h3>Step 1 · 选一具身体</h3>
      <p class="hint">挑一个倒霉蛋——唐迟穿进去的时候，他/她正处于最接近死亡/崩溃的那个瞬间。可以 reroll 整盘重抽，也可以自定义/编辑某一版。</p>
      <div v-if="loadingCandidates" class="loading-hint">正在打捞副本里最倒霉的几位……</div>
      <SushiCards
        v-else
        mode="ensemble"
        :items="candidateCards"
        :picked-id="candidate ? String(candidate.id) : null"
        axis-label="魂穿候选（将死/已死/被替换/SAN 归零）"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :is-active="true"
        @pick="onPickCandidate"
        @rerun="(idx, dir) => onRerunStep(0, idx, dir)"
        @custom-write="() => openCustom(0)"
        @edit-card="(idx) => openEdit(0, idx)"
      />
    </div>

    <!-- Step 1：选切入点 -->
    <div v-else-if="step === 1" class="embody-step">
      <div class="step-nav">
        <el-button size="small" @click="goBack">← 回到选身体</el-button>
        <h3>
          Step 2 · 选一个穿入时机
          <span class="who">（身体：{{ candidate?.name }}）</span>
        </h3>
      </div>
      <p class="hint">
        切入点决定唐迟开局知道<strong>多少</strong>、不知道<strong>什么</strong>——信息差是他活下来的唯一武器，也是他最大的陷阱。
      </p>
      <div v-if="loadingEntryPoints" class="loading-hint">正在为 {{ candidate?.name }} 推演 3 个致命瞬间……</div>
      <SushiCards
        v-else
        mode="ensemble"
        :items="entryPointCards"
        :picked-id="entryPoint ? String(entryPoint.id) : null"
        axis-label="穿入切入点（同一具身体，三种不同的开局死法）"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :is-active="true"
        @pick="onPickEntryPoint"
        @rerun="(idx, dir) => onRerunStep(1, idx, dir)"
        @custom-write="() => openCustom(1)"
        @edit-card="(idx) => openEdit(1, idx)"
      />
    </div>

    <!-- Step 2：选立场 -->
    <div v-else-if="step === 2" class="embody-step">
      <div class="step-nav">
        <el-button size="small" @click="goBack">← 回到切入点</el-button>
        <h3>
          Step 3 · 选唐迟的立场
          <span class="who">
            （{{ candidate?.name }} · {{ entryPoint?.name }}）
          </span>
        </h3>
      </div>
      <p class="hint">
        立场决定唐迟面对身边人/怪物/真相时的<strong>第一反应</strong>——克苏鲁副本里，选立场就是选 SAN 的掉法、选自己会变成什么样的人。
      </p>
      <div v-if="loadingStances" class="loading-hint">正在推演 3 种立场的代价……</div>
      <template v-else>
        <SushiCards
          mode="ensemble"
          :items="stanceCards"
          :picked-id="stance ? String(stance.id) : null"
          axis-label="唐迟的行动立场"
          item-title-field="_title"
          item-subtitle-field="_subtitle"
          item-content-field="_content"
          item-badge-field="_badge"
          :plate-count="3"
          :is-active="true"
          @pick="onPickStance"
          @rerun="(idx, dir) => onRerunStep(2, idx, dir)"
          @custom-write="() => openCustom(2)"
          @edit-card="(idx) => openEdit(2, idx)"
        />

        <el-card shadow="never" class="directive-card">
          <h4>📝 作者额外要求（可选，将作用于大纲生成与 reroll）</h4>
          <el-input
            v-model="userDirective"
            type="textarea"
            :rows="3"
            placeholder="比如：必须让唐迟和林薇原本暧昧的学长发生对手戏 / 不要让老陈死在自己值班的门卫室 / 希望 Day4 出现一次假的曙光……"
          />
        </el-card>

        <div class="step-actions">
          <el-button
            type="primary"
            size="large"
            :disabled="!stance"
            @click="startGenerate(false)"
          >
            🎲 开始并行生成 5 版唐迟七日大纲
          </el-button>
        </div>
      </template>
    </div>

    <!-- Step 3：生成大纲 + 选版 -->
    <div v-else-if="step === 3" class="embody-step">
      <div class="step-nav">
        <el-button size="small" @click="goBack">← 回到立场</el-button>
        <h3>
          Step 4 · 5 版七日大纲并行生成
          <span class="who">
            （{{ candidate?.name }} · {{ entryPoint?.name }} · {{ stance?.name }}）
          </span>
        </h3>
      </div>

      <div v-if="generating" class="gen-progress">
        <ElProgress :percentage="genProgress" :stroke-width="14" />
        <p class="gen-msg">{{ genMessage }}</p>
        <p class="hint">5 版大纲按不同节奏/克苏鲁基调并行生成，互相不会雷同，请稍候……</p>
      </div>

      <div v-if="outlines.length > 0" class="outline-results">
        <h4 v-if="!generating">🎯 请挑选一版作为唐迟的最终故事大纲（可 reroll / 自定义 / 编辑）</h4>
        <h4 v-else>已完成的版本（生成中…）</h4>
        <SushiCards
          mode="ensemble"
          :items="outlineCards"
          :picked-id="pickedOutlineId"
          axis-label="5 版唐迟七日大纲（不同节奏/基调）"
          item-title-field="_title"
          item-subtitle-field="_subtitle"
          item-content-field="_content"
          item-badge-field="_badge"
          :plate-count="5"
          :is-active="!generating && !completing"
          @pick="pickOutlineById"
          @rerun="(idx, dir) => onRerunStep(3, idx, dir)"
          @custom-write="() => openCustom(3)"
          @edit-card="(idx) => openEdit(3, idx)"
        />
      </div>
    </div>

    <!-- Step 4：完成态 -->
    <div v-else-if="step === 4" class="embody-complete">
      <h2>✅ 魂穿完成 · 唐迟的七日壳子已经穿好</h2>
      <div class="complete-card">
        <h3>
          🧟 魂穿角色：<span class="hl">{{ completedHost?.name }}</span>
          <span class="sub-role">（{{ completedHost?.role }}）</span>
        </h3>
        <p class="body-state">
          <strong>身体状态：</strong>{{ completedHost?.body_state }}
        </p>
        <p class="entry-line">
          <strong>切入点：</strong>{{ entryPoint?.name }}（{{ entryPoint?.time_anchor }}）— {{ entryPoint?.info_gap }}
        </p>
        <p class="stance-line">
          <strong>立场：</strong>{{ stance?.name }}（{{ stance?.tendency }}）
        </p>
        <div v-if="pickedOutline" class="picked-outline">
          <h4>📜 选定大纲：{{ pickedOutline.title }}
            <el-tag v-if="(pickedOutline as any).is_custom" size="small" type="warning" style="margin-left:8px;">自定义</el-tag>
          </h4>
          <p class="pacing-badge" :class="'p-' + (pickedOutline.pacing || pickedOutline.flavor || '').slice(0,4)">
            节奏：{{ pickedOutline.pacing || pickedOutline.flavor }}
          </p>
          <p class="one-liner">「{{ pickedOutline.one_liner }}」</p>
        </div>
        <details class="outline-text">
          <summary>展开完整大纲文本</summary>
          <pre>{{ completedOutlineText || (pickedOutline?.outline_text || '') }}</pre>
        </details>
      </div>

      <div class="complete-actions">
        <el-button size="large" type="primary" @click="emit('done')">
          ➡️ 下一阶段：副本详设（scaffold）
        </el-button>
        <el-button size="large" @click="resetAll">🔄 重设魂穿</el-button>
      </div>
    </div>

    <!-- Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'custom' ? '📝 自定义卡片' : '✏️ 编辑卡片'"
      width="600px"
      :close-on-click-modal="false"
    >
      <div class="dlg-row">
        <label>名称/标题</label>
        <el-input v-model="dialogLabel" placeholder="自定义名称" maxlength="40" />
      </div>
      <div class="dlg-row">
        <label>内容</label>
        <el-input
          v-model="dialogText"
          type="textarea"
          :rows="14"
          placeholder="请输入内容"
        />
      </div>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="onDialogSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.embody-view {
  padding: 20px 28px 40px;
  color: #e6e0d4;
  min-height: 100%;
}

.embody-header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.embody-header h2 {
  margin: 0 0 4px;
  font-size: 22px;
  letter-spacing: 1px;
  color: #d4b48a;
  text-shadow: 0 0 8px rgba(212, 180, 138, 0.25);
}
.embody-header .sub {
  margin: 0 0 16px;
  color: #8e8575;
  font-size: 13px;
}
.embody-steps {
  margin-bottom: 28px;
}
.embody-steps :deep(.el-step__title) {
  color: #8e8575;
  font-size: 13px;
}
.embody-steps :deep(.el-step__title.is-finish),
.embody-steps :deep(.el-step__title.is-process) {
  color: #d4b48a;
}

.embody-step h3 {
  margin: 0 0 6px;
  font-size: 17px;
  color: #c8b18a;
}
.embody-step .who {
  font-size: 13px;
  color: #8e8575;
  font-weight: normal;
  margin-left: 6px;
}
.hint {
  margin: 0 0 16px;
  color: #8e8575;
  font-size: 13px;
  line-height: 1.6;
}
.hint strong {
  color: #c98e8e;
}
.loading-hint {
  padding: 24px;
  text-align: center;
  color: #8e8575;
  font-style: italic;
}

.step-nav {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.directive-card {
  margin-top: 20px;
  background: rgba(30, 26, 22, 0.6) !important;
  border: 1px solid #3a332a !important;
  color: #e6e0d4;
}
.directive-card h4 {
  margin: 0 0 10px;
  color: #c8b18a;
  font-size: 14px;
}
.directive-card :deep(.el-textarea__inner) {
  background: rgba(20, 17, 14, 0.7);
  color: #e6e0d4;
  border-color: #3a332a;
}

.step-actions {
  margin-top: 22px;
  display: flex;
  justify-content: center;
}

.gen-progress {
  max-width: 720px;
  margin: 10px auto 24px;
  padding: 18px 20px;
  background: rgba(30, 26, 22, 0.5);
  border: 1px solid #3a332a;
  border-radius: 6px;
}
.gen-progress :deep(.el-progress-bar__outer) {
  background-color: #2a241d;
}
.gen-progress :deep(.el-progress-bar__inner) {
  background-color: #c4894e;
}
.gen-msg {
  margin: 10px 0 4px;
  color: #d4b48a;
  font-size: 13px;
}

.outline-results {
  margin-top: 18px;
}
.outline-results h4 {
  margin: 0 0 12px;
  color: #c8b18a;
  font-size: 15px;
}

.dlg-row {
  margin-bottom: 14px;
}
.dlg-row label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  color: #606266;
  font-weight: 500;
}

/* 完成态 */
.embody-complete {
  padding-top: 10px;
}
.embody-complete h2 {
  margin: 0 0 20px;
  color: #8fbf8f;
  font-size: 20px;
}
.complete-card {
  background: rgba(30, 26, 22, 0.7);
  border: 1px solid #3a332a;
  border-radius: 8px;
  padding: 22px 26px;
  margin-bottom: 22px;
}
.complete-card h3 {
  margin: 0 0 12px;
  color: #d4b48a;
}
.complete-card .hl {
  color: #c98e8e;
  font-size: 20px;
  margin-left: 4px;
}
.complete-card .sub-role {
  font-size: 13px;
  color: #8e8575;
  font-weight: normal;
  margin-left: 8px;
}
.complete-card p {
  margin: 8px 0;
  font-size: 13.5px;
  line-height: 1.7;
  color: #c9c0ae;
}
.complete-card strong {
  color: #d4b48a;
}
.picked-outline {
  margin-top: 16px;
  padding: 14px 18px;
  background: rgba(20, 17, 14, 0.5);
  border-left: 3px solid #c4894e;
  border-radius: 4px;
}
.picked-outline h4 {
  margin: 0 0 8px;
  color: #d4b48a;
}
.pacing-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  background: rgba(196, 137, 78, 0.18);
  color: #d4b48a;
  border: 1px solid rgba(196, 137, 78, 0.35);
  margin: 4px 0 8px;
}
.one-liner {
  color: #c9c0ae !important;
  font-style: italic;
  margin-top: 6px !important;
}
.outline-text {
  margin-top: 16px;
}
.outline-text summary {
  cursor: pointer;
  color: #8e8575;
  font-size: 13px;
  margin-bottom: 8px;
}
.outline-text pre {
  max-height: 360px;
  overflow: auto;
  padding: 14px 18px;
  background: rgba(15, 12, 10, 0.7);
  border-radius: 4px;
  color: #c9c0ae;
  font-size: 12.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: 'Courier New', monospace;
}

.complete-actions {
  display: flex;
  gap: 14px;
  justify-content: center;
}
.complete-actions .el-button--primary {
  background: linear-gradient(135deg, #8a5a2e, #c4894e);
  border-color: #c4894e;
}
</style>
