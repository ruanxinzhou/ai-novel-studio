<script setup lang="ts">
import { computed } from 'vue'
import { Setting } from '@element-plus/icons-vue'
import {
  currentPhase,
  currentVolumeNo,
  currentChapterNo,
  currentIsLastChapter,
  drawerOpen,
} from '../state'

const props = defineProps<{
  bookTitle?: string
  /** 污染值 0-100，Task 11 接 protagonist API，这里先写死 */
  contamination?: number
}>()

const PHASE_LABELS: Record<string, string> = {
  setup: '开书设定',
  adapt: '剧本改编',
  adapt_empty: '新卷',
  simulate: '模拟跑团',
  embody: '魂穿设计',
  scaffold: '卷内详设',
  rhythm: '节奏骨架',
  briefing: '章前规划',
  desk: '日更',
  settle: '杀青',
  interlude: '蓝星间章',
}

const contaminationVal = computed(() => props.contamination ?? 12)
const phaseLabel = computed(() => (PHASE_LABELS[currentPhase.value] ?? currentPhase.value) || '—')
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
      <el-tag size="small" type="primary" effect="plain" round>{{ phaseLabel }}</el-tag>
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
  background: #fff;
  border-bottom: 1px solid #ebeef5;
  font-size: 13px;
  flex-shrink: 0;
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
  color: #303133;
}
.sb-loc {
  color: #606266;
}
.sb-contam-label {
  color: #909399;
  font-size: 12px;
}
.sb-contam-val {
  color: #606266;
  font-size: 12px;
  min-width: 32px;
  text-align: right;
}
</style>
