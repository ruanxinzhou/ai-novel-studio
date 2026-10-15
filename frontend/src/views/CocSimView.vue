<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import SushiCards from '../components/SushiCards.vue'
import { simulateApi } from '../api'
import type { Job } from '../api'
import { currentProjectId } from '../state'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

type UiState = 'loading' | 'ready' | 'running' | 'result' | 'completed'

const uiState = ref<UiState>('loading')
const volume = ref<any>(null)
const job = ref<Job | null>(null)
const running = ref(false)
const pickedVariantId = ref<string | null>(null)
// 本地追加的自定义/编辑变体（不来自 job 结果）
const extraVariants = ref<any[]>([])

// ---- 启动参数 ----
const userDirective = ref('')
const maxPairs = ref(6)
const maxTurns = ref(20)
const tangchiReplace = ref<number | null>(null)
const optionsOpen = ref(false)

// ---- 拼贴参数 ----
const collageBase = ref(0)
const SECTIONS = [
  { key: 'opening', label: '开场' },
  { key: 'death_1', label: '第一个死亡' },
  { key: 'death_2', label: '第二个死亡' },
  { key: 'climax', label: '高潮' },
  { key: 'ending', label: '结局' },
  { key: 'npc_fates', label: 'NPC 命运' },
]
const FROM_OPTIONS = [
  { value: 0, label: '全员殒落线' },
  { value: 1, label: '一人独活线' },
  { value: 2, label: '真相污染线' },
]
const collageSelections = ref<Record<string, number>>({})
const collageNotes = ref<Record<string, string>>({})
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

/** 包含自定义卡的全部可选项（自定义卡追加在尾部） */
const allVariants = computed<any[]>(() => {
  return [...jobVariants.value, ...extraVariants.value]
})

const completed = computed(() => volume.value?.simulated_run ?? null)

async function loadVolume() {
  const pid = currentProjectId.value
  if (!pid || !props.volNo) return
  try {
    const r = await simulateApi.getVolume(pid, props.volNo)
    volume.value = r.volume
    if (r.volume?.simulated_run) {
      uiState.value = 'completed'
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
      const mine = [...ev.jobs].reverse().find((j) => j.kind === 'coc_batch')
      if (mine) applyJob(mine)
    } else if (ev.type === 'job_idle') {
      // no-op
    }
  })
}

