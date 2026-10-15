<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SushiCards from '../components/SushiCards.vue'
import { scaffoldApi, type Job } from '../api'
import { currentProjectId } from '../state'

interface Props {
  volNo: number
}
const props = defineProps<Props>()
const emit = defineEmits<{
  (e: 'done'): void
}>()

// ---- 卡片定义 ----
type CardKind = 'list' | 'text'
interface CardDef {
  key: string
  label: string
  subtitle: string
  kind: CardKind
}
const CARD_DEFS: CardDef[] = [
  { key: 'death_nodes',    label: '💀 死亡节点',     subtitle: '谁在哪天怎么死（每行一个，格式：DayN · 人物 · 死法）',       kind: 'list' },
  { key: 'climax_nodes',   label: '🔥 高潮转折',     subtitle: '关键爆点（每行一个，格式：DayN · 标题 · 节拍描述 [权重★1-5]）', kind: 'list' },
  { key: 'clue_nodes',     label: '🔍 线索与红鲱鱼', subtitle: '5 条线索/误导（每行一个，格式：DayN · 地点 · 线索内容 末尾加 [红鲱鱼] 则标记）', kind: 'list' },
  { key: 'core_twist',     label: '🌀 核心诡计',     subtitle: '这卷最大的反转（100-200 字）',     kind: 'text' },
  { key: 'tangchi_start',  label: '⏱️ 唐迟第一秒',   subtitle: '穿入瞬间看到什么、第一个危机',     kind: 'text' },
  { key: 'cost_cap',       label: '💸 代价上限',     subtitle: '这卷唐迟要付的最大代价',           kind: 'text' },
]

// ---- 状态 ----
type Step = 'idle' | 'prefilling' | 'picking' | 'done'
const step = ref<Step>('idle')
const prefillJob = ref<Job | null>(null)
const progressMsg = ref('')
const progressPct = ref(0)
const prefillDirective = ref('')

// 三版候选：6 key → 3 items；list 的 item 是 {id, version_label, preview: []}；text 的 item 是 {id, version_label, content: string}
const cards = ref<Record<string, any[]>>({})
// 本地自定义/编辑追加项：key → any[]
const extraCards = ref<Record<string, any[]>>({})
// 已选 + 已锁
const picked = ref<Record<string, number>>({})     // key → variant idx (相对 allItems)
const locked = ref<Record<string, { idx: number; value: any; locked_at: string; is_custom?: boolean }>>({})

const volume = ref<any>(null)
let es: { close: () => void } | null = null

// ---- Dialog ----
const dialogVisible = ref(false)
const dialogMode = ref<'custom' | 'edit'>('custom')
const dialogKey = ref<string>('death_nodes')
const dialogEditIndex = ref<number>(-1) // index in allItems
const dialogText = ref('')

function dialogDef(): CardDef {
  return CARD_DEFS.find(d => d.key === dialogKey.value) || CARD_DEFS[0]
}

function allItems(key: string): any[] {
  const base = cards.value[key] || []
  const extra = extraCards.value[key] || []
  return [...base, ...extra]
}

function openCustom(def: CardDef) {
  dialogMode.value = 'custom'
  dialogKey.value = def.key
  dialogEditIndex.value = -1
  dialogText.value = ''
  dialogVisible.value = true
}

function openEdit(def: CardDef, idx: number) {
  const items = allItems(def.key)
  const it = items[idx]
  if (!it) return
  dialogMode.value = 'edit'
  dialogKey.value = def.key
  dialogEditIndex.value = idx
  if (def.kind === 'list') {
    // Serialize preview to lines
    const lines = (it.preview || []).map((p: any) => {
      if (def.key === 'death_nodes') {
        return `Day${p.day ?? ''} · ${p.who ?? ''} · ${p.how ?? ''}`
      } else if (def.key === 'climax_nodes') {
        const stars = '★'.repeat(Math.max(1, Math.min(5, Number(p.weight) || 1)))
        return `Day${p.day ?? ''} · ${p.title ?? ''} · ${p.beat ?? ''} ${stars}`
      } else {
        const rh = p.is_red_herring ? ' [红鲱鱼]' : ''
        return `Day${p.day ?? ''} · ${p.where ?? ''} · ${p.what ?? ''}${rh}`
      }
    })
    dialogText.value = lines.join('\n')
  } else {
    dialogText.value = it.content || ''
  }
  dialogVisible.value = true
}

