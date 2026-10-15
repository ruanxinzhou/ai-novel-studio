<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Loading, UploadFilled, Document } from '@element-plus/icons-vue'
import SushiCards from '../components/SushiCards.vue'
import { adaptApi } from '../api'
import type { Job } from '../api'
import { currentProjectId } from '../state'

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
let sse: { close: () => void } | null = null

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

async function startPdf5() {
  if (!file.value) { ElMessage.warning('请先选择 PDF 文件'); return }
  const pid = currentProjectId.value
  if (!pid || !realVolNo.value) return
  starting.value = true
  errorMsg.value = ''
  try {
    const r = await adaptApi.startPdf5(pid, realVolNo.value, file.value, userHint.value, nVal.value)
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

async function onPickAdapt(id: string | null, idx?: number) {
  if (id == null || idx == null) return
  pickedAdaptId.value = id
  if (!job.value) return
  const pid = currentProjectId.value
  if (!pid) return
  try {
    await adaptApi.pickAdapt(pid, realVolNo.value, job.value.job_id, idx)
    ElMessage.success('已选定改编版本')
    emit('done')
  } catch (e: any) {
    ElMessage.error('选版失败：' + (e?.message ?? e))
  }
}

function onNextStage() {
  emit('done')
}

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
      <div v-if="file" class="adapt-file-info">
        <el-icon><Document /></el-icon>
        <span>{{ file.name }}</span>
        <el-button size="small" text @click="file = null">移除</el-button>
      </div>

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
              <span v-for="(c, i) in item.changed_elements.slice(0, 3)" :key="i" class="meta-chip">{{ c }}</span>
            </div>
            <div v-if="item.player_cards?.length" class="meta-hint">角色卡 {{ item.player_cards.length }} 位</div>
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
        :allow-regenerate="false"
        :is-active="true"
        @pick="onPickAdapt"
      >
        <template #item="{ item }">
          <div v-if="item.changed_elements?.length" class="meta-line">
            <span v-for="(c, i) in item.changed_elements.slice(0, 3)" :key="i" class="meta-chip">{{ c }}</span>
          </div>
          <div v-if="item.player_cards?.length" class="meta-hint">角色卡 {{ item.player_cards.length }} 位</div>
        </template>
      </SushiCards>
    </div>

    <!-- selected -->
    <div v-else-if="uiState === 'selected'" class="adapt-selected">
      <el-alert type="success" :closable="false" show-icon title="改编版本已选定" />
      <div class="adapt-preview">
        <el-scrollbar max-height="320">
          <pre class="adapt-preview-text">{{ volume?.adapted_module }}</pre>
        </el-scrollbar>
      </div>
      <div class="adapt-actions">
        <el-button type="primary" size="large" @click="onNextStage">下一阶段：模拟跑团</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.module-adapt-view {
  max-width: 980px;
  margin: 0 auto;
  padding: 8px 4px 40px;
}
.adapt-header {
  margin-bottom: 20px;
}
.adapt-header h2 {
  margin: 0 0 6px;
  font-size: 22px;
  color: #303133;
}
.adapt-subtitle {
  margin: 0;
  font-size: 13px;
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
</style>
