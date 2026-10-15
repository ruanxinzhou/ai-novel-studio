<script setup lang="ts">
// @deprecated 旧 kindle 深色 UI（仅被 SessionView 使用），新代码请用 <SushiCards mode="ensemble">。
//             Task 14 删除 SessionView 时一并删除本文件。
// 通用 N 选 1 对比面板：卡片网格 + 选定按钮 + 选中高亮
// 支持三种内建渲染模式（pdf5 / coc / outline5），与 jobs.result 的数据结构对应。
import { computed, ref } from 'vue'
import { Check } from '@element-plus/icons-vue'
import type { CocVariant, Outline5Variant, Pdf5Variant } from '../api'

export type VariantKind = 'pdf5' | 'coc' | 'outline5'

interface Props {
  title?: string
  items: unknown[]
  kind: VariantKind
  /** 初始选中索引（-1 表示未选） */
  pickedIndex?: number
}
const props = withDefaults(defineProps<Props>(), {
  title: '',
  pickedIndex: -1,
})

const emit = defineEmits<{
  (e: 'pick', idx: number): void
}>()

const localPicked = ref(props.pickedIndex)
const expandedIdx = ref<Set<number>>(new Set())

function toggleExpand(i: number) {
  const s = new Set(expandedIdx.value)
  if (s.has(i)) s.delete(i); else s.add(i)
  expandedIdx.value = s
}
function isExpanded(i: number) { return expandedIdx.value.has(i) }

function doPick(i: number) {
  localPicked.value = i
  emit('pick', i)
}

const cols = computed(() => {
  if (props.kind === 'outline5') return 1
  if (props.kind === 'coc') return 3
  return 3 // pdf5
})
const gridStyle = computed(() => ({
  gridTemplateColumns: `repeat(${cols.value}, minmax(0, 1fr))`,
}))

// ---- 类型守卫 / 取字段帮助 ----
function asPdf(it: unknown): Pdf5Variant { return (it as Pdf5Variant) ?? {} }
function asCoc(it: unknown): CocVariant { return (it as CocVariant) ?? {} }
function asOutline(it: unknown): Outline5Variant { return (it as Outline5Variant) ?? {} }

function itemLabel(it: unknown, idx: number): string {
  if (props.kind === 'pdf5') return asPdf(it).title ?? `第 ${idx + 1} 版`
  if (props.kind === 'coc') return asCoc(it).label ?? `方案 ${idx + 1}`
  return asOutline(it).label ?? `大纲 ${idx + 1}`
}
function oneLiner(it: unknown): string {
  if (props.kind === 'pdf5') return asPdf(it).one_liner ?? asPdf(it).flavor_hook ?? ''
  if (props.kind === 'coc') return asCoc(it).one_liner ?? ''
  return asOutline(it).one_liner ?? ''
}
function premisePreview(it: unknown): string {
  const p = asPdf(it).premise ?? ''
  return p.length > 200 ? p.slice(0, 200) + '…' : p
}
function playerCardCount(it: unknown): number {
  return asPdf(it).player_cards?.length ?? 0
}
function changedElements(it: unknown): string[] {
  return (asPdf(it).changed_elements ?? []).slice(0, 3)
}

interface DayRow { day: number | string; summary: string; title?: string }

function dayBreakdown(it: unknown): DayRow[] {
  const raw = (props.kind === 'coc' ? asCoc(it).day_breakdown : asOutline(it).day_breakdown) ?? []
  return raw.map((d: any, idx: number) => ({
    day: d?.day ?? (idx + 1),
    summary: d?.summary ?? d?.title ?? '',
    title: d?.title,
  })).filter((d: DayRow) => d.summary)
}
function cocFinalSummary(it: unknown): string { return asCoc(it).final_summary ?? '' }
function outlineClimax(it: unknown): string { return asOutline(it).climax ?? '' }
function outlineTangchi(it: unknown): string { return asOutline(it).tangchi_entry ?? '' }
</script>

