<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SushiCards from '../components/SushiCards.vue'
import {
  settleApi,
  type SettleForeshadow,
  type SettleEntityCheck,
  type SettleCostSummary,
  type SettleBlueStarHook,
  type SettleNextVolumeHint,
  type SettleOverview,
  type SettleEntityUpdate,
} from '../api'
import { currentProjectId } from '../state'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

// ---- 步骤状态（0 伏笔清点 / 1 实体污染 / 2 代价核算 / 3 蓝星钩子 / 4 下卷预告 / 5 落锤）----
const step = ref(0)
const stepLabels = ['伏笔清点', '实体污染检查', '代价核算', '蓝星钩子', '下卷预告', '落锤']
const loading = ref(false)
const activeStep = computed(() => step.value)

// ---- 基础数据 ----
const overview = ref<SettleOverview | null>(null)
const foreshadows = ref<SettleForeshadow[]>([])
const foreshadowDecisions = ref<Record<string, { status: 'paid' | 'dropped' | 'carried'; resolution?: string }>>({})

// ---- Step 1：实体污染 ----
const entityChecks = ref<SettleEntityCheck[]>([])
const entityAdopted = ref<Record<string, boolean>>({})
const entityNoteEdits = ref<Record<string, string>>({})
const entitySanEdits = ref<Record<string, number>>({})

// ---- Step 2：代价 ----
const cost = ref<SettleCostSummary | null>(null)
const contaminationDelta = ref(0)

// ---- Step 3：蓝星钩子（ensemble SushiCards）----
const blueStarHooks = ref<SettleBlueStarHook[]>([])
const pickedBlueStarId = ref<string | null>(null)
const pickedBlueStar = computed(() => blueStarHooks.value.find((h) => h.id === pickedBlueStarId.value) || null)
const loadingBlueStar = ref(false)

// ---- Step 4：下卷预告（ensemble SushiCards）----
const nextHints = ref<SettleNextVolumeHint[]>([])
const pickedNextId = ref<string | null>(null)
const pickedNext = computed(() => nextHints.value.find((h) => h.id === pickedNextId.value) || null)
const loadingNext = ref(false)

// ---- Step 5：落锤 ----
const applying = ref(false)
const finalizing = ref(false)
const finalized = ref(false)
const nextActionOptions = ref<string[]>([])
const nextDungeonTitle = ref('')
const creatingNext = ref(false)

// ============================================================
// 数据加载
// ============================================================

const pid = computed(() => currentProjectId.value || '')

