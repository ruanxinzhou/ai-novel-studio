<script setup lang="ts">
// 回转寿司：N 份并排候选 → 选一份 → 进入编辑 → AI 改一版 / 手动改 / 锁定
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import type { Candidate, EditingState, SessionMessage } from '../api'

interface Props {
  cardKey: string
  cardLabel: string
  candidates: Candidate[]
  editing: EditingState | null
  messages: SessionMessage[]
  axisLabel?: string
  phase?: string
  isActive?: boolean
  lockedText?: string | null
  contextHint?: string
  plateCount?: number         // 这一转盘出几盘（副本核=5，其他=3）
}
const props = withDefaults(defineProps<Props>(), {
  axisLabel: '',
  phase: '',
  isActive: true,
  lockedText: null,
  contextHint: '',
  plateCount: 3,
})

const emit = defineEmits<{
  (e: 'generate', hint?: string): void
  (e: 'pick', candidateId: string | null): void
  (e: 'updateText', text: string): void
  (e: 'aiSuggest', instruction: string, apply: boolean): void
  (e: 'lock'): void
  (e: 'endEditing'): void
}>()

// 属于当前卡片的候选（按 created_at 取最近 N 条作为"当前一转盘"，N = plateCount）
const plateCandidates = computed(() => {
  const list = props.candidates.filter(c => c.card_key === props.cardKey)
  return list.slice(-props.plateCount)
})

// 是否在编辑当前卡
const editingThisCard = computed(() => props.editing?.card_key === props.cardKey)

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
  return props.messages
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
</script>

<template>
  <div class="sushi-card-block">
    <div class="sushi-title-row">
      <span class="sushi-label">{{ cardLabel }}</span>
      <el-tag v-if="lockedText" size="small" type="success">已锁定</el-tag>
      <el-tag v-else-if="editingThisCard" size="small" type="warning">编辑中（第 {{ editing?.edit_round ?? 0 }} 轮）</el-tag>
    </div>

    <!-- 已锁定：显示锁定文本 + 提示 -->
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
      <div class="sushi-cards" :class="{ 'sushi-cards-5': plateCount >= 5 }">
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
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px;
}
.sushi-cards.sushi-cards-5 {
  grid-template-columns: repeat(5, 1fr);
}
@media (max-width: 1280px) {
  .sushi-cards.sushi-cards-5 {
    grid-template-columns: repeat(3, 1fr);
  }
}
.sushi-cand {
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
.sushi-cand-text {
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
  flex: 1;
  max-height: 240px;
  overflow-y: auto;
}
.sushi-cand-rationale {
  font-size: 11px;
  color: #909399;
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #ebeef5;
  line-height: 1.5;
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
