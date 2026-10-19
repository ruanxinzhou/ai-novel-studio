<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, UploadFilled, Document, WarningFilled } from '@element-plus/icons-vue'
import SushiCards from '../components/SushiCards.vue'
import { adaptApi } from '../api'
import type { Job } from '../api'
import { currentProjectId } from '../state'
import { provideChatContext } from '../chatContext'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

type UiState = 'loading' | 'upload' | 'progress' | 'done-pick' | 'selected'

const uiState = ref<UiState>('loading')
const realVolNo = ref<number>(0)
const volume = ref<any>(null)
// adapted_module 兼容 dict / 字符串两种格式
const adaptedPreview = computed(() => {
  const am = volume.value?.adapted_module
  if (!am) return ''
  if (typeof am === 'string') return am
  if (typeof am === 'object') {
    const premise = am.adapted_premise || ''
    const cast: any[] = Array.isArray(am.cast) ? am.cast
      : (Array.isArray(am.player_cards) ? am.player_cards : [])
    const flavor = am.flavor_full || am.flavor || ''
    const title = am.title ? `《${am.title}》\n` : ''
    const lines: string[] = []
    if (title || flavor) lines.push(`${title}${flavor ? '【风格】' + flavor : ''}`)
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
const file = ref<File | null>(null)
const userHint = ref('')
const nOptions = [3, 5, 8]
const nVal = ref(5)
const job = ref<Job | null>(null)
// ensemble 模式直接用后端原始 adaptations，不再映射成 Candidate[]
const adaptations = ref<any[]>([])
const pickedAdaptId = ref<string | null>(null)
const errorMsg = ref('')
const starting = ref(false)
const unpicking = ref(false)
let sse: { close: () => void } | null = null

// ---- 自定义/编辑 dialog ----
const dialogVisible = ref(false)
const dialogMode = ref<'custom' | 'edit'>('custom')
const dialogText = ref('')      // adapted_premise 内容
const dialogFlavor = ref('')    // flavor/标签（可选）
const dialogEditIndex = ref<number>(-1)

const isEmptyVol = computed(() => props.volNo === 0)

function adaptationsFromJob(j: Job): any[] {
  if (!j) return []
  if (j.result && typeof j.result === 'object' && Array.isArray((j.result as any).adaptations)) {
    return (j.result as any).adaptations
  }
  if (Array.isArray(j.sub_results)) return j.sub_results
  return []
}

function applyJob(j: Job | null) {
  if (!j) return
  job.value = j
  if (j.status === 'done') {
    const adapts = adaptationsFromJob(j)
    adaptations.value = adapts
    if (adapts.length > 0) {
      uiState.value = 'done-pick'
    } else {
      uiState.value = 'upload'
    }
  } else if (j.status === 'error') {
    errorMsg.value = j.error || '改编失败'
    uiState.value = 'upload'
    ElMessage.error('改编任务失败：' + errorMsg.value)
  } else if (j.status === 'running' || j.status === 'pending') {
    uiState.value = 'progress'
    // 实时把已完成的 sub_results 也展示出来（ensemble 模式可直接显示）
    const subs = (j.sub_results || []).filter((r: any) => r && !r.error)
    if (subs.length) adaptations.value = subs
  }
}

function closeSse() {
  if (sse) { sse.close(); sse = null }
}

function startSse() {
  closeSse()
  const pid = currentProjectId.value
  if (!pid || !realVolNo.value) return
  sse = adaptApi.subscribeJobs(pid, realVolNo.value, (ev) => {
    if (ev.type === 'job_update' && ev.job) {
      applyJob(ev.job)
    } else if (ev.type === 'job_update_batch' && ev.jobs) {
      // 取最新的 pdf_adapt5 job
      const list = [...ev.jobs].reverse().find(j => j.kind === 'pdf_adapt5')
      if (list) applyJob(list)
    } else if (ev.type === 'job_idle') {
      // 无活动任务，刷新一下 jobs 看是否已 done
      fetchJobs()
    }
  })
}

async function fetchVolume() {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const r = await adaptApi.getVolume(pid, realVolNo.value)
    volume.value = r.volume
    if (r.volume?.adapted_module && r.volume?.stage_completed?.adapt) {
      uiState.value = 'selected'
      return
    }
    await fetchJobs()
  } catch (e: any) {
    ElMessage.error('获取卷信息失败：' + (e?.message ?? e))
    uiState.value = 'upload'
  }
}

async function fetchJobs() {
  const pid = currentProjectId.value
  if (!pid || !realVolNo.value) return
  try {
    const r = await adaptApi.listJobs(pid, realVolNo.value)
    const list = [...r.jobs].reverse()
    const latest = list.find(j => j.kind === 'pdf_adapt5')
    if (latest) {
      applyJob(latest)
      if (latest.status === 'pending' || latest.status === 'running') {
        startSse()
      }
    } else {
      uiState.value = 'upload'
    }
  } catch (e: any) {
    console.warn('[ModuleAdaptView] listJobs failed:', e)
    uiState.value = 'upload'
  }
}

async function ensureVolume() {
  // adapt_empty：先创建卷
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const r = await adaptApi.createVolume(pid, '')
    realVolNo.value = r.volume_no
    volume.value = r.volume
  } catch (e: any) {
    ElMessage.error('创建副本卷失败：' + (e?.message ?? e))
    uiState.value = 'upload'
    return
  }
}

function onFilePick(uploadFile: any) {
  const f = uploadFile?.raw || uploadFile
  if (!f) return
  if (!f.name?.toLowerCase().endsWith('.pdf')) {
    ElMessage.warning('请选择 PDF 文件')
    return
  }
  file.value = f
}

function onRemoveFile() {
  file.value = null
}

function formatSize(bytes?: number): string {
  if (!bytes || bytes < 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

/** 内部：启动 pdf5（允许传入额外 directive 追加到 userHint） */
async function _runPdf5(extraDirective = '') {
  if (!file.value) { ElMessage.warning('请先选择 PDF 文件'); return }
  const pid = currentProjectId.value
  if (!pid || !realVolNo.value) return
  starting.value = true
  errorMsg.value = ''
  const hint = extraDirective
    ? (userHint.value ? userHint.value + '\n' : '') + extraDirective
    : userHint.value
  try {
    const r = await adaptApi.startPdf5(pid, realVolNo.value, file.value, hint, nVal.value)
    job.value = r.job
    adaptations.value = []
    pickedAdaptId.value = null
    uiState.value = 'progress'
    startSse()
  } catch (e: any) {
    ElMessage.error('启动改编失败：' + (e?.message ?? e))
  } finally {
    starting.value = false
  }
}

async function startPdf5() {
  await _runPdf5('')
}

async function onPickAdapt(id: string | null, idx?: number) {
  if (id == null || idx == null) return
  pickedAdaptId.value = id
  const pid = currentProjectId.value
  if (!pid) return
  const picked = adaptations.value[idx]
  if (!picked) return
  // 自定义/编辑卡 → 走 save-custom
  if (picked.is_custom) {
    try {
      await adaptApi.saveCustomAdapt(pid, realVolNo.value, picked)
      ElMessage.success('已保存自定义改编版本')
      emit('done')
    } catch (e: any) {
      ElMessage.error('保存失败：' + (e?.message ?? e))
    }
    return
  }
  if (!job.value) return
  try {
    await adaptApi.pickAdapt(pid, realVolNo.value, job.value.job_id, idx)
    ElMessage.success('已选定改编版本')
    emit('done')
  } catch (e: any) {
    ElMessage.error('选版失败：' + (e?.message ?? e))
  }
}

// ---- SushiCards 迭代事件 ----
function onRerun(idx: number | null, directive: string) {
  if (!file.value) {
    ElMessage.warning('需要有原始 PDF 才能重抽。请在上方文件条选择一个 PDF。')
    if (uiState.value !== 'upload') {
      // 保留已生成的 adaptations，跳到上传页让用户选 PDF；选完再点「开始并行改编」会重抽
      uiState.value = 'upload'
    }
    return
  }
  const extra = directive
    ? (idx != null ? `参考第${idx + 1}版风格，调整方向：${directive}` : `重抽提示：${directive}`)
    : (idx != null ? `参考第${idx + 1}版风格重新生成一组差异化改编。` : '')
  _runPdf5(extra)
}

function onCustomWrite() {
  dialogMode.value = 'custom'
  dialogText.value = ''
  dialogFlavor.value = ''
  dialogEditIndex.value = -1
  dialogVisible.value = true
}

function onEditCard(idx: number) {
  const it = adaptations.value[idx]
  if (!it) return
  dialogMode.value = 'edit'
  dialogText.value = it.adapted_premise || it.content || ''
  dialogFlavor.value = it.flavor || ''
  dialogEditIndex.value = idx
  dialogVisible.value = true
}

async function onDialogSave() {
  const text = dialogText.value.trim()
  if (!text) { ElMessage.warning('改编内容不能为空'); return }
  const flavor = dialogFlavor.value.trim() || '自定义'
  const newCard: any = {
    is_custom: true,
    adapted_premise: text,
    flavor: dialogMode.value === 'edit' ? (dialogFlavor.value.trim() || '已编辑') : flavor,
    one_liner: text.slice(0, 80).replace(/\n/g, ' '),
    title: `✏️ ${dialogMode.value === 'edit' ? '已编辑' : '自定义'}改编`,
    changed_elements: [],
    player_cards: [],
  }
  if (dialogMode.value === 'edit' && dialogEditIndex.value >= 0) {
    // 就地替换原卡（保留 id 与其他字段）
    const base = adaptations.value[dialogEditIndex.value] || {}
    adaptations.value[dialogEditIndex.value] = { ...base, ...newCard }
  } else {
    adaptations.value = [...adaptations.value, newCard]
  }
  dialogVisible.value = false
  ElMessage.success(dialogMode.value === 'edit' ? '已更新该版，可点击「选这盘」保存' : '已添加自定义版本，可点击「选这盘」保存')
}

function onNextStage() {
  emit('done')
}

async function onUnpick() {
  try {
    await ElMessageBox.confirm(
      '撤销选版会清空改编副本核，并同时清掉后面的跑团/魂穿/详设/节奏产物（需要重做这些阶段）。确定撤销吗？',
      '撤销选版',
      { confirmButtonText: '确定撤销', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  const pid = currentProjectId.value
  if (!pid) return
  unpicking.value = true
  try {
    await adaptApi.unpickAdapt(pid, realVolNo.value)
    ElMessage.success('已撤销选版，回到改编选版页')
    // 重新拉取 volume 与 jobs，让 adaptations 还在（重抽/重选可用）
    volume.value = null
    await fetchVolume()
  } catch (e: any) {
    ElMessage.error('撤销失败：' + (e?.message ?? e))
  } finally {
    unpicking.value = false
  }
}

// 向悬浮窗 AI 注册当前页上下文（必须在 setup 同步阶段 provide）
provideChatContext(() => {
  const picked = adaptations.value.find((a: any) => a.id === pickedAdaptId.value) || null
  const others = adaptations.value
    .filter((a: any) => a.id !== pickedAdaptId.value)
    .map((a: any, i: number) => ({
      idx: i + 1,
      title: a?.title || a?.axis || `第${i + 1}版`,
      tone: a?.tone || a?.axis || '',
      summary: (a?.text || a?.summary || '').slice(0, 600),
    }))
  const focus = picked
    ? {
        title: picked?.title || picked?.axis || '选中版',
        tone: picked?.tone || picked?.axis || '',
        player_cards_count: Array.isArray(picked?.player_cards) ? picked.player_cards.length : 0,
        changed_elements_count: Array.isArray(picked?.changed_elements) ? picked.changed_elements.length : 0,
        full_text: picked?.text || picked?.summary || '',
        player_cards: picked?.player_cards || [],
        changed_elements: picked?.changed_elements || [],
      }
    : null
  return {
    phase: '📕 上传 COC 模组并改编',
    system_hint:
      '作者在比对 3-8 版 AI 并行改编方案。他可能在吐槽某一版的能力设计/基调/白描。'
      + '回答时先从 context.focus 或 context.other_adaptations 里定位他在说的是哪一版，给具体可落地的改写（含至少一段替换示范），'
      + '尤其要遵守铁律：「主角替换获得的能力必须长线复用，不能只针对单本剧本」——要给未来剧本还能用、能进化/反噬的能力方向。',
    context: {
      volume_no: realVolNo.value || props.volNo,
      ui_state: uiState.value,
      pdf: file.value ? { name: file.value.name, size: file.value.size } : null,
      user_hint: userHint.value || null,
      adaptations_count: adaptations.value.length,
      picked_idx: pickedAdaptId.value
        ? adaptations.value.findIndex((a: any) => a.id === pickedAdaptId.value) + 1
        : null,
      focus,
      other_adaptations: picked
        ? others
        : adaptations.value.map((a: any, i: number) => ({
            idx: i + 1,
            title: a?.title || a?.axis || `第${i + 1}版`,
            tone: a?.tone || a?.axis || '',
            summary: (a?.text || a?.summary || '').slice(0, 400),
          })),
    },
  }
})

onMounted(async () => {
  if (isEmptyVol.value) {
    await ensureVolume()
  } else {
    realVolNo.value = props.volNo
  }
  if (realVolNo.value) {
    await fetchVolume()
  } else {
    uiState.value = 'upload'
  }
})

onUnmounted(() => {
  closeSse()
})
</script>

<template>
  <div class="module-adapt-view">
    <div class="adapt-header">
      <h2>📕 上传 COC 模组并改编</h2>
      <p class="adapt-subtitle">上传官方/第三方 COC 模组 PDF，AI 会并行跑 5 版差异化改编，避开版权风险</p>
    </div>

    <!-- 常驻文件条：所有状态下都可见，用于看当前 PDF/重传 PDF -->
    <div class="adapt-file-bar">
      <el-upload
        accept=".pdf"
        :auto-upload="false"
        :show-file-list="false"
        :on-change="onFilePick"
        class="adapt-file-upload"
      >
        <el-button size="small" type="primary" plain :icon="UploadFilled">
          {{ file ? '重新选择 PDF' : '选择 PDF 文件' }}
        </el-button>
      </el-upload>
      <div v-if="file" class="adapt-file-info-inline">
        <el-icon><Document /></el-icon>
        <span class="adapt-file-name" :title="file.name">{{ file.name }}</span>
        <span class="adapt-file-size">({{ formatSize(file.size) }})</span>
        <el-button size="small" text type="danger" @click="onRemoveFile">移除</el-button>
      </div>
      <div v-else class="adapt-file-missing">
        <el-icon><WarningFilled /></el-icon>
        <span>未选择 PDF，请先选一个模组文件再开始或重抽</span>
      </div>
    </div>

    <!-- loading -->
    <div v-if="uiState === 'loading'" class="adapt-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载中…</span>
    </div>

    <!-- upload -->
    <div v-else-if="uiState === 'upload'" class="adapt-upload">
      <el-upload
        drag
        accept=".pdf"
        :auto-upload="false"
        :show-file-list="false"
        :on-change="onFilePick"
        class="adapt-upload-drag"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">把 PDF 拖到这里，<em>或点击选择</em></div>
        <template #tip>
          <div class="el-upload__tip">仅支持 .pdf 文件，最大 20MB</div>
        </template>
      </el-upload>

      <el-input
        v-model="userHint"
        type="textarea"
        :rows="2"
        class="adapt-hint"
        placeholder="可选：对改编方向的补充说明（例如想要更阴冷、想结合程序员 oncall 主题...）"
      />

      <div class="adapt-n">
        <span class="adapt-n-label">并行版数：</span>
        <el-radio-group v-model="nVal">
          <el-radio-button v-for="k in nOptions" :key="k" :label="k">{{ k }} 版</el-radio-button>
        </el-radio-group>
      </div>

      <div class="adapt-actions">
        <el-button type="primary" size="large" :disabled="!file" :loading="starting" @click="startPdf5">
          开始并行改编
        </el-button>
      </div>
    </div>

    <!-- progress -->
    <div v-else-if="uiState === 'progress'" class="adapt-progress">
      <el-progress
        :percentage="Math.round(job?.progress ?? 0)"
        :status="job?.status === 'error' ? 'exception' : undefined"
      />
      <p class="adapt-progress-msg">{{ job?.message || '排队中...' }}</p>

      <!-- 改编进行中：用 ensemble 模式实时展示已完成版本 -->
      <div v-if="adaptations.length" class="adapt-subs-loading">
        <p class="adapt-subs-title">已完成版本（实时）：</p>
        <SushiCards
          mode="ensemble"
          :items="adaptations"
          axis-label="已出炉版本（任务仍在进行，完成后可选定）"
          item-title-field="flavor"
          item-subtitle-field="one_liner"
          item-content-field="adapted_premise"
          item-badge-field="flavor"
          :allow-regenerate="false"
          :is-active="false"
        >
          <template #item="{ item }">
            <div v-if="item.changed_elements?.length" class="meta-line">
              <span v-for="(c, i) in item.changed_elements" :key="i" class="meta-chip">{{ c }}</span>
            </div>
            <div v-if="item.player_cards?.length" class="player-cards-block">
              <div class="player-cards-title">🎭 角色卡（{{ item.player_cards.length }} 位）</div>
              <div class="player-card" v-for="(pc, pi) in item.player_cards" :key="pi">
                <div class="pc-head">
                  <b class="pc-name">{{ pc.name || `调查员${pi + 1}` }}</b>
                  <el-tag v-if="pc.occupation" size="small" type="info">{{ pc.occupation }}</el-tag>
                  <el-tag v-if="pc.age" size="small">{{ pc.age }}岁</el-tag>
                  <span v-if="pc.hp_max || pc.san_max || pc.mp_max" class="pc-stats">
                    <template v-if="pc.hp_max">HP {{ pc.hp_max }}</template>
                    <template v-if="pc.san_max"> · SAN {{ pc.san_max }}</template>
                    <template v-if="pc.mp_max"> · MP {{ pc.mp_max }}</template>
                  </span>
                </div>
                <div v-if="pc.background || pc.bio || pc.concept || pc.trait || pc.personality" class="pc-bio">
                  {{ pc.background || pc.bio || pc.concept || pc.trait || pc.personality }}
                </div>
                <div v-if="pc.skills && Object.keys(pc.skills).length" class="pc-skills">
                  <span class="pc-label">技能：</span>
                  <span v-for="(v, k) in pc.skills" :key="String(k)" class="pc-skill">{{ k }} {{ v }}</span>
                </div>
                <div v-if="pc.obsession || pc.drive || pc.secret || pc.bond || pc.flaw" class="pc-bio">
                  <span v-if="pc.obsession"><b>执念：</b>{{ pc.obsession }} </span>
                  <span v-if="pc.drive"><b>驱动：</b>{{ pc.drive }} </span>
                  <span v-if="pc.secret"><b>秘密：</b>{{ pc.secret }} </span>
                  <span v-if="pc.bond"><b>羁绊：</b>{{ pc.bond }} </span>
                  <span v-if="pc.flaw"><b>缺陷：</b>{{ pc.flaw }}</span>
                </div>
              </div>
            </div>
          </template>
        </SushiCards>
      </div>
    </div>

    <!-- done-pick：N 版 ensemble SushiCards -->
    <div v-else-if="uiState === 'done-pick'" class="adapt-pick">
      <p class="adapt-pick-hint">改编完成！请从下面 {{ adaptations.length }} 版中挑选一版作为本卷副本核：</p>
      <SushiCards
        mode="ensemble"
        :items="adaptations"
        :picked-id="pickedAdaptId"
        :axis-label="`${nVal} 版差异化改编`"
        item-title-field="flavor"
        item-subtitle-field="one_liner"
        item-content-field="adapted_premise"
        item-badge-field="flavor"
        :is-active="true"
        @pick="onPickAdapt"
        @rerun="onRerun"
        @custom-write="onCustomWrite"
        @edit-card="onEditCard"
      >
        <template #item="{ item }">
          <div v-if="item.changed_elements?.length" class="meta-line">
            <span v-for="(c, i) in item.changed_elements" :key="i" class="meta-chip">{{ c }}</span>
          </div>
          <div v-if="item.player_cards?.length" class="player-cards-block">
            <div class="player-cards-title">🎭 角色卡（{{ item.player_cards.length }} 位）</div>
            <div class="player-card" v-for="(pc, pi) in item.player_cards" :key="pi">
              <div class="pc-head">
                <b class="pc-name">{{ pc.name || `调查员${pi + 1}` }}</b>
                <el-tag v-if="pc.occupation" size="small" type="info">{{ pc.occupation }}</el-tag>
                <el-tag v-if="pc.age" size="small">{{ pc.age }}岁</el-tag>
                <span v-if="pc.hp_max || pc.san_max || pc.mp_max" class="pc-stats">
                  <template v-if="pc.hp_max">HP {{ pc.hp_max }}</template>
                  <template v-if="pc.san_max"> · SAN {{ pc.san_max }}</template>
                  <template v-if="pc.mp_max"> · MP {{ pc.mp_max }}</template>
                </span>
              </div>
              <div v-if="pc.background || pc.bio || pc.concept || pc.trait || pc.personality" class="pc-bio">
                {{ pc.background || pc.bio || pc.concept || pc.trait || pc.personality }}
              </div>
              <div v-if="pc.skills && Object.keys(pc.skills).length" class="pc-skills">
                <span class="pc-label">技能：</span>
                <span v-for="(v, k) in pc.skills" :key="String(k)" class="pc-skill">{{ k }} {{ v }}</span>
              </div>
              <div v-if="pc.obsession || pc.drive || pc.secret || pc.bond || pc.flaw" class="pc-bio">
                <span v-if="pc.obsession"><b>执念：</b>{{ pc.obsession }} </span>
                <span v-if="pc.drive"><b>驱动：</b>{{ pc.drive }} </span>
                <span v-if="pc.secret"><b>秘密：</b>{{ pc.secret }} </span>
                <span v-if="pc.bond"><b>羁绊：</b>{{ pc.bond }} </span>
                <span v-if="pc.flaw"><b>缺陷：</b>{{ pc.flaw }}</span>
              </div>
            </div>
          </div>
        </template>
      </SushiCards>
    </div>

    <!-- selected -->
    <div v-else-if="uiState === 'selected'" class="adapt-selected">
      <el-alert type="success" :closable="false" show-icon title="改编版本已选定" />
      <div class="adapt-preview">
        <el-scrollbar max-height="320">
          <pre class="adapt-preview-text">{{ adaptedPreview }}</pre>
        </el-scrollbar>
      </div>
      <div class="adapt-actions">
        <el-button size="large" :loading="unpicking" @click="onUnpick">← 撤销选版·重新改编</el-button>
        <el-button type="primary" size="large" @click="onNextStage">下一阶段：模拟跑团</el-button>
      </div>
    </div>

    <!-- 自定义/编辑 dialog -->
    <el-dialog
      v-model="dialogVisible"
      :title="dialogMode === 'custom' ? '✏️ 自写改编版本' : '✏️ 编辑此版改编'"
      width="680px"
      :close-on-click-modal="false"
    >
      <el-input
        v-model="dialogFlavor"
        placeholder="可选：风格标签（如「民俗阴冷向」），留空则默认"
        class="mb-10"
        style="margin-bottom: 10px"
      />
      <el-input
        v-model="dialogText"
        type="textarea"
        :rows="14"
        placeholder="在此粘贴/撰写改编后的副本核前提（adapted_premise），会作为本卷的改编版本直接保存。"
      />
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="onDialogSave">
          {{ dialogMode === 'custom' ? '添加此版' : '保存修改' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.module-adapt-view {
  max-width: 1360px;
  margin: 0 auto;
  padding: 12px 16px 48px;
}
.adapt-header {
  margin-bottom: 20px;
}
.adapt-header h2 {
  margin: 0 0 6px;
  font-size: 24px;
  color: #303133;
}
.adapt-subtitle {
  margin: 0;
  font-size: 14px;
  color: #909399;
}
.adapt-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  padding: 80px 0;
  color: #909399;
}
.adapt-upload-drag {
  margin-bottom: 16px;
}

/* 常驻文件条 */
.adapt-file-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  background: #f4f8ff;
  border: 1px solid #d9ecff;
  border-radius: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.adapt-file-upload :deep(.el-upload) { display: inline-block; }
.adapt-file-info-inline {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #303133;
  flex: 1 1 auto;
  min-width: 0;
}
.adapt-file-name {
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 520px;
}
.adapt-file-size {
  color: #909399;
  font-size: 12px;
}
.adapt-file-missing {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #e6a23c;
}
.adapt-file-info {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f4f4f5;
  border-radius: 6px;
  margin-bottom: 16px;
  font-size: 14px;
  color: #606266;
}
.adapt-hint {
  margin-bottom: 16px;
}
.adapt-n {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}
.adapt-n-label {
  font-size: 14px;
  color: #606266;
}
.adapt-actions {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
.adapt-progress {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  border: 1px solid #ebeef5;
}
.adapt-progress-msg {
  margin-top: 10px;
  font-size: 14px;
  color: #606266;
  text-align: center;
}
.adapt-subs-loading {
  margin-top: 20px;
}
.adapt-subs-title {
  font-size: 13px;
  color: #909399;
  margin-bottom: 8px;
}
.adapt-pick {
  margin-top: 8px;
}
.adapt-pick-hint {
  font-size: 14px;
  color: #606266;
  margin: 8px 0 12px;
}
.adapt-selected {
  margin-top: 8px;
}
.adapt-preview {
  margin: 16px 0;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 12px;
  background: #fafcff;
}
.adapt-preview-text {
  margin: 0;
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.8;
  color: #303133;
}
.mb-10 { margin-bottom: 10px; }

/* ensemble item 插槽里的 meta chip */
.meta-line {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 4px;
}
.meta-chip {
  display: inline-block;
  font-size: 11px;
  color: #909399;
  background: #f4f4f5;
  border-radius: 3px;
  padding: 1px 6px;
}
.meta-hint {
  font-size: 11px;
  color: #909399;
}

/* 角色卡展示 */
.player-cards-block {
  margin-top: 6px;
  border-top: 1px dashed #ebeef5;
  padding-top: 6px;
}
.player-cards-title {
  font-size: 12px;
  font-weight: 600;
  color: #409eff;
  margin-bottom: 6px;
}
.player-card {
  background: #f9fafc;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 6px 8px;
  margin-bottom: 6px;
  font-size: 12px;
  line-height: 1.6;
}
.pc-head {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 3px;
}
.pc-name {
  font-size: 13px;
  color: #303133;
}
.pc-stats {
  font-size: 11px;
  color: #e6a23c;
  margin-left: auto;
  font-family: monospace;
}
.pc-bio {
  color: #606266;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  margin: 2px 0;
}
.pc-skills {
  margin-top: 2px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.pc-label {
  font-size: 11px;
  color: #909399;
}
.pc-skill {
  font-size: 11px;
  color: #606266;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 3px;
  padding: 0 5px;
}
</style>