function parseListLines(text: string, key: string): any[] {
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
  return lines.map((line, i) => {
    const parts = line.split('·').map(s => s.trim())
    if (key === 'death_nodes') {
      const [dayStr, who, how] = [parts[0] || '', parts[1] || '', parts.slice(2).join(' · ')]
      const day = parseInt(String(dayStr).replace(/^Day/i, '')) || (i + 1)
      return { day, who: who || '某人', how: how || line }
    } else if (key === 'climax_nodes') {
      // weight is stars at the end
      const starMatch = parts[parts.length - 1]?.match(/★+/)
      let weight = 3
      let beatIdx = 2
      if (starMatch) {
        weight = starMatch[0].length
        parts[parts.length - 1] = parts[parts.length - 1].replace(/★+/g, '').trim()
        beatIdx = parts.length - 1
      }
      const dayStr = parts[0] || ''
      const title = parts[1] || ''
      const beat = parts.slice(2, beatIdx + 1).join(' · ') || parts[2] || ''
      const day = parseInt(String(dayStr).replace(/^Day/i, '')) || (i + 1)
      return { day, title: title || `转折${i+1}`, beat: beat || line, weight }
    } else {
      // clue
      const isRH = /\[红鲱鱼\]/i.test(line)
      const cleanLine = line.replace(/\[红鲱鱼\]/ig, '').trim()
      const cps = cleanLine.split('·').map(s => s.trim())
      const dayStr = cps[0] || ''
      const where = cps[1] || ''
      const what = cps.slice(2).join(' · ') || cps[2] || cleanLine
      const day = parseInt(String(dayStr).replace(/^Day/i, '')) || (i + 1)
      return { day, where: where || `地点${i+1}`, what: what || cleanLine, is_red_herring: isRH }
    }
  })
}

function onDialogSave() {
  const text = dialogText.value.trim()
  if (!text) {
    ElMessage.warning('请填写内容')
    return
  }
  const key = dialogKey.value
  const def = dialogDef()
  const isEdit = dialogMode.value === 'edit'
  const editIdx = dialogEditIndex.value
  const base = cards.value[key] || []
  const extra = extraCards.value[key] || (extraCards.value[key] = [])

  let newItem: any
  const cid = `custom-${Date.now()}`
  if (def.kind === 'list') {
    const preview = parseListLines(text, key)
    newItem = {
      id: cid,
      is_custom: true,
      version_label: isEdit ? '✏️ 已编辑版' : '✏️ 自定义版',
      preview,
    }
  } else {
    newItem = {
      id: cid,
      is_custom: true,
      version_label: isEdit ? '✏️ 已编辑版' : '✏️ 自定义版',
      content: text,
    }
  }

  // Determine if editing an existing custom (within extra offset) or an AI card
  if (isEdit && editIdx >= 0) {
    if (editIdx < base.length) {
      // Editing an AI card → append as new custom, clear pick
      extra.push(newItem)
      picked.value[key] = base.length + extra.length - 1
    } else {
      // Editing custom in extra → replace
      const eIdx = editIdx - base.length
      extra.splice(eIdx, 1, newItem)
      picked.value[key] = editIdx
    }
  } else {
    extra.push(newItem)
    picked.value[key] = base.length + extra.length - 1
  }
  dialogVisible.value = false
  ElMessage.success(isEdit ? '已保存编辑' : '已添加自定义版')
}

