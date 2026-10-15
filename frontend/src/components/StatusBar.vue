<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Setting } from '@element-plus/icons-vue'
import {
  currentPhase,
  currentProjectId,
  currentVolumeNo,
  currentChapterNo,
  currentIsLastChapter,
  drawerOpen,
} from '../state'
import { PHASE_TITLES, protagonistApi } from '../api'

const props = defineProps<{
  bookTitle?: string
  /** 污染值 0-100；若显式传非零值则优先使用，否则组件自动拉 protagonist */
  contamination?: number
  /** 外部强制刷新触发键 */
  refreshKey?: number
}>()

const autoContamination = ref(0)

async function refreshContamination() {
  const pid = currentProjectId.value
  if (!pid) {
    autoContamination.value = 0
    return
  }
  try {
    const res = await protagonistApi.get(pid)
    autoContamination.value = res.protagonist?.contamination_level ?? 0
  } catch (err: any) {
    // 新项目可能还没有 protagonist，404 时显示 0
    if (err?.status === 404 || err?.response?.status === 404) {
      autoContamination.value = 0
    } else {
      console.debug('[StatusBar] fetch contamination failed', err)
      autoContamination.value = 0
    }
  }
}

onMounted(refreshContamination)
watch(() => currentProjectId.value, refreshContamination)
watch(() => currentPhase.value, refreshContamination)
watch(() => currentChapterNo.value, refreshContamination)
watch(() => props.refreshKey, () => refreshContamination())

const contaminationVal = computed(() => {
  if (props.contamination && props.contamination > 0) return props.contamination
  return autoContamination.value
})

const phaseLabel = computed(() => (PHASE_TITLES[currentPhase.value] ?? currentPhase.value) || '—')
const locationText = computed(() => {
  if (!currentVolumeNo.value) return '—'
  const ch = currentChapterNo.value ? `· 第${currentChapterNo.value}章` : ''
  const last = currentIsLastChapter.value ? '（卷末）' : ''
  return `第${currentVolumeNo.value}卷${ch}${last}`
})

function openDrawer() {
  drawerOpen.value = true
}
</script>

<template>
  <div class="status-bar">
    <div class="sb-left">
      <span class="sb-title">{{ bookTitle || '未命名作品' }}</span>
      <el-divider direction="vertical" />
      <span class="sb-loc">{{ locationText }}</span>
    </div>
    <div class="sb-center">
      <el-tag size="small" effect="plain" round class="sb-phase-tag">{{ phaseLabel }}</el-tag>
    </div>
    <div class="sb-right">
      <span class="sb-contam-label">污染</span>
      <el-progress
        :percentage="contaminationVal"
        :stroke-width="8"
        :show-text="false"
        :color="contaminationVal > 60 ? '#f56c6c' : contaminationVal > 30 ? '#e6a23c' : '#67c23a'"
        style="width: 120px"
      />
      <span class="sb-contam-val">{{ contaminationVal }}%</span>
      <el-divider direction="vertical" />
      <el-button :icon="Setting" circle size="small" @click="openDrawer" title="设置" />
    </div>
  </div>
</template>

<style scoped>
.status-bar {
  height: 40px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #1a1a1a;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  font-size: 13px;
  flex-shrink: 0;
  color: #e4e7ed;
}
.sb-left,
.sb-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sb-center {
  flex: 1;
  display: flex;
  justify-content: center;
}
.sb-title {
  font-weight: 600;
  color: #e4e7ed;
}
.sb-loc {
  color: #909399;
}
.sb-contam-label {
  color: #909399;
  font-size: 12px;
}
.sb-contam-val {
  color: #e4e7ed;
  font-size: 12px;
  min-width: 32px;
  text-align: right;
}
.sb-phase-tag {
  color: #e4e7ed;
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.2);
}
/* 深色主题下分隔线颜色覆盖 */
:deep(.el-divider--vertical) {
  border-color: rgba(255, 255, 255, 0.1);
}
:deep(.el-button.is-circle) {
  color: #e4e7ed;
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.1);
}
:deep(.el-button.is-circle:hover) {
  background: rgba(255, 255, 255, 0.12);
  border-color: rgba(255, 255, 255, 0.2);
  color: #fff;
}
</style>
