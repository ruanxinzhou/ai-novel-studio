<script setup lang="ts">
// 回转寿司：N 份并排候选 → 选一份 → 进入编辑 → AI 改一版 / 手动改 / 锁定
// 支持两种模式：
//   - diverge（默认）：编辑流程（出 N 盘→选一盘→进入 AI 改稿→锁定），向后兼容 SessionView
//   - ensemble        ：通用 N 选 1 只读对比面板（pdf5/coc/outline5 等批量产出展示）
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Candidate, EditingState, SessionMessage } from '../api'

interface Props {
  // === 通用 ===
  cardKey?: string
  cardLabel?: string
  mode?: 'diverge' | 'ensemble'
  plateCount?: number
  isActive?: boolean
  axisLabel?: string
  phase?: string

  // === diverge 模式专属（保持不变）===
  candidates?: Candidate[]
  editing?: EditingState | null
  messages?: SessionMessage[]
  lockedText?: string | null
  contextHint?: string

  // === ensemble 模式专属 ===
  items?: Record<string, any>[]
  pickedId?: string | null
  itemTitleField?: string
  itemSubtitleField?: string
  itemContentField?: string
  itemBadgeField?: string
  showIndex?: boolean
  allowRegenerate?: boolean
  regenerateLabel?: string
  emptyLabel?: string
  // ensemble 迭代能力（T10+）
  allowCustom?: boolean
  customLabel?: string
  rerunLabel?: string
}
const props = withDefaults(defineProps<Props>(), {
  // 通用默认
  mode: 'diverge',
  cardKey: '',
  cardLabel: '',
  isActive: true,
  axisLabel: '',
  phase: '',
  plateCount: 3,
  // diverge 默认
  candidates: () => [],
  editing: null,
  messages: () => [],
  lockedText: null,
  contextHint: '',
  // ensemble 默认
  items: () => [],
  pickedId: null,
  itemTitleField: 'label',
  itemSubtitleField: 'one_liner',
  itemContentField: 'content',
  itemBadgeField: 'flavor',
  showIndex: true,
  allowRegenerate: true,
  regenerateLabel: '换一拨',
  emptyLabel: '暂无可选项',
  // ensemble 迭代默认
  allowCustom: true,
  customLabel: '✏️ 我自己写一版',
  rerunLabel: '🔄 再抽一版',
})

const emit = defineEmits<{
  // === 通用 ===
  (e: 'pick', id: string | null, index?: number): void
  (e: 'generate'): void

  // === diverge 专属（保持不变）===
  (e: 'updateText', text: string): void
  (e: 'aiSuggest', instruction: string, apply: boolean): void
  (e: 'lock'): void
  (e: 'endEditing'): void

  // === ensemble 迭代（T10+）===
  // index=null 整盘重抽；index=数字 基于该版微调
  (e: 'rerun', index: number | null, directive: string): void
  // 作者要自己写一版（父组件弹输入框）
  (e: 'custom-write'): void
  // 作者要就地编辑某一版文本
  (e: 'edit-card', index: number): void
}>()

// ==================== diverge 模式逻辑（保持原行为）====================

// 属于当前卡片的候选（按 created_at 取最近 N 条作为"当前一转盘"，N = plateCount）
const plateCandidates = computed(() => {
  if (props.mode !== 'diverge') return []
  const list = (props.candidates || []).filter(c => c.card_key === props.cardKey)
  return list.slice(-props.plateCount)
})

// 是否在编辑当前卡
const editingThisCard = computed(() => props.mode === 'diverge' && props.editing?.card_key === props.cardKey)

// 当前建议输入
const suggestInstr = ref('')
const suggesting = ref(false)
const localText = ref('')
let textTimer: number | null = null

// 同步 editing.current_text → localText（防抖手动保存）
watch(
  () => props.editing?.current_text,
  (v) => {
    if (editingThisCard.value && v != null) {
      localText.value = v
    }
  },
  { immediate: true },
)
watch(
  () => props.editing?.card_key,
  (k) => {
    if (k === props.cardKey && props.editing) localText.value = props.editing.current_text
  },
)

function onLocalTextInput(v: string) {
  localText.value = v
  if (textTimer) window.clearTimeout(textTimer)
  textTimer = window.setTimeout(() => {
    emit('updateText', localText.value)
  }, 500)
}