function applyJob(j: Job) {
  job.value = j
  if (j.status === 'done') {
    running.value = false
    extraVariants.value = []
    uiState.value = 'result'
    pickedVariantId.value = null
    // 默认拼贴 base = 0；selections 默认每段从 base
    if (Object.keys(collageSelections.value).length === 0) {
      collageBase.value = 0
      SECTIONS.forEach((s) => {
        collageSelections.value[s.key] = 0
      })
    }
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
      tangchi_replace_idx: tangchiReplace.value,
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

async function onPickOne(id: string | null, idx?: number) {
  if (id == null || idx == null) return
  pickedVariantId.value = id
  const pid = currentProjectId.value
  if (!pid) return
  const picked = allVariants.value[idx]
  if (!picked) return
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
  const selections = SECTIONS.map((s) => ({
    section: s.key,
    from_index: collageSelections.value[s.key] ?? collageBase.value,
    note: collageNotes.value[s.key] ?? '',
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
      <h2>🎲 COC 并行跑团仿真</h2>
      <p class="sub">
        3 场并行（全员殒落 / 一人独活 / 真相污染），挑一场作为主线或拼贴多场精华
      </p>
      <div v-if="completed" class="completed-banner">
        <span>✓ 已选定跑团走向：{{ completed.summary || completed.label || '（已完成）' }}</span>
        <el-button size="small" @click="onReset">重跑</el-button>
      </div>
    </div>

    <!-- 准备态 -->
    <div v-if="uiState === 'ready'" class="sim-ready">
      <el-card shadow="never">
        <h3>启动 3 场并行 COC 跑团</h3>
        <el-input
          v-model="userDirective"
          type="textarea"
          :rows="3"
          placeholder="可选：对本场跑团的特别指令，例如'多死人''让记者死在 Day3'..."
          class="directive-input"
        />
        <el-collapse v-model="optionsOpen" class="options-collapse">
          <el-collapse-item title="高级选项" name="opts">
            <div class="opt-row">
              <label>每场最大 KP/PL 对数：{{ maxPairs }}</label>
              <el-slider v-model="maxPairs" :min="4" :max="10" :step="1" />
            </div>
            <div class="opt-row">
              <label>每场最大回合数：{{ maxTurns }}（设为 1 可走测试 mock 路径，2-3 秒出结果）</label>
              <el-slider v-model="maxTurns" :min="1" :max="40" :step="1" />
            </div>
            <div class="opt-row">
              <label>唐迟替换哪张 PL 卡：</label>
              <el-radio-group v-model="tangchiReplace">
                <el-radio :value="null">不替换</el-radio>
                <el-radio :value="0">PL 1</el-radio>
                <el-radio :value="1">PL 2</el-radio>
                <el-radio :value="2">PL 3</el-radio>
              </el-radio-group>
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
        <div class="variant-cards-loading">
          <div class="var-loading-row">
            <div v-for="(label, i) in ['全员殒落线', '一人独活线', '真相污染线']" :key="i" class="var-loading-card">
              <div class="var-label">{{ label }}</div>
              <div class="var-one-liner">
                <template v-if="(job?.sub_results || []).length > i">
                  {{ (job?.sub_results as any[])[i]?.one_liner || '推演中…' }}
                </template>
                <template v-else>等待开场…</template>
              </div>
            </div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- 结果对比 + 拼贴（用 SushiCards ensemble）-->
    <div v-else-if="uiState === 'result'" class="sim-result">
      <SushiCards
        mode="ensemble"
        :items="allVariants"
        :picked-id="pickedVariantId"
        axis-label="3 场 COC 并行跑团结果"
        item-title-field="label"
        item-subtitle-field="one_liner"
        item-content-field="full_log"
        item-badge-field="flavor"
        :plate-count="3"
        :is-active="true"
        @pick="onPickOne"
        @rerun="onRerun"
        @custom-write="onCustomWrite"
        @edit-card="onEditCard"
      />

      <el-card shadow="never" class="collage-card">
        <h3>🧩 或：拼贴合成主跑团记录</h3>
        <p class="hint">
          拼贴模式：从 3 场 AI 版本中各挑你喜欢的元素，机械合并为一份主跑团记录（智能 AI 合成后续迭代）。自定义版本请用「单选」。
        </p>
        <div class="collage-row">
          <label>主线骨架：</label>
          <el-radio-group v-model="collageBase">
            <el-radio v-for="o in FROM_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</el-radio>
          </el-radio-group>
        </div>
        <div class="section-grid">
          <div v-for="s in SECTIONS" :key="s.key" class="section-row">
            <span class="section-label">{{ s.label }}</span>
            <el-select v-model="collageSelections[s.key]" :placeholder="`从${FROM_OPTIONS[collageBase].label}取`">
              <el-option v-for="o in FROM_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
            </el-select>
            <el-input
              v-model="collageNotes[s.key]"
              placeholder="备注（可选）"
              size="small"
              class="section-note"
            />
          </div>
        </div>
        <el-input
          v-model="collageUserNote"
          type="textarea"
          :rows="3"
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
        <el-scrollbar max-height="400" class="log-scroll">
          <pre class="full-log">{{ (completed?.full_log || '').slice(0, 1500) }}</pre>
        </el-scrollbar>
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
  padding: 16px 24px;
  max-width: 1200px;
  margin: 0 auto;
}

.sim-header h2 {
  margin: 0 0 4px 0;
  font-size: 22px;
}
.sim-header .sub {
  color: #666;
  margin: 0 0 12px 0;
  font-size: 13px;
}
.completed-banner {
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
  color: #67c23a;
  padding: 8px 12px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.directive-input {
  margin: 12px 0;
}
.options-collapse {
  margin: 8px 0;
}
.opt-row {
  padding: 8px 0;
}
.opt-row label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  color: #444;
}
.actions {
  margin-top: 16px;
  text-align: center;
}

.msg {
  margin: 12px 0;
  color: #555;
}

/* 跑中态：简化三卡（不带选定按钮） */
.variant-cards-loading {
  margin-top: 16px;
}
.var-loading-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}
.var-loading-card {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 12px;
  background: #fafafa;
  min-height: 100px;
}
.var-label {
  font-weight: 600;
  color: #409eff;
  margin-bottom: 8px;
}
.var-one-liner {
  font-size: 13px;
  color: #666;
  line-height: 1.5;
}

/* 拼贴区域 */
.collage-card {
  margin-top: 16px;
  background: #fafafa;
}
.collage-card h3 {
  margin: 0 0 4px 0;
}
.collage-card .hint {
  color: #888;
  font-size: 13px;
  margin: 0 0 16px 0;
}
.collage-row {
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.collage-row label {
  font-weight: 600;
}
.section-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 12px;
}
.section-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-label {
  width: 100px;
  font-size: 13px;
  color: #444;
}
.section-row .el-select {
  width: 140px;
}
.section-note {
  flex: 1;
}
.user-note {
  margin-top: 8px;
}

.completed-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.completed-head .summary {
  font-size: 14px;
  color: #333;
}
.log-scroll {
  margin: 12px 0;
}
.full-log {
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 13px;
  line-height: 1.6;
  background: #f6f8fa;
  padding: 12px;
  border-radius: 4px;
  margin: 0;
}

.loading {
  text-align: center;
  color: #888;
  padding: 40px;
}
</style>