<template>
  <div class="variant-picker">
    <div v-if="title" class="vp-title">{{ title }}</div>
    <div v-if="!items?.length" class="vp-empty">暂无可选项</div>
    <div v-else class="vp-grid" :style="gridStyle">
      <div
        v-for="(it, i) in items"
        :key="i"
        class="vp-card"
        :class="{ 'vp-card--picked': localPicked === i }"
      >
        <div class="vp-card-head">
          <span class="vp-card-idx">#{{ i + 1 }}</span>
          <span class="vp-card-label">{{ itemLabel(it, i) }}</span>
          <el-button
            size="small"
            :type="localPicked === i ? 'success' : 'primary'"
            class="vp-pick-btn"
            :icon="Check"
            @click="doPick(i)"
          >
            {{ localPicked === i ? '已选定' : '选定' }}
          </el-button>
        </div>
        <div v-if="oneLiner(it)" class="vp-oneliner">{{ oneLiner(it) }}</div>
        <div class="vp-card-body">

          <!-- PDF 5版：premise 预览 + 角色卡数量 + 改动三条 -->
          <template v-if="kind === 'pdf5'">
            <div v-if="asPdf(it).flavor" class="vp-flavor">风格：{{ asPdf(it).flavor }}</div>
            <div v-if="premisePreview(it)" class="vp-preview-text">{{ premisePreview(it) }}</div>
            <div class="vp-meta-row">
              <span class="vp-meta-chip">角色卡 {{ playerCardCount(it) }} 位</span>
              <span v-if="(asPdf(it).changed_elements?.length ?? 0) > 0" class="vp-meta-chip">
                改动 {{ asPdf(it).changed_elements!.length }} 处
              </span>
            </div>
            <ul v-if="changedElements(it).length" class="vp-bullets">
              <li v-for="(c, ci) in changedElements(it)" :key="ci">{{ c }}</li>
            </ul>
            <div v-if="(asPdf(it).risk_flags?.length ?? 0) > 0" class="vp-risk">
              风险 {{ asPdf(it).risk_flags!.length }} 条
            </div>
          </template>

          <!-- COC 三场：默认折叠 Day breakdown，展开显示 Day1-Day7 -->
          <template v-else-if="kind === 'coc'">
            <div v-if="asCoc(it).premise" class="vp-preview-text" style="max-height: 3.2em; overflow: hidden">{{ asCoc(it).premise }}</div>
            <div class="vp-meta-row">
              <span class="vp-meta-chip" v-if="asCoc(it).final_san != null">SAN {{ asCoc(it).final_san }}</span>
              <span class="vp-meta-chip" v-if="asCoc(it).final_hp != null">HP {{ asCoc(it).final_hp }}</span>
              <span class="vp-meta-chip" v-if="asCoc(it).turns != null">{{ asCoc(it).turns }} 回合</span>
            </div>
            <div class="vp-day-summary">
              <div v-for="(d, di) in dayBreakdown(it).slice(0, isExpanded(i) ? undefined : 2)" :key="di" class="vp-day-row">
                <b>Day{{ d.day ?? di + 1 }}：</b><span>{{ d.summary ?? d.title ?? '' }}</span>
              </div>
            </div>
            <el-button
              v-if="dayBreakdown(it).length > 2"
              size="small"
              text
              class="vp-toggle"
              @click="toggleExpand(i)"
            >
              {{ isExpanded(i) ? '收起' : `展开全部 ${dayBreakdown(it).length} 天` }}
            </el-button>
            <div v-if="isExpanded(i) && cocFinalSummary(it)" class="vp-final">
              <b>结局：</b>{{ cocFinalSummary(it) }}
            </div>
          </template>

          <!-- 大纲 5版：纵向完整 day_breakdown + climax + tangchi_entry -->
          <template v-else-if="kind === 'outline5'">
            <div v-if="outlineTangchi(it)" class="vp-tangchi">
              <b>唐迟切入点：</b>{{ outlineTangchi(it) }}
            </div>
            <div class="vp-day-list">
              <div v-for="(d, di) in dayBreakdown(it)" :key="di" class="vp-day-row">
                <b>Day{{ d.day ?? di + 1 }}：</b><span>{{ d.summary ?? d.title ?? '' }}</span>
              </div>
            </div>
            <div v-if="outlineClimax(it)" class="vp-climax">
              <b>高潮：</b>{{ outlineClimax(it) }}
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.variant-picker {
  margin-top: 10px;
  padding: 10px 12px;
  background: #16181d;
  border: 1px solid #2a2d35;
  border-radius: 8px;
}
.vp-title {
  font-size: 13px;
  font-weight: 600;
  color: #d4a24b;
  margin-bottom: 8px;
}
.vp-empty {
  color: #8b8d93;
  font-size: 12px;
  text-align: center;
  padding: 16px 0;
}
.vp-grid {
  display: grid;
  gap: 10px;
}
.vp-card {
  background: #1d2027;
  border: 1px solid #2a2d35;
  border-radius: 6px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 220px;
  max-height: 420px;
  transition: border-color 0.2s;
}
.vp-card--picked {
  border-color: #d4a24b;
  box-shadow: 0 0 0 1px rgba(212,162,75,0.35);
}
.vp-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.vp-card-idx {
  font-size: 12px;
  color: #8b8d93;
  font-weight: 600;
}
.vp-card-label {
  font-size: 13px;
  font-weight: 600;
  color: #e8e6df;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vp-pick-btn {
  flex-shrink: 0;
}
.vp-oneliner {
  font-size: 12px;
  color: #d4a24b;
  margin-bottom: 6px;
  line-height: 1.5;
}
.vp-card-body {
  flex: 1;
  overflow-y: auto;
  font-size: 12px;
  color: #c0c2c7;
  line-height: 1.6;
}
.vp-flavor {
  font-size: 11px;
  color: #8b8d93;
  margin-bottom: 4px;
}
.vp-preview-text {
  color: #a9abaf;
  margin-bottom: 6px;
  white-space: pre-wrap;
}
.vp-meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}
.vp-meta-chip {
  display: inline-block;
  font-size: 11px;
  background: #0d0e11;
  border: 1px solid #2a2d35;
  color: #d4a24b;
  padding: 2px 6px;
  border-radius: 3px;
}
.vp-bullets {
  margin: 4px 0 0 18px;
  padding: 0;
  font-size: 11px;
  color: #a9abaf;
}
.vp-bullets li { margin-bottom: 2px; }
.vp-risk {
  margin-top: 6px;
  font-size: 11px;
  color: #c25050;
}
.vp-day-row {
  font-size: 12px;
  line-height: 1.6;
  color: #c0c2c7;
}
.vp-day-row b { color: #d4a24b; font-weight: 600; }
.vp-toggle {
  margin-top: 4px;
  padding: 0;
  color: #d4a24b !important;
  font-size: 11px;
}
.vp-final {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #2a2d35;
  font-size: 11px;
  color: #a9abaf;
}
.vp-final b { color: #d4a24b; }
.vp-tangchi {
  background: #0d0e11;
  border-left: 2px solid #d4a24b;
  padding: 6px 8px;
  margin-bottom: 8px;
  font-size: 12px;
  color: #e8e6df;
}
.vp-tangchi b { color: #d4a24b; }
.vp-day-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.vp-climax {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed #2a2d35;
  font-size: 12px;
  color: #e8e6df;
}
.vp-climax b { color: #c25050; }
</style>
