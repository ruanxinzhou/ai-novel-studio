<script setup lang="ts">
/**
 * PrevStepPanel ·「上一步产物」折叠面板
 *
 * 用在每个创作步骤的视图顶部，默认折叠，点开可以看到上一阶段产出的核心信息。
 * sections: [{ title, body (string|object), bodyType? ('text'|'json'|'kv') }]
 * - bodyType 默认 text（按 <pre> 原样展示长文本，自动换行）；
 * - kv 模式：body 为对象 { 标签: 值 }，按两列展示；
 * - json 模式：JSON.stringify(obj, null, 2) 展示。
 */
import { computed } from 'vue'

interface Section {
  title: string
  body: any
  bodyType?: 'text' | 'kv' | 'json'
}

const props = defineProps<{
  stepLabel: string            // 当前步骤名，如「COC 并行跑团仿真」
  prevStepLabel: string        // 上一步骤名，如「剧本改编」
  sections?: Section[]         // 上一步的结构化产物
  defaultOpen?: boolean
}>()

const hasContent = computed(() => {
  const secs = props.sections || []
  return secs.some(s => {
    if (s.body == null) return false
    if (typeof s.body === 'string') return s.body.trim().length > 0
    if (Array.isArray(s.body)) return s.body.length > 0
    if (typeof s.body === 'object') return Object.keys(s.body).length > 0
    return Boolean(s.body)
  })
})

function asText(v: any, type?: string): string {
  if (v == null) return '（空）'
  if (typeof v === 'string') return v || '（空）'
  if (type === 'json') return JSON.stringify(v, null, 2)
  if (type === 'kv' && typeof v === 'object') {
    return Object.entries(v)
      .filter(([, val]) => val != null && val !== '')
      .map(([k, val]) => `${k}：${typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val)}`)
      .join('\n')
  }
  return JSON.stringify(v, null, 2)
}

function kvEntries(v: any): Array<[string, any]> {
  if (!v || typeof v !== 'object') return []
  return Object.entries(v).filter(([, val]) => val != null && val !== '')
}
</script>

<template>
  <div v-if="hasContent" class="prev-step-panel">
    <el-collapse v-model="activeNames">
      <el-collapse-item name="prev" class="prev-item">
        <template #title>
          <span class="prev-title">
            <span class="prev-tag">上一步</span>
            <span class="prev-step-name">{{ prevStepLabel }}</span>
            <span class="prev-sep">·</span>
            <span class="prev-hint">点此展开查看上一步产出，作为当前步骤的参考</span>
          </span>
        </template>
        <div class="prev-body">
          <div v-if="!sections || sections.length === 0" class="prev-empty">（暂无内容）</div>
          <div v-for="(sec, i) in sections" :key="i" class="prev-section">
            <div v-if="sec.title" class="prev-section-title">{{ sec.title }}</div>
            <!-- kv 模式：两列键值对，过滤深层长文本字段 -->
            <div v-if="(sec.bodyType || 'text') === 'kv'" class="prev-kv">
              <div v-for="[k, val] in kvEntries(sec.body)" :key="k" class="prev-kv-row">
                <span class="prev-kv-key">{{ k }}</span>
                <span class="prev-kv-val">
                  <template v-if="val != null && typeof val === 'object'">
                    <span class="prev-obj-preview">{{ asText(val, 'json') }}</span>
                  </template>
                  <template v-else>{{ String(val) || '—' }}</template>
                </span>
              </div>
            </div>
            <!-- 文本/JSON 模式 -->
            <pre v-else class="prev-text">{{ asText(sec.body, sec.bodyType) }}</pre>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.prev-step-panel {
  margin-bottom: 16px;
}
.prev-item :deep(.el-collapse-item__header) {
  background: linear-gradient(90deg, #fdf6ec 0%, #fff 60%);
  border-left: 3px solid #e6a23c;
  padding-left: 12px;
  font-weight: 500;
}
.prev-item :deep(.el-collapse-item__wrap) {
  border-left: 3px solid #e6a23c;
  background: #fffdf7;
}
.prev-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.prev-tag {
  display: inline-block;
  padding: 1px 8px;
  background: #e6a23c;
  color: #fff;
  border-radius: 3px;
  font-size: 12px;
}
.prev-step-name {
  color: #303133;
  font-weight: 600;
}
.prev-sep {
  color: #c0c4cc;
}
.prev-hint {
  color: #909399;
  font-weight: normal;
}
.prev-body {
  padding: 4px 4px 12px;
}
.prev-empty {
  color: #c0c4cc;
  font-size: 13px;
  padding: 8px 0;
}
.prev-section + .prev-section {
  margin-top: 14px;
  border-top: 1px dashed #f0d9b5;
  padding-top: 12px;
}
.prev-section-title {
  font-weight: 600;
  color: #606266;
  font-size: 13px;
  margin-bottom: 8px;
}
.prev-text {
  margin: 0;
  padding: 10px 12px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  font-size: 12.5px;
  line-height: 1.7;
  color: #303133;
  max-height: 360px;
  overflow-y: auto;
}
.prev-kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 14px;
  padding: 8px 12px;
  background: #fafafa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  font-size: 13px;
}
.prev-kv-row {
  display: contents;
}
.prev-kv-key {
  color: #909399;
  white-space: nowrap;
}
.prev-kv-val {
  color: #303133;
  word-break: break-word;
}
.prev-obj-preview {
  display: block;
  font-family: "SFMono-Regular", Consolas, monospace;
  font-size: 12px;
  white-space: pre-wrap;
  color: #606266;
  max-height: 200px;
  overflow-y: auto;
}
</style>
