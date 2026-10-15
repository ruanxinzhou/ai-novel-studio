<script setup lang="ts">
// 后台长任务进度条：显示 kind 中文名 + 百分比进度条 + message + 取消按钮
// 无 job 时不渲染任何内容。
import { computed } from 'vue'
import { Close } from '@element-plus/icons-vue'
import { JOB_KIND_LABELS, type Job } from '../api'

interface Props {
  job: Job | null
  cancellable?: boolean
}
const props = withDefaults(defineProps<Props>(), {
  job: null,
  cancellable: true,
})

const emit = defineEmits<{
  (e: 'cancel', jobId: string): void
}>()

const visible = computed(() => props.job != null)
const kindLabel = computed(() => {
  if (!props.job) return ''
  return JOB_KIND_LABELS[props.job.kind] ?? props.job.kind
})
const progress = computed(() => {
  if (!props.job) return 0
  const p = Number(props.job.progress) || 0
  return Math.max(0, Math.min(100, p))
})
const statusLabel = computed(() => {
  if (!props.job) return ''
  switch (props.job.status) {
    case 'pending': return '排队中'
    case 'running': return '进行中'
    case 'done': return '已完成'
    case 'error': return '失败'
    case 'cancelled': return '已取消'
    default: return props.job.status
  }
})
const isWorking = computed(() =>
  props.job?.status === 'pending' || props.job?.status === 'running',
)
const isError = computed(() => props.job?.status === 'error')

function onCancel() {
  if (props.job) emit('cancel', props.job.job_id)
}
</script>

<template>
  <div v-if="visible" class="job-bar" :class="{ 'job-bar--error': isError }">
    <div class="job-bar-head">
      <span class="job-bar-kind">{{ kindLabel }}</span>
      <span class="job-bar-status">{{ statusLabel }} · {{ progress }}%</span>
      <el-button
        v-if="cancellable && isWorking"
        size="small"
        text
        :icon="Close"
        class="job-bar-cancel"
        @click="onCancel"
      >取消</el-button>
    </div>
    <div class="job-bar-track">
      <div class="job-bar-fill" :style="{ width: progress + '%' }" />
    </div>
    <div v-if="job?.message" class="job-bar-msg">{{ job.message }}</div>
    <div v-if="isError && job?.error" class="job-bar-err">{{ job.error }}</div>
  </div>
</template>

<style scoped>
.job-bar {
  background: #1d2027;
  border: 1px solid #2a2d35;
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  color: #e8e6df;
  font-size: 12px;
}
.job-bar--error {
  border-color: #c25050;
}
.job-bar-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.job-bar-kind {
  font-weight: 600;
  color: #d4a24b;
  font-size: 13px;
}
.job-bar-status {
  color: #8b8d93;
  font-size: 11px;
}
.job-bar-cancel {
  margin-left: auto;
  color: #c25050 !important;
  padding: 2px 4px;
}
.job-bar-track {
  width: 100%;
  height: 6px;
  background: #0d0e11;
  border-radius: 3px;
  overflow: hidden;
}
.job-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #d4a24b 0%, #e0b668 100%);
  border-radius: 3px;
  transition: width 0.3s ease;
}
.job-bar-msg {
  margin-top: 6px;
  color: #a9abaf;
  font-size: 11px;
  line-height: 1.5;
  word-break: break-word;
}
.job-bar-err {
  margin-top: 4px;
  color: #c25050;
  font-size: 11px;
  line-height: 1.5;
  word-break: break-word;
}
</style>