// ---- 计算 ----
const lockedCount = computed(() => Object.keys(locked.value).length)
const allLocked = computed(() => lockedCount.value === 6)
const prefillDone = computed(() => Array.isArray(cards.value.death_nodes) && cards.value.death_nodes.length > 0)

function stageDone(): boolean {
  return !!(volume.value?.stage_completed?.scaffold)
}

// ---- 数据加载 ----
async function loadVolume() {
  try {
    const r = await scaffoldApi.getVolume(currentProjectId.value, props.volNo)
    volume.value = r.volume
    restoreFromVolume()
  } catch (e: any) {
    ElMessage.error(`读取卷数据失败：${e?.message || e}`)
  }
}

function restoreFromVolume() {
  const v = volume.value
  if (!v) return
  const sc = v.scaffold_cards || {}
  const dnAll = v.death_nodes
  const cnAll = v.climax_nodes
  const clAll = v.clue_nodes

  const hasListVariants = (k: string) => Array.isArray((v as any)[k]) && Array.isArray((v as any)[k][0])

  cards.value = {}
  extraCards.value = {}
  picked.value = {}
  locked.value = {}

  const buildList = (key: string, list3d: any[] | undefined) => {
    if (!Array.isArray(list3d)) return []
    return list3d.map((arr: any, i: number) => ({
      id: String(i),
      version_label: `第${i + 1}版`,
      preview: Array.isArray(arr) ? arr : [],
    }))
  }
  const buildText = (key: string) => {
    const variants = sc?.[key]?.variants
    if (!Array.isArray(variants)) return []
    return variants.map((t: string, i: number) => ({
      id: String(i),
      version_label: `第${i + 1}版`,
      content: t || '',
    }))
  }

  cards.value.death_nodes = buildList('death_nodes', dnAll)
  cards.value.climax_nodes = buildList('climax_nodes', cnAll)
  cards.value.clue_nodes = buildList('clue_nodes', clAll)
  cards.value.core_twist = buildText('core_twist')
  cards.value.tangchi_start = buildText('tangchi_start')
  cards.value.cost_cap = buildText('cost_cap')

  for (const def of CARD_DEFS) {
    const entry = sc?.[def.key]
    if (!entry?.locked_at) continue
    if (def.kind === 'list') {
      const arr = (v as any)[def.key]
      const idx = typeof entry.picked_idx === 'number' ? entry.picked_idx : 0
      const isCustom = entry.picked_idx === -1 || entry.is_custom
      locked.value[def.key] = { idx: isCustom ? -1 : idx, value: arr, locked_at: entry.locked_at, is_custom: isCustom }
      if (!isCustom) picked.value[def.key] = idx
    } else {
      const idx = typeof entry.picked_idx === 'number' ? entry.picked_idx : 0
      const isCustom = entry.picked_idx === -1 || entry.is_custom
      locked.value[def.key] = { idx: isCustom ? -1 : idx, value: entry.text, locked_at: entry.locked_at, is_custom: isCustom }
      if (!isCustom) picked.value[def.key] = idx
    }
  }

  if (stageDone()) step.value = 'done'
  else if (prefillDone.value) step.value = 'picking'
  else step.value = 'idle'
}

// ---- 预填 ----
async function startPrefill(force = false) {
  if (force && prefillDone.value) {
    try {
      await ElMessageBox.confirm(
        '重新预填会清空当前已选/已锁的 6 张卡（包括自定义项），确认重来？',
        '重新预填',
        { type: 'warning' },
      )
    } catch { return }
    try {
      await scaffoldApi.patchVolume(currentProjectId.value, props.volNo, {
        death_nodes: [],
        climax_nodes: [],
        clue_nodes: [],
        scaffold_cards: {},
        pasted_nodes: [],
      } as any)
    } catch (e: any) {
      ElMessage.error(`清空旧数据失败：${e?.message || e}`)
      return
    }
    await loadVolume()
  }

  step.value = 'prefilling'
  progressMsg.value = '正在启动 AI 预填任务…'
  progressPct.value = 0
  picked.value = {}
  locked.value = {}
  cards.value = {}
  extraCards.value = {}

  try {
    const r = await scaffoldApi.prefill(currentProjectId.value, props.volNo, {
      force: true,
      directive: prefillDirective.value.trim(),
    })
    prefillJob.value = r.job
    openSSE()
  } catch (e: any) {
    const d = e?.detail || e?.message || String(e)
    ElMessage.error(`预填启动失败：${typeof d === 'string' ? d : JSON.stringify(d)}`)
    step.value = 'idle'
  }
}