async function loadOverview() {
  if (!pid.value) return
  loading.value = true
  try {
    const ov = await settleApi.overview(pid.value, props.volNo)
    overview.value = ov
    foreshadows.value = ov.foreshadows || []
    // 初始化伏笔决策（默认 carried）
    foreshadowDecisions.value = {}
    for (const f of foreshadows.value) {
      foreshadowDecisions.value[f.id] = { status: (f.status as any) || 'carried' }
    }
  } catch (e: any) {
    ElMessage.error(`加载杀青概览失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

onMounted(loadOverview)

// ============================================================
// Step 0 · 伏笔清点
// ============================================================

function setForeshadow(id: string, status: 'paid' | 'dropped' | 'carried') {
  const d = foreshadowDecisions.value[id] || { status: 'carried' }
  d.status = status
  foreshadowDecisions.value[id] = d
}

function setResolution(id: string, text: string) {
  const d = foreshadowDecisions.value[id] || { status: 'carried' }
  d.resolution = text
  foreshadowDecisions.value[id] = d
}

const unresolvedCount = computed(
  () => foreshadows.value.filter((f) => !foreshadowDecisions.value[f.id] || foreshadowDecisions.value[f.id].status === 'carried').length,
)

// ============================================================
// Step 1 · 实体污染检查
// ============================================================

async function loadEntityChecks() {
  if (!pid.value) return
  loading.value = true
  entityChecks.value = []
  try {
    const res = await settleApi.aiSuggest(pid.value, props.volNo, 'entity_check')
    const checks: SettleEntityCheck[] = res.entity_checks || res.checks || res.items || []
    entityChecks.value = checks
    for (const c of checks) {
      entityAdopted.value[c.entity_id] = true
      entityNoteEdits.value[c.entity_id] = c.note || ''
      entitySanEdits.value[c.entity_id] = c.san_delta ?? 0
    }
  } catch (e: any) {
    ElMessage.error(`实体污染 AI 建议失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

function toggleEntityAdopt(id: string) {
  entityAdopted.value[id] = !entityAdopted.value[id]
}

const adoptedEntityUpdates = computed<SettleEntityUpdate[]>(() =>
  entityChecks.value
    .filter((c) => entityAdopted.value[c.entity_id])
    .map((c) => ({
      id: c.entity_id,
      status: c.status_after,
      san_delta: entitySanEdits.value[c.entity_id] ?? c.san_delta,
      note: entityNoteEdits.value[c.entity_id] || c.note,
    })),
)

// ============================================================
// Step 2 · 代价核算
// ============================================================

async function loadCost() {
  if (!pid.value) return
  loading.value = true
  cost.value = null
  try {
    const res = await settleApi.aiSuggest(pid.value, props.volNo, 'cost', {
      entity_updates: adoptedEntityUpdates.value,
    })
    cost.value = {
      cost_summary: res.cost_summary || '',
      suggested_contamination_delta: res.suggested_contamination_delta ?? 3,
      san_loss_note: res.san_loss_note || '',
      new_echo: res.new_echo,
      kill_add: res.kill_add,
    }
    contaminationDelta.value = cost.value.suggested_contamination_delta
  } catch (e: any) {
    ElMessage.error(`代价核算 AI 建议失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

// ============================================================
// Step 3 · 蓝星钩子（SushiCards ensemble）
// ============================================================

async function loadBlueStar(directive?: string | null, baseIndex?: number | null) {
  if (!pid.value) return
  loadingBlueStar.value = true
  try {
    const res = await settleApi.aiSuggest(pid.value, props.volNo, 'blue_star_hook', {
      directive: directive || null,
      base_index: baseIndex ?? null,
      entity_updates: adoptedEntityUpdates.value,
      cost: cost.value,
    })
    const hooks: SettleBlueStarHook[] = res.hooks || res.items || res.options || []
    blueStarHooks.value = hooks.map((h, i) => ({
      ...h,
      id: h.id || `hook-${i}-${Date.now()}`,
    }))
    if (!pickedBlueStarId.value && blueStarHooks.value.length > 0) {
      pickedBlueStarId.value = blueStarHooks.value[0].id
    }
  } catch (e: any) {
    ElMessage.error(`蓝星钩子 AI 建议失败：${e?.message || e}`)
  } finally {
    loadingBlueStar.value = false
  }
}

function onBlueStarPick(id: string) {
  pickedBlueStarId.value = id
}

function onBlueStarRerun(idx: number | null, directive: string) {
  loadBlueStar(directive, idx)
}

async function onBlueStarCustom() {
  try {
    const { value: title } = await ElMessageBox.prompt('请输入自拟蓝星钩子的标题', '自拟蓝星钩子', {
      inputPlaceholder: '例：手机相册里多了一张没拍过的合照',
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
    })
    const { value: detail } = await ElMessageBox.prompt('请描述钩子的具体表现（会怎么纠缠唐迟）', '自拟蓝星钩子', {
      inputType: 'textarea',
      inputPlaceholder: '例：每次打开相册都会多出一张，照片里的人正对镜头笑，背景里却站着……',
      confirmButtonText: '加入',
      cancelButtonText: '取消',
    })
    const newCard: SettleBlueStarHook = {
      id: `hook-custom-${Date.now()}`,
      title: title || '自拟蓝星钩子',
      detail: detail || '',
      summary: '作者自拟',
    }
    blueStarHooks.value = [newCard, ...blueStarHooks.value]
    pickedBlueStarId.value = newCard.id
    ElMessage.success('已加入自拟钩子')
  } catch {
    /* 用户取消 */
  }
}

async function onBlueStarEdit(idx: number) {
  const h = blueStarHooks.value[idx]
  if (!h) return
  try {
    const { value: detail } = await ElMessageBox.prompt('微调钩子细节', '编辑钩子', {
      inputType: 'textarea',
      inputValue: h.detail || '',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    blueStarHooks.value[idx] = { ...h, detail }
  } catch {
    /* 取消 */
  }
}

const blueStarCards = computed(() =>
  blueStarHooks.value.map((h) => ({
    ...h,
    _title: h.title,
    _subtitle: h.summary || '蓝星钩子',
    _badge: 'Hook',
    _content: h.detail || h.summary || '',
  })),
)

// ============================================================
// Step 4 · 下卷预告（SushiCards ensemble）
// ============================================================

async function loadNextHints(directive?: string | null, baseIndex?: number | null) {
  if (!pid.value) return
  loadingNext.value = true
  try {
    const res = await settleApi.aiSuggest(pid.value, props.volNo, 'next_volume_hint', {
      directive: directive || null,
      base_index: baseIndex ?? null,
      entity_updates: adoptedEntityUpdates.value,
      cost: cost.value,
      blue_star_hook: pickedBlueStar.value,
    })
    const hints: SettleNextVolumeHint[] = res.hints || res.items || res.options || []
    nextHints.value = hints.map((h, i) => ({
      ...h,
      id: h.id || `next-${i}-${Date.now()}`,
    }))
    if (!pickedNextId.value && nextHints.value.length > 0) {
      pickedNextId.value = nextHints.value[0].id
    }
    // 预填下一卷标题
    if (!nextDungeonTitle.value && nextHints.value.length > 0) {
      nextDungeonTitle.value = nextHints.value.find((h) => h.id === pickedNextId.value)?.title || nextHints.value[0].title
    }
  } catch (e: any) {
    ElMessage.error(`下卷预告 AI 建议失败：${e?.message || e}`)
  } finally {
    loadingNext.value = false
  }
}

function onNextPick(id: string) {
  pickedNextId.value = id
  const h = nextHints.value.find((x) => x.id === id)
  if (h) nextDungeonTitle.value = h.title
}

function onNextRerun(idx: number | null, directive: string) {
  loadNextHints(directive, idx)
}

async function onNextCustom() {
  try {
    const { value: title } = await ElMessageBox.prompt('下一副本的名字/标题？', '自拟下卷预告', {
      inputPlaceholder: '例：深夜便利店·24小时不打烊',
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
    })
    const { value: entry } = await ElMessageBox.prompt('切入点：唐迟怎么被卷进来的？', '自拟下卷预告', {
      inputType: 'textarea',
      inputPlaceholder: '例：唐迟夜班值班，凌晨 3 点一个浑身湿透的客人买了一瓶水，付的钱是三年前停产的旧版钞票……',
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
    })
    const { value: gap } = await ElMessageBox.prompt('核心信息差：他开局不知道什么？', '自拟下卷预告', {
      inputType: 'textarea',
      inputPlaceholder: '例：这家便利店的"商品"根本不是货架上的东西，而客人也不全是活人……',
      confirmButtonText: '加入',
      cancelButtonText: '取消',
    })
    const newCard: SettleNextVolumeHint = {
      id: `next-custom-${Date.now()}`,
      title: title || '自拟下卷',
      entry_point: entry || '',
      info_gap: gap || '',
      summary: '作者自拟',
    }
    nextHints.value = [newCard, ...nextHints.value]
    pickedNextId.value = newCard.id
    nextDungeonTitle.value = newCard.title
    ElMessage.success('已加入自拟下卷预告')
  } catch {
    /* 取消 */
  }
}

async function onNextEdit(idx: number) {
  const h = nextHints.value[idx]
  if (!h) return
  try {
    const { value: entry } = await ElMessageBox.prompt('编辑切入点', '编辑下卷预告', {
      inputType: 'textarea',
      inputValue: h.entry_point || '',
      confirmButtonText: '保存',
      cancelButtonText: '取消',
    })
    nextHints.value[idx] = { ...h, entry_point: entry }
    if (pickedNextId.value === h.id) pickedNextId.value = nextHints.value[idx].id
  } catch {
    /* 取消 */
  }
}

const nextCards = computed(() =>
  nextHints.value.map((h) => ({
    ...h,
    _title: h.title,
    _subtitle: h.summary || h.info_gap || '',
    _badge: '下卷',
    _content:
      `【切入点】${h.entry_point || ''}\n\n` +
      `【信息差】${h.info_gap || ''}`,
  })),
)

// ============================================================
// 步骤切换
// ============================================================

function goBack() {
  if (step.value > 0) step.value -= 1
}

async function goNext() {
  // 校验
  if (step.value === 0) {
    const unpaid = foreshadows.value.filter(
      (f) => foreshadowDecisions.value[f.id]?.status === 'paid' && !(foreshadowDecisions.value[f.id].resolution?.trim()),
    )
    if (unpaid.length > 0) {
      ElMessage.warning(`有 ${unpaid.length} 条伏笔标记为"兑现"但未写明处理方式，请补充 resolution。`)
      return
    }
    // 进入实体检查
    step.value = 1
    if (entityChecks.value.length === 0) await loadEntityChecks()
    return
  }
  if (step.value === 1) {
    step.value = 2
    if (!cost.value) await loadCost()
    return
  }
  if (step.value === 2) {
    step.value = 3
    if (blueStarHooks.value.length === 0) await loadBlueStar()
    return
  }
  if (step.value === 3) {
    if (!pickedBlueStar.value) {
      ElMessage.warning('请先选中一个蓝星钩子')
      return
    }
    step.value = 4
    if (nextHints.value.length === 0) await loadNextHints()
    return
  }
  if (step.value === 4) {
    if (!pickedNext.value) {
      ElMessage.warning('请先选中一个下卷预告')
      return
    }
    step.value = 5
    return
  }
}

// ============================================================
// Step 5 · 落锤：apply + finalize
// ============================================================

async function doApply() {
  if (!pid.value) return
  applying.value = true
  try {
    // 后端 foreshadow_decisions 期望 list[{id, action:pay|drop|carry, note}]
    const fdList = Object.entries(foreshadowDecisions.value).map(([id, d]) => ({
      id,
      action: d.status, // paid/dropped/carried → 后端 pay/drop/carry
      note: d.resolution || '',
    })).map((x) => ({
      ...x,
      action: x.action === 'paid' ? 'pay' : x.action === 'dropped' ? 'drop' : 'carry',
    }))
    await settleApi.apply(pid.value, props.volNo, {
      contamination_delta: contaminationDelta.value,
      foreshadow_decisions: fdList as any,
      entity_updates: adoptedEntityUpdates.value,
      cost_summary: cost.value?.cost_summary || '',
      blue_star_hook: pickedBlueStar.value,
      next_volume_hint: pickedNext.value,
    } as any)
    ElMessage.success('杀青归账已写入')
  } catch (e: any) {
    ElMessage.error(`apply 失败：${e?.message || e}`)
  } finally {
    applying.value = false
  }
}

async function doFinalize() {
  if (!pid.value) return
  await doApply()
  finalizing.value = true
  try {
    const res = await settleApi.finalize(pid.value, props.volNo)
    finalized.value = true
    nextActionOptions.value = res.next_action_options || ['interlude', 'next_dungeon']
    ElMessage.success('卷已落锤！')
  } catch (e: any) {
    ElMessage.error(`finalize 失败：${e?.message || e}`)
  } finally {
    finalizing.value = false
  }
}

async function createInterlude() {
  if (!pid.value) return
  creatingNext.value = true
  try {
    const res = await settleApi.createInterlude(pid.value)
    ElMessage.success(`已创建蓝星间章 · 第 ${res.volume_no} 卷`)
    emit('done')
  } catch (e: any) {
    ElMessage.error(`创建间章失败：${e?.message || e}`)
  } finally {
    creatingNext.value = false
  }
}

async function createNextDungeon() {
  if (!pid.value) return
  const title = nextDungeonTitle.value.trim()
  if (!title) {
    ElMessage.warning('请填写下一卷标题')
    return
  }
  creatingNext.value = true
  try {
    const res = await settleApi.createNextDungeon(pid.value, title)
    ElMessage.success(`已创建下一副本 · 第 ${res.volume_no} 卷《${title}》`)
    emit('done')
  } catch (e: any) {
    ElMessage.error(`创建下一卷失败：${e?.message || e}`)
  } finally {
    creatingNext.value = false
  }
}

// ============================================================
// 汇总信息（落锤页）
// ============================================================

const summaryStats = computed(() => ({
  foreshadowsTotal: foreshadows.value.length,
  paid: Object.values(foreshadowDecisions.value).filter((d) => d.status === 'paid').length,
  dropped: Object.values(foreshadowDecisions.value).filter((d) => d.status === 'dropped').length,
  carried: Object.values(foreshadowDecisions.value).filter((d) => d.status === 'carried').length,
  entitiesAdopted: adoptedEntityUpdates.value.length,
  contaminationDelta: contaminationDelta.value,
  currentContam: overview.value?.current_contamination ?? 0,
  nextContam: (overview.value?.current_contamination ?? 0) + contaminationDelta.value,
  blueStarTitle: pickedBlueStar.value?.title || '（未选）',
  nextTitle: pickedNext.value?.title || '（未选）',
}))
</script>

<template>
  <div class="settle-view">
    <header class="settle-header">
      <h2>
        <span class="rune">⌬</span>
        杀青归账 · 第 {{ props.volNo }} 卷
        <span class="vol-sub" v-if="overview?.volume?.title">《{{ overview.volume.title }}》</span>
      </h2>
      <p class="settle-sub">七日将尽，死者收声——清点伏笔、结算代价、留下钩子，再决定唐迟下一次睁眼在哪里。</p>
      <div v-if="overview" class="overview-meta">
        <el-tag size="small" effect="dark">章节 {{ overview.chapters_written }} · 字数 {{ overview.total_words.toLocaleString() }}</el-tag>
        <el-tag size="small" type="danger" effect="dark">死亡节点 {{ overview.death_count }}</el-tag>
        <el-tag size="small" type="warning" effect="dark">污染 {{ overview.current_contamination }}</el-tag>
        <el-tag v-if="overview.is_mock" size="small" type="info" effect="plain">Mock 数据</el-tag>
      </div>
    </header>

    <el-steps :active="activeStep" finish-status="success" align-center class="settle-steps">
      <el-step v-for="(lbl, i) in stepLabels" :key="i" :title="lbl" />
    </el-steps>

    <!-- ============================================================ -->
    <!-- Step 0：伏笔清点 -->
    <!-- ============================================================ -->
    <div v-if="step === 0" class="settle-step">
      <h3>Step 1 · 伏笔清点</h3>
      <p class="hint">
        本卷一共埋了 <strong>{{ foreshadows.length }}</strong> 条伏笔。对每一条选择：<br>
        <el-tag size="small" type="success" effect="plain">兑现</el-tag> 本卷已经给出答案 ·
        <el-tag size="small" type="danger" effect="plain">丢弃</el-tag> 明确放弃/被真相抹掉 ·
        <el-tag size="small" type="info" effect="plain">顺延</el-tag> 带到下卷继续发酵
      </p>

      <el-table
        :data="foreshadows"
        v-loading="loading"
        stripe
        class="foreshadow-table"
      >
        <el-table-column prop="chapter_no" label="章节" width="70" align="center" />
        <el-table-column prop="planted_text" label="伏笔原文" min-width="280">
          <template #default="{ row }">
            <div class="planted-text">{{ row.planted_text }}</div>
          </template>
        </el-table-column>
        <el-table-column label="处理" width="260">
          <template #default="{ row }">
            <div class="fs-actions">
              <el-button
                size="small"
                :type="foreshadowDecisions[row.id]?.status === 'paid' ? 'success' : 'default'"
                @click="setForeshadow(row.id, 'paid')"
              >兑现</el-button>
              <el-button
                size="small"
                :type="foreshadowDecisions[row.id]?.status === 'dropped' ? 'danger' : 'default'"
                @click="setForeshadow(row.id, 'dropped')"
              >丢弃</el-button>
              <el-button
                size="small"
                :type="foreshadowDecisions[row.id]?.status === 'carried' ? 'info' : 'default'"
                @click="setForeshadow(row.id, 'carried')"
              >顺延</el-button>
            </div>
            <el-input
              v-if="foreshadowDecisions[row.id]?.status === 'paid'"
              v-model="foreshadowDecisions[row.id].resolution"
              size="small"
              type="textarea"
              :rows="2"
              placeholder="怎么兑现的？（一句话）"
              class="resolution-input"
            />
          </template>
        </el-table-column>
      </el-table>

      <div class="step-actions">
        <span class="step-hint">待处理 {{ unresolvedCount }} 条</span>
        <el-button type="primary" :disabled="unresolvedCount === foreshadows.length" @click="goNext">
          下一步：实体污染检查 →
        </el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 1：实体污染检查 -->
    <!-- ============================================================ -->
    <div v-else-if="step === 1" class="settle-step">
      <h3>Step 2 · 实体污染检查</h3>
      <p class="hint">
        AI 扫描本卷所有上场人物/可疑物件，给出状态变化与 SAN 冲击建议。<br>
        你可以采纳或忽略每条建议；采纳的条目将写入实体档案，并把负向 SAN 折算到污染增量上。
      </p>

      <el-button size="small" @click="loadEntityChecks" :loading="loading" class="reload-btn">
        ↻ 重新扫描
      </el-button>

      <el-table :data="entityChecks" v-loading="loading" stripe class="entity-table">
        <el-table-column label="采纳" width="70" align="center">
          <template #default="{ row }">
            <el-switch v-model="entityAdopted[row.entity_id]" />
          </template>
        </el-table-column>
        <el-table-column prop="name" label="实体" width="140" />
        <el-table-column prop="status_after" label="结局/状态" width="160" />
        <el-table-column label="SAN 冲击" width="140">
          <template #default="{ row }">
            <el-input-number
              v-model="entitySanEdits[row.entity_id]"
              :step="1"
              :min="-10"
              :max="3"
              size="small"
              :disabled="!entityAdopted[row.entity_id]"
            />
          </template>
        </el-table-column>
        <el-table-column label="备注">
          <template #default="{ row }">
            <el-input
              v-model="entityNoteEdits[row.entity_id]"
              size="small"
              :disabled="!entityAdopted[row.entity_id]"
              placeholder="一句话备注"
            />
          </template>
        </el-table-column>
      </el-table>

      <div class="step-actions">
        <el-button size="small" @click="goBack">← 返回伏笔清点</el-button>
        <span class="step-hint">将采纳 {{ adoptedEntityUpdates.length }} 条实体更新</span>
        <el-button type="primary" @click="goNext">下一步：代价核算 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 2：代价核算 -->
    <!-- ============================================================ -->
    <div v-else-if="step === 2" class="settle-step">
      <h3>Step 3 · 代价核算</h3>
      <p class="hint">活下来从来不是免费的。AI 汇总本卷的精神伤害、新获得的回响、KILL 加成，你可以手动微调最终污染增量。</p>

      <el-button size="small" @click="loadCost" :loading="loading" class="reload-btn">
        ↻ 重新核算
      </el-button>

      <div v-if="cost" class="cost-card">
        <div class="cost-section">
          <h4>◆ 代价总览</h4>
          <p class="cost-text">{{ cost.cost_summary }}</p>
        </div>
        <div class="cost-grid">
          <div class="cost-block">
            <label>SAN 损失说明</label>
            <p>{{ cost.san_loss_note || '—' }}</p>
          </div>
          <div class="cost-block" v-if="cost.new_echo">
            <label>新增回响</label>
            <p>{{ cost.new_echo }}</p>
          </div>
          <div class="cost-block" v-if="cost.kill_add">
            <label>KILL / 能力加成</label>
            <p>{{ cost.kill_add }}</p>
          </div>
        </div>
        <div class="cost-delta">
          <label>最终污染增量（可手动调整）</label>
          <el-input-number v-model="contaminationDelta" :min="0" :max="15" :step="1" size="large" />
          <span class="delta-hint">
            当前污染 <strong>{{ overview?.current_contamination ?? 0 }}</strong>
            → 新值 <strong class="warn">{{ (overview?.current_contamination ?? 0) + contaminationDelta }}</strong>
          </span>
        </div>
      </div>
      <el-empty v-else-if="!loading" description="点击「重新核算」生成代价总结" />

      <div class="step-actions">
        <el-button size="small" @click="goBack">← 返回实体检查</el-button>
        <el-button type="primary" :disabled="!cost" @click="goNext">下一步：蓝星钩子 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 3：蓝星钩子（SushiCards ensemble） -->
    <!-- ============================================================ -->
    <div v-else-if="step === 3" class="settle-step">
      <h3>Step 4 · 蓝星钩子</h3>
      <p class="hint">
        副本结束后回到蓝星，总有一些东西跟着唐迟一起回来——一个打不通的号码、一段不属于自己的记忆、镜子里多了半秒的眨眼。<br>
        AI 给出 3 个候选，你可以重抽、自拟、微调，最后挑一个钉进主角的蓝星日常。
      </p>

      <div v-if="loadingBlueStar" class="loading-hint">深海正在把一些东西吐回水面……</div>
      <SushiCards
        v-else-if="blueStarHooks.length > 0"
        mode="ensemble"
        :items="blueStarCards"
        :picked-id="pickedBlueStarId"
        axis-label="蓝星钩子候选"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :allow-custom="true"
        :allow-regenerate="true"
        :is-active="true"
        @pick="onBlueStarPick"
        @rerun="onBlueStarRerun"
        @custom-write="onBlueStarCustom"
        @edit-card="onBlueStarEdit"
      />

      <div v-else class="empty-tip">
        <el-button type="primary" @click="loadBlueStar()">生成蓝星钩子候选</el-button>
      </div>

      <div class="step-actions">
        <el-button size="small" @click="goBack">← 返回代价核算</el-button>
        <span class="step-hint" v-if="pickedBlueStar">当前选中：{{ pickedBlueStar.title }}</span>
        <el-button type="primary" :disabled="!pickedBlueStar" @click="goNext">下一步：下卷预告 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 4：下卷预告（SushiCards ensemble） -->
    <!-- ============================================================ -->
    <div v-else-if="step === 4" class="settle-step">
      <h3>Step 5 · 下卷预告</h3>
      <p class="hint">
        蓝星钩子像一根线，下一截副本要接在哪里？AI 给 3 个新副本的切入点与信息差，你挑一个方向，或者自己写。
      </p>

      <div v-if="loadingNext" class="loading-hint">命运的岔路口正在显形……</div>
      <SushiCards
        v-else-if="nextHints.length > 0"
        mode="ensemble"
        :items="nextCards"
        :picked-id="pickedNextId"
        axis-label="下一副本候选"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :allow-custom="true"
        :allow-regenerate="true"
        :is-active="true"
        @pick="onNextPick"
        @rerun="onNextRerun"
        @custom-write="onNextCustom"
        @edit-card="onNextEdit"
      />

      <div v-else class="empty-tip">
        <el-button type="primary" @click="loadNextHints()">生成下卷预告</el-button>
      </div>

      <div class="step-actions">
        <el-button size="small" @click="goBack">← 返回蓝星钩子</el-button>
        <span class="step-hint" v-if="pickedNext">当前选中：{{ pickedNext.title }}</span>
        <el-button type="primary" :disabled="!pickedNext" @click="goNext">下一步：落锤 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 5：落锤 -->
    <!-- ============================================================ -->
    <div v-else-if="step === 5" class="settle-step">
      <h3>Step 6 · 落锤</h3>
      <p class="hint">最后核对一遍所有决定——一旦落锤，本卷盖章、污染入库、钩子钉死、下一卷的门会打开。</p>

      <el-card shadow="never" class="hammer-card">
        <h4>◆ 归账总览</h4>
        <ul class="summary-list">
          <li>伏笔 <strong>{{ summaryStats.foreshadowsTotal }}</strong> 条：兑现 {{ summaryStats.paid }} · 丢弃 {{ summaryStats.dropped }} · 顺延 {{ summaryStats.carried }}</li>
          <li>实体状态更新 <strong>{{ summaryStats.entitiesAdopted }}</strong> 条</li>
          <li>污染 {{ summaryStats.currentContam }} → <strong class="warn">{{ summaryStats.nextContam }}</strong>（+{{ summaryStats.contaminationDelta }}）</li>
          <li>蓝星钩子：<em>{{ summaryStats.blueStarTitle }}</em></li>
          <li>下卷方向：<em>{{ summaryStats.nextTitle }}</em></li>
        </ul>
      </el-card>

      <div v-if="!finalized" class="hammer-actions">
        <el-button size="small" @click="goBack">← 返回下卷预告</el-button>
        <el-button
          type="primary"
          size="large"
          :loading="applying || finalizing"
          @click="doFinalize"
        >
          ⚒ 确认落锤
        </el-button>
      </div>

      <div v-else class="finalized-box">
        <el-result icon="success" title="本卷已落锤" sub-title="死者沉下去了，活着的人还得接着走。">
          <template #extra>
            <div class="next-actions">
              <p class="next-title">唐迟下一步要去哪里？</p>
              <div class="next-buttons">
                <el-button
                  type="warning"
                  size="large"
                  :disabled="creatingNext"
                  :loading="creatingNext"
                  @click="createInterlude"
                >
                  🌙 先写蓝星间章
                </el-button>
                <div class="next-dungeon">
                  <el-input
                    v-model="nextDungeonTitle"
                    size="large"
                    placeholder="下一副本标题"
                    class="next-title-input"
                  />
                  <el-button
                    type="primary"
                    size="large"
                    :disabled="creatingNext"
                    :loading="creatingNext"
                    @click="createNextDungeon"
                  >
                    ⚔ 直接开下一副本
                  </el-button>
                </div>
              </div>
            </div>
          </template>
        </el-result>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settle-view {
  padding: 20px 28px 40px;
  color: #e6e0d4;
  min-height: 100%;
  background: radial-gradient(ellipse at top, #2a241d 0%, #1e1a16 45%, #0f0c0a 100%);
}

.settle-header {
  margin-bottom: 18px;
  border-bottom: 1px solid rgba(196, 137, 78, 0.25);
  padding-bottom: 14px;
}
.settle-header h2 {
  margin: 0 0 6px;
  font-size: 22px;
  letter-spacing: 1px;
}
.settle-header .rune {
  color: #c4894e;
  margin-right: 8px;
}
.settle-header .vol-sub {
  font-size: 15px;
  color: #c4894e;
  font-weight: normal;
  margin-left: 6px;
}
.settle-sub {
  margin: 0 0 10px;
  color: #a89a84;
  font-size: 13px;
}
.overview-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.overview-meta :deep(.el-tag) {
  background: rgba(30, 26, 22, 0.6);
  border-color: rgba(196, 137, 78, 0.3);
}

.settle-steps {
  margin: 12px 0 24px;
}
.settle-steps :deep(.el-step__title) {
  color: #c9bfa8;
  font-size: 13px;
}
.settle-steps :deep(.el-step__title.is-process) {
  color: #c4894e;
}
.settle-steps :deep(.el-step__head.is-process) {
  color: #c4894e;
  border-color: #c4894e;
}
.settle-steps :deep(.el-step__head.is-finish) {
  color: #8c7b5e;
  border-color: #8c7b5e;
}

.settle-step {
  background: rgba(30, 26, 22, 0.55);
  border: 1px solid rgba(196, 137, 78, 0.15);
  border-radius: 8px;
  padding: 20px 22px;
}
.settle-step h3 {
  margin: 0 0 10px;
  color: #c4894e;
  font-size: 17px;
  letter-spacing: 1px;
}
.settle-step .hint {
  color: #a89a84;
  font-size: 13px;
  line-height: 1.7;
  margin: 0 0 16px;
}
.settle-step .hint strong {
  color: #e6d9b8;
}
.reload-btn {
  margin-bottom: 12px;
}
.loading-hint,
.empty-tip {
  text-align: center;
  color: #a89a84;
  padding: 28px 0;
  font-size: 13px;
}

/* 伏笔表格 */
.foreshadow-table,
.entity-table {
  background: rgba(15, 12, 10, 0.5);
  border-radius: 6px;
  overflow: hidden;
}
.foreshadow-table :deep(.el-table),
.entity-table :deep(.el-table) {
  background: transparent;
  color: #e6e0d4;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-border-color: rgba(196, 137, 78, 0.12);
  --el-table-header-bg-color: rgba(42, 36, 29, 0.9);
  --el-table-header-text-color: #c4894e;
  --el-table-row-hover-bg-color: rgba(196, 137, 78, 0.08);
}
.planted-text {
  font-size: 13px;
  line-height: 1.6;
  color: #d6ccb6;
}
.fs-actions {
  display: flex;
  gap: 6px;
  margin-bottom: 6px;
}
.resolution-input {
  margin-top: 4px;
}
.resolution-input :deep(.el-textarea__inner) {
  background: rgba(15, 12, 10, 0.8);
  color: #e6e0d4;
}

/* 代价卡 */
.cost-card {
  background: rgba(15, 12, 10, 0.6);
  border: 1px solid rgba(196, 137, 78, 0.2);
  border-radius: 6px;
  padding: 18px 20px;
}
.cost-section h4 {
  margin: 0 0 8px;
  color: #c4894e;
  font-size: 14px;
}
.cost-text {
  margin: 0 0 14px;
  color: #d6ccb6;
  line-height: 1.8;
  font-size: 13.5px;
}
.cost-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.cost-block {
  background: rgba(42, 36, 29, 0.6);
  border-left: 2px solid #c4894e;
  padding: 8px 12px;
  border-radius: 3px;
}
.cost-block label {
  display: block;
  color: #c4894e;
  font-size: 12px;
  margin-bottom: 4px;
}
.cost-block p {
  margin: 0;
  font-size: 13px;
  color: #d6ccb6;
  line-height: 1.6;
}
.cost-delta {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-top: 12px;
  border-top: 1px dashed rgba(196, 137, 78, 0.2);
}
.cost-delta label {
  color: #c4894e;
  font-size: 13px;
}
.delta-hint {
  color: #a89a84;
  font-size: 13px;
}
.delta-hint .warn {
  color: #d97a56;
  font-size: 15px;
}

/* 步骤底部操作条 */
.step-actions {
  margin-top: 22px;
  padding-top: 14px;
  border-top: 1px dashed rgba(196, 137, 78, 0.2);
  display: flex;
  align-items: center;
  gap: 12px;
  justify-content: flex-end;
}
.step-hint {
  color: #a89a84;
  font-size: 12.5px;
  margin-right: auto;
}

/* 落锤 */
.hammer-card {
  background: rgba(15, 12, 10, 0.7) !important;
  border: 1px solid rgba(196, 137, 78, 0.25) !important;
  color: #e6e0d4;
}
.hammer-card :deep(.el-card__body) {
  padding: 18px 22px;
}
.hammer-card h4 {
  margin: 0 0 10px;
  color: #c4894e;
}
.summary-list {
  margin: 0;
  padding-left: 18px;
  line-height: 2;
  color: #d6ccb6;
  font-size: 13.5px;
}
.summary-list em {
  color: #c4894e;
  font-style: normal;
}
.summary-list .warn {
  color: #d97a56;
}
.hammer-actions {
  margin-top: 20px;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
.finalized-box {
  margin-top: 18px;
  background: rgba(15, 12, 10, 0.7);
  border: 1px solid rgba(196, 137, 78, 0.2);
  border-radius: 8px;
}
.finalized-box :deep(.el-result__title) {
  color: #c4894e;
}
.finalized-box :deep(.el-result__subtitle) {
  color: #a89a84;
}
.next-actions {
  width: 100%;
  max-width: 560px;
  margin: 0 auto;
}
.next-title {
  color: #c9bfa8;
  margin: 0 0 14px;
}
.next-buttons {
  display: flex;
  flex-direction: column;
  gap: 12px;
  align-items: stretch;
}
.next-dungeon {
  display: flex;
  gap: 8px;
}
.next-title-input {
  flex: 1;
}
.next-title-input :deep(.el-input__wrapper) {
  background: rgba(15, 12, 10, 0.8);
}
</style>