async function doSuggest(apply = true) {
  const t = suggestInstr.value.trim()
  // 不填也可以点，走"润色优化"默认指令
  suggesting.value = true
  try {
    emit('aiSuggest', t, apply)
    suggestInstr.value = ''
  } finally {
    suggesting.value = false
  }
}

// 取最近 N 条属于本卡的 suggestion 消息（供展示"AI 建议历史"）
const suggestionHistory = computed(() => {
  return (props.messages || [])
    .filter(m => m.kind === 'suggestion' && (m.meta as any)?.card_key === props.cardKey)
    .slice(-3)
})

function extractSuggestedText(m: SessionMessage): string {
  const meta = m.meta as any
  if (meta?.suggested_text) return String(meta.suggested_text)
  // 兜底：从 text 里「】」之后截取
  const idx = m.text.indexOf('】\n\n')
  return idx >= 0 ? m.text.slice(idx + 3) : m.text
}

function applySuggestion(m: SessionMessage) {
  const text = extractSuggestedText(m)
  localText.value = text
  emit('updateText', text)
  ElMessage.success('已采纳建议')
}

// ==================== ensemble 模式逻辑 ====================

const ensembleItems = computed(() => props.items || [])
const rerunDirective = ref('')

function doRerunAll() {
  emit('rerun', null, rerunDirective.value.trim())
  rerunDirective.value = ''
}
function doRerunOne(idx: number) {
  emit('rerun', idx, rerunDirective.value.trim())
  rerunDirective.value = ''
}

function getField(it: any, field: string): string {
  if (!it || !field) return ''
  const v = it[field]
  if (v == null) return ''
  return String(v)
}

function itemId(it: any, idx: number): string {
  if (!it) return String(idx)
  if (it.id != null) return String(it.id)
  if (it.vid != null) return String(it.vid)
  return String(idx)
}