function openSSE() {
  closeSSE()
  es = scaffoldApi.subscribeJobs(currentProjectId.value, props.volNo, (ev) => {
    if (ev.type === 'job_update' && ev.job) applyJob(ev.job)
    else if (ev.type === 'job_update_batch' && ev.jobs?.length) {
      const mine = [...ev.jobs].reverse().find(j => j.kind === 'scaffold_prefill')
      if (mine) applyJob(mine)
    } else if (ev.type === 'job_idle') {
      scaffoldApi.listJobs(currentProjectId.value, props.volNo).then(r => {
        const mine = [...r.jobs].reverse().find(j => j.kind === 'scaffold_prefill')
        if (mine) applyJob(mine)
      }).catch(() => {})
      closeSSE()
    }
  })
}

function applyJob(j: Job) {
  prefillJob.value = j
  progressMsg.value = j.message || ''
  progressPct.value = j.progress || 0
  if (j.status === 'done') {
    const res = (j.result as any) || {}
    loadVolume().then(() => {
      if (prefillDone.value && !stageDone()) {
        step.value = 'picking'
      }
      ElMessage.success(res.is_mock ? 'AI 预填完成（mock 示例数据）' : 'AI 预填完成')
    })
    closeSSE()
  } else if (j.status === 'error') {
    ElMessage.error(`预填失败：${j.error || '未知错误'}`)
    step.value = 'idle'
    closeSSE()
  } else if (j.status === 'cancelled') {
    step.value = 'idle'
    closeSSE()
  }
}

function closeSSE() {
  if (es) { es.close(); es = null }
}

onMounted(() => {
  loadVolume().then(() => {
    scaffoldApi.listJobs(currentProjectId.value, props.volNo).then(r => {
      const active = r.jobs.find(j => j.kind === 'scaffold_prefill' && (j.status === 'pending' || j.status === 'running'))
      const done = r.jobs.find(j => j.kind === 'scaffold_prefill' && j.status === 'done')
      if (active) { prefillJob.value = active; openSSE(); step.value = 'prefilling' }
      else if (done && !prefillDone.value) { prefillJob.value = done; applyJob(done) }
    }).catch(() => {})
  })
})
onBeforeUnmount(() => closeSSE())

// ---- 选/锁 ----
function pickVersion(key: string, _id: string | null, index?: number) {
  if (index == null) return
  if (locked.value[key]) return
  picked.value[key] = index
}

async function lockThisCard(def: CardDef) {
  const idx = picked.value[def.key]
  if (idx == null) {
    ElMessage.warning('请先选一个版本')
    return
  }
  try {
    const items = allItems(def.key)
    const item = items[idx]
    const isCustom = !!(item as any)?.is_custom
    let value: any
    let r: any
    if (isCustom) {
      value = def.kind === 'list' ? item.preview : item.content
      r = await scaffoldApi.lockCard(currentProjectId.value, props.volNo, def.key, {
        custom_value: value,
      })
    } else {
      value = def.kind === 'list' ? item.preview : item.content
      r = await scaffoldApi.lockCard(currentProjectId.value, props.volNo, def.key, {
        pick_index: idx,
      })
    }
    locked.value[def.key] = {
      idx: isCustom ? -1 : idx,
      value,
      locked_at: r.locked_at,
      is_custom: isCustom,
    }
    ElMessage.success(`「${def.label}」已锁定 ✓${isCustom ? '（自定义）' : ''}`)
    await loadVolume()
    if (r.all_locked) ElMessage.success('6 卡全部锁定，可以落锤进节奏骨架了')
  } catch (e: any) {
    ElMessage.error(`锁定失败：${e?.message || e}`)
  }
}

