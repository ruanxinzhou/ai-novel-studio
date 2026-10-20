<script setup lang="ts">
import { computed } from 'vue'

interface ChapterItem {
  no: number
  title: string
  finalized?: boolean
  brief?: string
}
interface VolumeItem {
  no: number
  title: string
  icon?: string
  contamination?: number
  chapters?: ChapterItem[]
}

const props = defineProps<{
  volumes: VolumeItem[]
  activeVolumeNo?: number
  activeChapterNo?: number
}>()

const emit = defineEmits<{
  (e: 'selectVolume', no: number): void
  (e: 'selectChapter', vn: number, cn: number): void
  (e: 'addVolume'): void
  (e: 'deleteVolume', no: number): void
  (e: 'goSetup'): void
  (e: 'openDesk'): void
}>()

function onDeleteVolume(e: MouseEvent, no: number) {
  e.stopPropagation()
  emit('deleteVolume', no)
}

const totalVolumes = computed(() => props.volumes.length)
</script>

<template>
  <aside class="spine-nav">
    <div class="spine-header">书脊</div>

    <div class="spine-setup" @click="emit('goSetup')">
      <span class="spine-icon">📖</span>
      <span class="spine-label">书设定</span>
    </div>

    <div class="spine-setup" @click="emit('openDesk')">
      <span class="spine-icon">✎</span>
      <span class="spine-label">写作台</span>
    </div>

    <div class="spine-divider" v-if="totalVolumes > 0">
      <span>卷目</span>
    </div>

    <div class="spine-volumes">
      <div
        v-for="v in volumes"
        :key="v.no"
        class="spine-volume"
        :class="{ active: v.no === activeVolumeNo }"
        @click="emit('selectVolume', v.no)"
      >
        <div class="sv-row">
          <span class="sv-icon">{{ v.icon || '📘' }}</span>
          <span class="sv-title">第{{ v.no }}卷 {{ v.title }}</span>
          <button class="sv-del" title="删除此卷" @click="onDeleteVolume($event, v.no)">✕</button>
        </div>
        <div class="sv-chapters" v-if="v.chapters && v.chapters.length">
          <div
            v-for="c in v.chapters"
            :key="c.no"
            class="sv-chapter"
            :class="{ active: v.no === activeVolumeNo && c.no === activeChapterNo, finalized: c.finalized }"
            @click.stop="emit('selectChapter', v.no, c.no)"
          >
            <span class="sc-dot"></span>
            <span class="sc-title">{{ c.no }}. {{ c.title || '（未命名）' }}</span>
          </div>
        </div>
      </div>
    </div>

    <el-button class="spine-add" type="primary" plain size="small" @click="emit('addVolume')">
      ➕ 新副本
    </el-button>
  </aside>
</template>

<style scoped>
.spine-nav {
  width: 240px;
  flex-shrink: 0;
  background: #fafafa;
  border-right: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;
  padding: 12px 8px;
  overflow-y: auto;
}
.spine-header {
  font-size: 12px;
  color: #909399;
  padding: 0 8px 8px;
  letter-spacing: 2px;
  font-weight: 600;
}
.spine-setup {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  color: #303133;
}
.spine-setup:hover {
  background: #ecf5ff;
}
.spine-divider {
  font-size: 11px;
  color: #c0c4cc;
  padding: 12px 8px 4px;
  text-transform: uppercase;
  letter-spacing: 1px;
}
.spine-volumes {
  flex: 1;
  overflow-y: auto;
}
.spine-volume {
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
}
.spine-volume.active > .sv-row {
  background: #ecf5ff;
  color: #409eff;
  font-weight: 600;
}
.sv-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  font-size: 13px;
}
.sv-row:hover {
  background: #f0f2f5;
}
.sv-icon {
  font-size: 16px;
}
.sv-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.sv-del {
  flex-shrink: 0;
  background: transparent;
  border: none;
  color: #c0c4cc;
  font-size: 12px;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  opacity: 0;
  transition: opacity .15s, color .15s, background .15s;
}
.sv-row:hover .sv-del {
  opacity: 1;
}
.sv-del:hover {
  color: #f56c6c;
  background: #fef0f0;
}
.sv-chapters {
  padding-left: 28px;
}
.sv-chapter {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  color: #606266;
  cursor: pointer;
}
.sv-chapter:hover {
  background: #f0f2f5;
}
.sv-chapter.active {
  background: #ecf5ff;
  color: #409eff;
}
.sv-chapter.finalized .sc-title {
  color: #67c23a;
}
.sc-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #c0c4cc;
  flex-shrink: 0;
}
.sv-chapter.finalized .sc-dot {
  background: #67c23a;
}
.sc-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.spine-add {
  margin-top: 8px;
  width: 100%;
}
</style>