function truncate(s: string, n: number) {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

// ==================== 共享：grid 列数计算 ====================

const sushiCardsGridClass = computed(() => {
  const n = props.mode === 'ensemble' ? ensembleItems.value.length : props.plateCount
  return {
    'sushi-cards-3': n <= 3,
    'sushi-cards-4': n === 4,
    'sushi-cards-5': n === 5,
    'sushi-cards-6plus': n >= 6,
  }
})
</script>

<template>
  <div class="sushi-card-block">
    <!-- diverge 模式的标题/标签（ensemble 模式不需要顶部 cardLabel；axis 标签在 plate 里统一展示） -->
    <div v-if="mode === 'diverge'" class="sushi-title-row">
      <span class="sushi-label">{{ cardLabel }}</span>
      <el-tag v-if="lockedText" size="small" type="success">已锁定</el-tag>
      <el-tag v-else-if="editingThisCard" size="small" type="warning">编辑中（第 {{ editing?.edit_round ?? 0 }} 轮）</el-tag>
    </div>

    <!-- ========== diverge：已锁定 ========== -->
    <template v-if="mode === 'diverge'">
      <div v-if="lockedText" class="sushi-locked">
        <div class="sushi-locked-text">{{ lockedText }}</div>
        <div class="sushi-hint" style="margin-top: 6px">已落锤。如需重开请回退阶段。</div>
      </div>

      <!-- 编辑态 -->
      <div v-else-if="editingThisCard" class="sushi-editing">
        <el-input
          :model-value="localText"
          @update:model-value="onLocalTextInput"
          type="textarea"
          :autosize="{ minRows: 6, maxRows: 14 }"
          :disabled="!isActive"
          placeholder="在此打磨文本，可手动改、可让 AI 改一版"
        />
        <div class="sushi-edit-instr">
          <el-input
            v-model="suggestInstr"
            size="small"
            placeholder="改稿方向（可不填，留空则让 AI 默认润色优化），回车直接改一版"
            :disabled="!isActive || suggesting"
            @keydown.enter.exact.prevent="doSuggest(true)"
          />
        </div>
        <div class="sushi-edit-actions">
          <el-button
            size="small"
            type="primary"
            :loading="suggesting"
            :disabled="!isActive"
            @click="doSuggest(true)"
          >AI 改一版（直接应用）</el-button>
          <el-button
            size="small"
            :loading="suggesting"
            :disabled="!isActive"
            @click="doSuggest(false)"
          >出建议先看看</el-button>
        </div>

        <!-- 建议历史：最近 3 条 -->
        <div v-if="suggestionHistory.length" class="sushi-suggest-history">
          <div class="sushi-hint">AI 建议：</div>
          <div v-for="m in suggestionHistory" :key="m.id" class="sushi-suggest-item">
            <div class="sushi-suggest-reason">{{ (m.meta as any)?.reason || '改稿建议' }}</div>
            <div class="sushi-suggest-preview">{{ extractSuggestedText(m).slice(0, 120) }}{{ extractSuggestedText(m).length > 120 ? '…' : '' }}</div>
            <div style="text-align: right; margin-top: 4px">
              <el-button size="small" type="primary" text @click="applySuggestion(m)">采纳这份</el-button>
            </div>
          </div>
        </div>

        <div class="sushi-edit-bottom">
          <el-button size="small" @click="emit('endEditing')" :disabled="!isActive">返回重选</el-button>
          <el-button size="small" type="success" @click="emit('lock')" :disabled="!isActive || !localText.trim()">
            就定这版（落锤）
          </el-button>
        </div>
      </div>

      <!-- 候选展示态：N 份并排 -->
      <div v-else-if="plateCandidates.length" class="sushi-plate">
        <div v-if="axisLabel" class="sushi-axis">
          <span class="sushi-axis-sep">◆</span>
          {{ axisLabel }}
          <span class="sushi-axis-sep">◆</span>
        </div>
        <div class="sushi-cards" :class="sushiCardsGridClass">
          <div
            v-for="c in plateCandidates"
            :key="c.id"
            class="sushi-cand"
          >
            <div class="sushi-cand-axis">{{ (c as any).twist || c.rationale.split(']')[0].replace('[','') || '候选' }}</div>
            <div class="sushi-cand-text">{{ c.content }}</div>
            <div v-if="c.rationale" class="sushi-cand-rationale">{{ c.rationale.replace(/^\[[^\]]{0,30}\]\s*/, '') }}</div>
            <div class="sushi-cand-pick">
              <el-button size="small" type="primary" :disabled="!isActive" @click="emit('pick', c.id)">
                选这盘
              </el-button>
            </div>
          </div>
        </div>
        <div class="sushi-plate-actions">
          <el-button size="small" :disabled="!isActive" @click="emit('generate')">换一拨</el-button>
          <el-button size="small" text :disabled="!isActive" @click="emit('pick', null)">从空白开始写</el-button>
        </div>
      </div>

      <!-- 未生成：触发按钮 -->
      <div v-else class="sushi-empty">
        <div v-if="axisLabel" class="sushi-axis-simple">{{ axisLabel }}</div>
        <el-button
          type="primary"
          size="small"
          :disabled="!isActive"
          @click="emit('generate')"
        >上 {{ plateCount }} 盘（AI 差异化生成）</el-button>
        <el-button size="small" text :disabled="!isActive" @click="emit('pick', null)">我自己空白写</el-button>
      </div>
    </template>

    <!-- ========== ensemble：通用 N 选 1 ========== -->
    <div v-else-if="mode === 'ensemble'" class="sushi-plate">
      <div v-if="axisLabel" class="sushi-axis">
        <span class="sushi-axis-sep">◆</span>{{ axisLabel }}<span class="sushi-axis-sep">◆</span>
      </div>

      <!-- 空态 -->
      <div v-if="!ensembleItems.length" class="sushi-empty">
        <div class="sushi-axis-simple">{{ emptyLabel }}</div>
        <div v-if="allowRegenerate && isActive" style="display: flex; gap: 6px; align-items: center; justify-content: center; flex-wrap: wrap; margin-bottom: 6px">
          <el-input
            v-model="rerunDirective"
            size="small"
            placeholder="可选：重抽提示词（方向/风格/雷点）"
            style="width: 260px"
            @keydown.enter.exact.prevent="doRerunAll"
          />
          <el-button type="primary" size="small" @click="doRerunAll">
            {{ rerunLabel }}
          </el-button>
        </div>
        <div v-if="allowCustom && isActive">
          <el-button size="small" @click="emit('custom-write')">{{ customLabel }}</el-button>
        </div>
      </div>

      <!-- N 张卡 -->
      <template v-else>
        <!-- 顶部工具栏：提示词 + 全部重抽 + 自己写 -->
        <div v-if="(allowRegenerate || allowCustom) && isActive" class="sushi-ensemble-toolbar">
          <el-input
            v-model="rerunDirective"
            size="small"
            placeholder="💡 重抽提示词（方向/风格/雷点），留空则随机重抽"
            class="sushi-ensemble-directive"
            @keydown.enter.exact.prevent="doRerunAll"
          />
          <el-button
            v-if="allowRegenerate"
            size="small"
            :disabled="!isActive"
            @click="doRerunAll"
          >{{ rerunLabel }}</el-button>
          <el-button
            v-if="allowCustom"
            size="small"
            plain
            :disabled="!isActive"
            @click="emit('custom-write')"
          >{{ customLabel }}</el-button>
        </div>

        <div class="sushi-cards" :class="sushiCardsGridClass">
          <div
            v-for="(it, idx) in ensembleItems"
            :key="itemId(it, idx)"
            class="sushi-cand"
            :class="{ 'sushi-cand--picked': pickedId === itemId(it, idx), 'sushi-cand--custom': it.is_custom }"
          >
            <div v-if="showIndex" class="sushi-cand-idx">#{{ idx + 1 }}</div>

            <!-- 每张卡右上角：🔄 基于此版微调 / ✏️ 编辑此版 -->
            <div v-if="isActive && !it.is_custom" class="sushi-cand-actions">
              <el-tooltip content="基于这版微调（换角度出新 3 版）" placement="top">
                <button class="sushi-cand-icon-btn" @click="doRerunOne(idx)" :disabled="!isActive">🔄</button>
              </el-tooltip>
              <el-tooltip content="编辑这版内容" placement="top">
                <button class="sushi-cand-icon-btn" @click="emit('edit-card', idx)" :disabled="!isActive">✏️</button>
              </el-tooltip>
            </div>
            <div v-else-if="it.is_custom" class="sushi-cand-actions">
              <el-tag size="small" type="warning" effect="plain">自编</el-tag>
            </div>

            <div v-if="getField(it, itemBadgeField)" class="sushi-cand-axis">{{ getField(it, itemBadgeField) }}</div>
            <div v-if="getField(it, itemTitleField)" class="sushi-cand-title">{{ getField(it, itemTitleField) }}</div>
            <div v-if="getField(it, itemSubtitleField)" class="sushi-cand-subtitle">{{ getField(it, itemSubtitleField) }}</div>
            <div
              v-if="getField(it, itemContentField)"
              class="sushi-cand-text sushi-cand-text--truncated"
            >{{ truncate(getField(it, itemContentField), 600) }}</div>
            <!-- 默认具名插槽：允许父组件传自定义详情（例如 Day 列表、meta chip）-->
            <div v-if="$slots.item" class="sushi-cand-extra">
              <slot name="item" :item="it" :index="idx" />
            </div>
            <div class="sushi-cand-pick">
              <el-button
                size="small"
                :type="pickedId === itemId(it, idx) ? 'success' : 'primary'"
                :disabled="!isActive"
                @click="emit('pick', itemId(it, idx), idx)"
              >
                {{ pickedId === itemId(it, idx) ? '已选定' : '选这盘' }}
              </el-button>
            </div>
          </div>
        </div>

        <div v-if="allowRegenerate" class="sushi-plate-actions sushi-plate-actions--center">
          <el-button size="small" :disabled="!isActive" @click="doRerunAll">{{ rerunLabel }}</el-button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.sushi-card-block {
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 12px;
  background: #fcfcfc;
}
.sushi-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 14px;
  font-weight: 600;
}
.sushi-label { color: #303133; }
.sushi-hint { font-size: 12px; color: #909399; }

.sushi-locked-text {
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
  background: #f0f9eb;
  border-left: 3px solid #67c23a;
  padding: 8px 10px;
  border-radius: 4px;
}

.sushi-axis {
  text-align: center;
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
  letter-spacing: 1px;
}
.sushi-axis-sep { color: #e6a23c; margin: 0 6px; }
.sushi-axis-simple {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}

.sushi-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
/* 按卡片数自适应列数：3→3列，4→4列，5→5列，6+→auto-fill 每卡最小 220px */
.sushi-cards.sushi-cards-3   { grid-template-columns: repeat(3, 1fr); }
.sushi-cards.sushi-cards-4   { grid-template-columns: repeat(4, 1fr); }
.sushi-cards.sushi-cards-5   { grid-template-columns: repeat(5, 1fr); }
.sushi-cards.sushi-cards-6plus { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
@media (max-width: 1440px) {
  .sushi-cards.sushi-cards-5 { grid-template-columns: repeat(3, 1fr); }
  .sushi-cards.sushi-cards-4 { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 900px) {
  .sushi-cards.sushi-cards-3,
  .sushi-cards.sushi-cards-4,
  .sushi-cards.sushi-cards-5 { grid-template-columns: 1fr; }
}

.sushi-cand {
  position: relative; /* 给 #idx 角标定位 */
  border: 1px solid #dcdfe6;
  border-radius: 6px;
  padding: 8px;
  background: white;
  display: flex;
  flex-direction: column;
  transition: border-color .2s, box-shadow .2s;
}
.sushi-cand:hover {
  border-color: #409eff;
  box-shadow: 0 2px 8px rgba(64,158,255,0.12);
}
.sushi-cand--picked {
  border-color: #67c23a;
  box-shadow: 0 0 0 2px rgba(103,194,58,0.18);
}
.sushi-cand--custom {
  border-color: #e6a23c;
  background: #fdf6ec;
}
.sushi-cand-idx {
  position: absolute;
  top: 6px;
  right: 8px;
  font-size: 11px;
  color: #c0c4cc;
  font-weight: 600;
}
.sushi-cand-actions {
  position: absolute;
  top: 6px;
  left: 8px;
  display: flex;
  gap: 4px;
  opacity: 0.45;
  transition: opacity .2s;
  z-index: 2;
}
.sushi-cand:hover .sushi-cand-actions {
  opacity: 1;
}
.sushi-cand-icon-btn {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 1px solid #dcdfe6;
  background: white;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: all .15s;
}
.sushi-cand-icon-btn:hover:not(:disabled) {
  border-color: #409eff;
  color: #409eff;
  transform: scale(1.08);
}
.sushi-cand-icon-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.sushi-ensemble-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.sushi-ensemble-directive {
  flex: 1;
  min-width: 240px;
}
.sushi-cand-axis {
  font-size: 12px;
  font-weight: 600;
  color: #e6a23c;
  margin-bottom: 6px;
  text-align: center;
  padding: 2px 0;
  background: #fdf6ec;
  border-radius: 4px;
}
.sushi-cand-title {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 4px;
}
.sushi-cand-subtitle {
  font-size: 12px;
  color: #e6a23c;
  margin-bottom: 6px;
  line-height: 1.5;
}
.sushi-cand-text {
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
  flex: 1;
  max-height: 240px;
  overflow-y: auto;
}
.sushi-cand-text--truncated {
  max-height: 160px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 7;
  -webkit-box-orient: vertical;
  /* 截断时用 overflow-y: hidden 避免滚动条 */
  overflow-y: hidden;
}
.sushi-cand-rationale {
  font-size: 11px;
  color: #909399;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #ebeef5;
  line-height: 1.5;
}
.sushi-cand-extra {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #ebeef5;
}
.sushi-cand-pick {
  margin-top: 8px;
  text-align: center;
}

.sushi-plate-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
}
.sushi-plate-actions--center {
  justify-content: center;
}

.sushi-empty {
  text-align: center;
  padding: 14px 0;
}
.sushi-empty .sushi-axis-simple { text-align: center; margin-bottom: 10px; }
.sushi-empty .el-button { margin: 0 4px; }

.sushi-editing {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sushi-edit-instr {
  margin: 8px 0 6px 0;
}
.sushi-edit-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.sushi-edit-bottom {
  display: flex;
  justify-content: space-between;
}
.sushi-suggest-history {
  border-top: 1px dashed #ebeef5;
  padding-top: 6px;
}
.sushi-suggest-item {
  background: #f4f4f5;
  border-radius: 4px;
  padding: 6px 8px;
  margin-top: 4px;
  font-size: 12px;
}
.sushi-suggest-reason {
  color: #409eff;
  font-weight: 600;
  margin-bottom: 2px;
}
.sushi-suggest-preview {
  color: #606266;
  line-height: 1.5;
  white-space: pre-wrap;
  max-height: 60px;
  overflow: hidden;
}
</style>