async function finalize() {
  if (!allLocked.value) return
  try {
    await scaffoldApi.finalize(currentProjectId.value, props.volNo)
    ElMessage.success('副本详设落锤 ✓')
    emit('done')
  } catch (e: any) {
    ElMessage.error(`落锤失败：${e?.message || e}`)
  }
}

function onRerunCard(_def: CardDef, _idx: number | null, directive: string) {
  // 重新预填（6 卡一起），directive 走 prefillDirective
  prefillDirective.value = directive || prefillDirective.value
  startPrefill(prefillDone.value)
}

// ---- 展示 helpers ----
function weightStars(w: number): string {
  const n = Math.max(1, Math.min(5, Number(w) || 1))
  return '★'.repeat(n) + '☆'.repeat(5 - n)
}

function itemsFor(key: string): any[] {
  return allItems(key)
}
function pickedIdFor(key: string): string | null {
  const p = picked.value[key]
  if (p == null) return null
  const items = allItems(key)
  const it = items[p]
  return it ? String(it.id) : null
}
</script>

<template>
  <div class="scaffold-view">
    <div class="scaffold-header">
      <h2>🏗️ 副本详设</h2>
      <p class="scaffold-sub">AI 基于唐迟七日历一次性预填 6 张核心卡，每张挑最对味的一版锁定，6 卡齐了就进节奏骨架。支持 reroll 带指令重抽、自定义/编辑每一张卡。</p>
      <div class="scaffold-tags">
        <el-tag :type="lockedCount === 6 ? 'success' : 'info'" size="default">
          {{ lockedCount }} / 6 已锁定
        </el-tag>
        <el-tag v-if="prefillJob?.status === 'running'" type="warning" size="default">预填中</el-tag>
        <el-tag v-else-if="stageDone()" type="success" size="default">✓ 副本详设已完成</el-tag>
      </div>
    </div>

    <!-- 已完成态 -->
    <div v-if="stageDone()" class="scaffold-done">
      <el-alert type="success" :closable="false" show-icon title="副本详设已完成">
        <template #default>
          6 张卡已全部锁定落盘。可以进入下一阶段「节奏骨架」，或者重新预填（会清空当前选择）。
        </template>
      </el-alert>
      <div class="scaffold-done-actions">
        <el-button type="primary" size="large" @click="emit('done')">🎯 下一阶段：节奏骨架</el-button>
        <el-button size="large" @click="startPrefill(true)">🔄 重新预填</el-button>
      </div>
      <div class="scaffold-cards">
        <div v-for="def in CARD_DEFS" :key="def.key" class="scaffold-card-block">
          <div class="scaffold-card-title">
            <span>{{ def.label }}</span>
            <span class="scaffold-card-sub">{{ def.subtitle }}</span>
            <el-tag type="success" size="small">已锁定{{ locked[def.key]?.is_custom ? '（自定义）' : '' }}</el-tag>
          </div>
          <div class="scaffold-locked-preview">
            <template v-if="def.kind === 'list'">
              <ul class="bullet-list">
                <li v-for="(it, i) in (locked[def.key]?.value || [])" :key="i">
                  <template v-if="def.key === 'death_nodes'">
                    <span class="dy">Day{{ it.day }} · {{ it.who }}</span>
                    <span class="dd">{{ it.how }}</span>
                  </template>
                  <template v-else-if="def.key === 'climax_nodes'">
                    <span class="dy">Day{{ it.day }} · {{ it.title }} <i class="ws">{{ weightStars(it.weight) }}</i></span>
                    <span class="dd">{{ it.beat }}</span>
                  </template>
                  <template v-else>
                    <span class="dy">
                      Day{{ it.day }} · {{ it.where }}
                      <el-tag v-if="it.is_red_herring" size="small" type="danger" effect="plain">红鲱鱼</el-tag>
                    </span>
                    <span class="dd">{{ it.what }}</span>
                  </template>
                </li>
              </ul>
            </template>
            <template v-else>
              <p class="text-preview">{{ locked[def.key]?.value }}</p>
            </template>
          </div>
        </div>
      </div>
    </div>

    <!-- 未完成态 -->
    <template v-else>
      <!-- 未预填态 -->
      <div v-if="step === 'idle' && !prefillDone" class="scaffold-idle">
        <el-result icon="info" title="副本详设尚未生成" sub-title="AI 将基于你已经选定的副本骨架、跑团走向、宿主角色与唐迟七日历，一次性为 6 张卡各出 3 版差异化候选。可在下方写 reroll/整体指令。">
          <template #extra>
            <div class="idle-directive">
              <el-input
                v-model="prefillDirective"
                type="textarea"
                :rows="3"
                placeholder="可选：对 AI 预填的整体要求，比如「死亡节点必须包含门卫老陈」「核心诡计必须涉及蓝星遗物」"
              />
            </div>
            <el-button type="primary" size="large" @click="startPrefill(false)" style="margin-top:12px;">
              🎰 AI 一次性预填 6 卡（每卡 3 版差异化）
            </el-button>
          </template>
        </el-result>
        <p class="scaffold-hint">⚠️ 预填会调用模型，mock 短路条件：adapted_module 以「测试副本核文本」开头或宿主姓名含「林薇」时直接返回示例。</p>
      </div>

      <!-- 预填中 -->
      <div v-else-if="step === 'prefilling'" class="scaffold-prefilling">
        <el-progress :percentage="Math.floor(progressPct)" :stroke-width="14" :text-inside="true" />
        <p class="scaffold-progress-msg">{{ progressMsg || 'AI 正在并行构思死亡 / 高潮 / 线索 / 诡计 / 起点 / 代价…' }}</p>
      </div>

      <!-- 预填完成 / 选版 -->
      <div v-else class="scaffold-cards">
        <div class="scaffold-directive-bar">
          <el-input
            v-model="prefillDirective"
            placeholder="整体指令（reroll 时自动带入，也可随时修改后点 Reroll）"
            style="max-width:520px;"
          />
        </div>
        <div v-for="def in CARD_DEFS" :key="def.key" class="scaffold-card-block">
          <div class="scaffold-card-title">
            <span class="lbl">{{ def.label }}</span>
            <span class="scaffold-card-sub">{{ def.subtitle }}</span>
            <el-tag v-if="locked[def.key]" type="success" size="small">已锁定 ✓{{ locked[def.key]?.is_custom ? '（自定义）' : '' }}</el-tag>
            <el-tag v-else-if="picked[def.key] != null" type="warning" size="small">
              已选{{ (itemsFor(def.key)[picked[def.key]] as any)?.is_custom ? '（自定义）' : `第 ${picked[def.key] + 1} 版` }}
            </el-tag>
            <el-tag v-else type="info" size="small">未选</el-tag>
          </div>

          <!-- 已锁：展示锁定版本 -->
          <div v-if="locked[def.key]" class="scaffold-locked-preview">
            <template v-if="def.kind === 'list'">
              <ul class="bullet-list">
                <li v-for="(it, i) in (locked[def.key]?.value || [])" :key="i">
                  <template v-if="def.key === 'death_nodes'">
                    <span class="dy">Day{{ it.day }} · {{ it.who }}</span>
                    <span class="dd">{{ it.how }}</span>
                  </template>
                  <template v-else-if="def.key === 'climax_nodes'">
                    <span class="dy">Day{{ it.day }} · {{ it.title }} <i class="ws">{{ weightStars(it.weight) }}</i></span>
                    <span class="dd">{{ it.beat }}</span>
                  </template>
                  <template v-else>
                    <span class="dy">
                      Day{{ it.day }} · {{ it.where }}
                      <el-tag v-if="it.is_red_herring" size="small" type="danger" effect="plain">红鲱鱼</el-tag>
                    </span>
                    <span class="dd">{{ it.what }}</span>
                  </template>
                </li>
              </ul>
            </template>
            <template v-else>
              <p class="text-preview">{{ locked[def.key]?.value }}</p>
            </template>
          </div>

          <!-- 未锁：SushiCards ensemble -->
          <template v-else>
            <SushiCards
              mode="ensemble"
              :items="itemsFor(def.key)"
              :picked-id="pickedIdFor(def.key)"
              item-title-field="version_label"
              item-content-field=""
              :show-index="true"
              empty-label="暂无候选，请先 AI 预填"
              @pick="(id, idx) => pickVersion(def.key, id, idx)"
              @rerun="(idx, dir) => onRerunCard(def, idx, dir)"
              @custom-write="() => openCustom(def)"
              @edit-card="(idx) => openEdit(def, idx)"
            >
              <template #item="{ item }">
                <div class="sushi-item-inner">
                  <template v-if="def.kind === 'list'">
                    <ul class="bullet-list bullet-list--compact">
                      <li v-for="(it, ii) in (item.preview || [])" :key="ii">
                        <template v-if="def.key === 'death_nodes'">
                          <span class="dy">Day{{ it.day }} · {{ it.who }}</span>
                          <span class="dd">{{ it.how }}</span>
                        </template>
                        <template v-else-if="def.key === 'climax_nodes'">
                          <span class="dy">Day{{ it.day }} · {{ it.title }} <i class="ws">{{ weightStars(it.weight) }}</i></span>
                          <span class="dd">{{ it.beat }}</span>
                        </template>
                        <template v-else-if="def.key === 'clue_nodes'">
                          <span class="dy">
                            Day{{ it.day }} · {{ it.where }}
                            <el-tag v-if="it.is_red_herring" size="small" type="danger" effect="plain">红鲱鱼</el-tag>
                          </span>
                          <span class="dd">{{ it.what }}</span>
                        </template>
                      </li>
                    </ul>
                  </template>
                  <template v-else>
                    <p class="text-preview text-preview--card">{{ item.content }}</p>
                  </template>
                </div>
              </template>
            </SushiCards>

            <div class="card-actions">
              <el-button
                type="primary"
                :disabled="picked[def.key] == null"
                @click="lockThisCard(def)"
              >🔒 锁定这张卡</el-button>
            </div>
          </template>
        </div>

        <!-- Finalize 区 -->
        <div v-if="allLocked" class="scaffold-finalize">
          <el-result type="success" title="6 卡全部锁定" sub-title="卷内骨架就绪，落锤即可进入节奏骨架阶段">
            <template #extra>
              <el-button type="success" size="large" @click="finalize">🎯 完成详设，进入节奏骨架</el-button>
              <el-button size="default" @click="startPrefill(true)">🔄 全部重来</el-button>
            </template>
          </el-result>
        </div>

        <!-- 重新预填按钮 -->
        <div v-if="!allLocked && prefillDone" class="scaffold-reprefill">
          <el-button plain @click="startPrefill(true)">🔄 重新预填（清空已选）</el-button>
        </div>
      </div>
    </template>

    <!-- Dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'custom' ? `📝 自定义「${dialogDef().label}」` : `✏️ 编辑「${dialogDef().label}」`"
      width="680px"
      :close-on-click-modal="false"
    >
      <div v-if="dialogDef().kind === 'list'" class="dlg-hint">
        每行一条，格式参照 subtitle 提示。例：<br />
        <code v-if="dialogKey === 'death_nodes'">Day3 · 门卫老陈 · 在值班室被破门而入的怪物撕成两半</code>
        <code v-else-if="dialogKey === 'climax_nodes'">Day5 · 蓝星遗物反杀 · 唐迟主动触碰遗物反制邪教徒 ★★★★</code>
        <code v-else>Day2 · 图书馆旧档案柜 · 发现前宿友失踪前的借阅记录 [红鲱鱼]</code>
      </div>
      <el-input
        v-model="dialogText"
        type="textarea"
        :rows="dialogDef().kind === 'list' ? 10 : 8"
        :placeholder="dialogDef().kind === 'text' ? '请输入内容' : '每行一条，按格式填写'"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="onDialogSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.scaffold-view {
  max-width: 1080px;
  margin: 0 auto;
  padding: 24px 20px 48px;
  color: #1f1d2b;
}
.scaffold-header {
  margin-bottom: 20px;
  border-bottom: 1px solid #eee;
  padding-bottom: 14px;
}
.scaffold-header h2 {
  margin: 0 0 6px;
  font-size: 22px;
  letter-spacing: 1px;
}
.scaffold-sub {
  margin: 0 0 10px;
  color: #6b6880;
  font-size: 13px;
}
.scaffold-tags {
  display: flex;
  gap: 8px;
}
.idle-directive {
  max-width: 560px;
  margin: 0 auto;
  text-align: left;
}
.scaffold-idle, .scaffold-prefilling {
  background: #f7f7fb;
  border-radius: 12px;
  padding: 24px;
  margin: 24px 0;
}
.scaffold-hint {
  text-align: center;
  color: #9a97b0;
  font-size: 12px;
  margin-top: 8px;
}
.scaffold-progress-msg {
  margin-top: 12px;
  color: #4b4866;
  font-size: 13px;
  text-align: center;
}
.scaffold-cards {
  display: flex;
  flex-direction: column;
  gap: 22px;
  margin-top: 18px;
}
.scaffold-directive-bar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: -8px;
}
.scaffold-card-block {
  background: #fcfcfe;
  border: 1px solid #eceaf3;
  border-radius: 12px;
  padding: 16px 18px;
}
.scaffold-card-title {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
  font-weight: 600;
  font-size: 16px;
  flex-wrap: wrap;
}
.scaffold-card-title .lbl::before {
  margin-right: 4px;
}
.scaffold-card-sub {
  font-weight: 400;
  color: #8c89a3;
  font-size: 12px;
}
.scaffold-locked-preview {
  background: #f2fbf4;
  border: 1px dashed #a8dfb6;
  border-radius: 8px;
  padding: 12px 14px;
}
.card-actions {
  margin-top: 10px;
  display: flex;
  justify-content: flex-end;
}
.scaffold-finalize {
  margin-top: 24px;
  background: #f3fbf5;
  border: 1px solid #b9e6c7;
  border-radius: 12px;
}
.scaffold-reprefill {
  text-align: center;
  margin-top: 8px;
}
.scaffold-done {
  margin-top: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.scaffold-done-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
}
.bullet-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bullet-list--compact { gap: 6px; }
.bullet-list li {
  padding: 6px 8px;
  background: rgba(105, 75, 155, 0.04);
  border-left: 3px solid #8e6fd3;
  border-radius: 4px;
}
.bullet-list .dy {
  display: block;
  font-weight: 600;
  color: #3d2f5e;
  font-size: 13px;
  margin-bottom: 2px;
}
.bullet-list .dd {
  display: block;
  color: #4b4866;
  font-size: 12.5px;
  line-height: 1.6;
}
.ws {
  color: #e8a130;
  font-style: normal;
  font-size: 12px;
  letter-spacing: 1px;
  margin-left: 4px;
}
.text-preview {
  margin: 0;
  font-size: 13.5px;
  line-height: 1.75;
  color: #2e2b44;
  white-space: pre-wrap;
}
.text-preview--card {
  font-size: 12.5px;
  line-height: 1.7;
}
.sushi-item-inner {
  padding: 2px 0;
}
.dlg-hint {
  font-size: 12px;
  color: #909399;
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px 10px;
  margin-bottom: 10px;
  line-height: 1.6;
}
.dlg-hint code {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: #606266;
}
</style>
