<script setup lang="ts">
// 章前 5 分钟方向卡 Modal：3 版 AI 候选 + 🔄再抽（带提示词）/ ✏️自编 / 📝基于选中版微调
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import SushiCards from './SushiCards.vue'
import { briefApi, type BriefOption } from '../api'

const props = defineProps<{
  pid: string
  volNo: number
  chapterNo: number
  visible: boolean
  /** 已有 brief.selected（重新打开时显示已选） */
  existingBrief?: any | null
  /** 上一章章尾（500字，可选） */
  prevTail?: string
  /** 到期伏笔（可选） */
  dueForeshadows?: string[]
}>()

const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  // 选中方向后触发
  (e: 'selected', brief: any): void
}>()

// ---- 状态 ----
const loading = ref(false)
const options = ref<BriefOption[]>([])
const day = ref(1)
/** story_day_override：null=按蓝图；0=跨日；1~7=指定天 */
const storyDayOverride = ref<number | null>(null)
const crossDay = ref(false)
const wordsEstimate = ref(6500)
const pickedIndex = ref<number | null>(null)
const pickedId = ref<string | null>(null)
const extraEdits = ref('')

const dayDisplay = computed(() => {
  if (crossDay.value || storyDayOverride.value === 0) return '跨日'
  return `第 ${day.value} 天`
})

// 自定义编写弹层
const showCustomWrite = ref(false)
const customText = ref('')

// 就地编辑
const editingIndex = ref<number | null>(null)
const editingText = ref('')
const editingVisible = computed({
  get: () => editingIndex.value !== null,
  set: (v: boolean) => { if (!v) editingIndex.value = null },
})

const BEAT_LABELS: Record<string, string> = {
  hook: '钩子章',
  sell: '卖设定',
  engine: '推进',
  climax: '高潮',
  cooldown: '余韵',
}

const beatLabel = computed(() => {
  const opt = options.value[pickedIndex.value ?? -1]
  return BEAT_LABELS[opt?.beat_type || ''] || '—'
})

// item 适配 SushiCards
const cardItems = computed(() =>
  options.value.map((o) => ({
    ...o,
    _badge: o.is_custom ? '自编' : (o.beat_type ? BEAT_LABELS[o.beat_type] || o.beat_type : ''),
  })),
)

function close() {
  emit('update:visible', false)
}

// ---- 加载选项 ----
async function loadOptions(opts: { directive?: string; base_index?: number | null; custom_writing?: string | null; story_day_override?: number | null } = {}) {
  if (!props.pid || !props.chapterNo) return
  loading.value = true
  try {
    const payload: any = { ...opts }
    if (storyDayOverride.value != null) payload.story_day_override = storyDayOverride.value
    const r = await briefApi.options(props.pid, props.volNo, props.chapterNo, payload)
    options.value = r.options || []
    day.value = r.day || 1
    crossDay.value = !!r.cross_day
    if (r.story_day_override != null) storyDayOverride.value = r.story_day_override
    wordsEstimate.value = r.chapter_words_estimate || 6500
    // 选中默认重置（如果是重抽则保留原选中索引的基础语义但清空选中）
    if (!opts.base_index && !opts.custom_writing) {
      pickedIndex.value = null
      pickedId.value = null
      extraEdits.value = ''
    }
  } catch (e: any) {
    ElMessage.error(`加载方向卡失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

function onStoryDayChange(v: number | 'auto') {
  if (v === 'auto') {
    storyDayOverride.value = null
    crossDay.value = false
  } else if (v === 0) {
    storyDayOverride.value = 0
    crossDay.value = true
  } else {
    storyDayOverride.value = v as number
    crossDay.value = false
  }
  loadOptions({})
}

// 打开时自动加载；如果已有 selected 则构造一个 selected 卡
watch(
  () => props.visible,
  (v) => {
    if (!v) return
    pickedIndex.value = null
    pickedId.value = null
    extraEdits.value = ''
    customText.value = ''
    showCustomWrite.value = false
    editingIndex.value = null
    if (props.existingBrief?.selected) {
      // 重新打开：以已选卡作为唯一选项（允许重抽换方向）
      const sel = props.existingBrief.selected
      options.value = [{
        id: sel.id || 'prev',
        title: sel.title || '已选方向',
        summary: sel.summary || '',
        hooks: sel.hooks || '',
        day: sel.day || props.existingBrief.day || 1,
        beat_type: sel.beat_type || 'engine',
        foreshadow_advance: sel.foreshadow_advance || [],
        entities_on_stage: sel.entities_on_stage || [],
        is_custom: !!sel.is_custom,
      }]
      pickedIndex.value = 0
      pickedId.value = options.value[0].id
      day.value = sel.day || props.existingBrief.day || 1
      const prevOverride = props.existingBrief.story_day_override
      if (typeof prevOverride === 'number' && 0 <= prevOverride && prevOverride <= 7) {
        storyDayOverride.value = prevOverride
        crossDay.value = prevOverride === 0
      } else {
        storyDayOverride.value = null
        crossDay.value = false
      }
      extraEdits.value = props.existingBrief.extra_edits || ''
    } else {
      storyDayOverride.value = null
      crossDay.value = false
      loadOptions()
    }
  },
)

// ---- 选中某卡 ----
function onPick(id: string | null, index?: number) {
  if (id == null || index == null) {
    pickedIndex.value = null
    pickedId.value = null
    return
  }
  pickedId.value = id
  pickedIndex.value = index
}

// ---- SushiCards 事件 ----
function onRerun(index: number | null, directive: string) {
  loadOptions({
    directive: directive || undefined,
    base_index: index,
  })
}

function onCustomWrite() {
  customText.value = ''
  showCustomWrite.value = true
}

async function submitCustomWrite() {
  const txt = customText.value.trim()
  if (!txt) {
    ElMessage.warning('先写点内容再提交')
    return
  }
  showCustomWrite.value = false
  await loadOptions({ custom_writing: txt })
  // 自编模式返回 1 条，默认选中
  pickedIndex.value = 0
  pickedId.value = options.value[0]?.id || null
  // 用户点"就用我的"语义就是"直接用这版开写"，不要让用户再多点一次底部"就按这版开写"
  // 直接调用 confirmSelect 把选中的自编方向持久化到 brief.selected
  await confirmSelect()
}

function onEditCard(index: number) {
  const it = options.value[index]
  if (!it) return
  editingIndex.value = index
  editingText.value = it.summary || ''
}

function applyEdit() {
  const idx = editingIndex.value
  if (idx == null) return
  const txt = editingText.value.trim()
  if (!txt) {
    ElMessage.warning('内容不能为空')
    return
  }
  options.value[idx] = { ...options.value[idx], summary: txt }
  editingIndex.value = null
  editingText.value = ''
}

// ---- 确认开写 ----
async function confirmSelect() {
  if (pickedIndex.value == null) {
    ElMessage.warning('先选一个方向')
    return
  }
  const opt = options.value[pickedIndex.value]
  if (!opt) return
  loading.value = true
  try {
    const body: any = {
      option_index: pickedIndex.value,
      extra_edits: extraEdits.value.trim() || undefined,
    }
    if (storyDayOverride.value != null) body.story_day_override = storyDayOverride.value
    if (opt.is_custom) {
      body.custom_text = opt.summary
    }
    const r = await briefApi.select(props.pid, props.volNo, props.chapterNo, body)
    ElMessage.success('方向已定，开写！')
    emit('selected', r.brief)
    close()
  } catch (e: any) {
    ElMessage.error(`选版失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    :title="`章前 5 分钟 · 第 ${chapterNo} 章方向卡`"
    width="980px"
    top="5vh"
    class="brief-modal"
    destroy-on-close
  >
    <!-- 信息条 -->
    <div class="brief-info-bar">
      <div class="brief-info-meta">
        <el-tag type="primary" effect="dark" size="small">第 {{ chapterNo }} 章</el-tag>
        <el-select
          :model-value="storyDayOverride == null ? 'auto' : storyDayOverride"
          size="small"
          class="brief-day-select"
          placeholder="按节奏蓝图"
          @change="onStoryDayChange"
        >
          <el-option label="按节奏蓝图" value="auto" />
          <el-option v-for="d in 7" :key="d" :label="`Day ${d}（第${d}天）`" :value="d" />
          <el-option label="跨日（多日聚合）" :value="0" />
        </el-select>
        <el-tag :type="crossDay ? 'danger' : 'warning'" effect="plain" size="small">{{ dayDisplay }}</el-tag>
        <el-tag type="success" effect="plain" size="small">{{ beatLabel }}</el-tag>
        <el-tag type="info" effect="plain" size="small">目标 ~{{ wordsEstimate.toLocaleString() }} 字</el-tag>
      </div>
      <div v-if="dueForeshadows && dueForeshadows.length" class="brief-foreshadows">
        <span class="brief-foreshadow-label">到期伏笔：</span>
        <el-tag v-for="f in dueForeshadows" :key="f" size="small" type="danger" effect="plain" style="margin-right:4px">
          {{ f }}
        </el-tag>
      </div>
      <div v-if="prevTail" class="brief-prev-tail">
        <span class="brief-prev-label">上章章尾：</span>
        <span class="brief-prev-text">{{ prevTail.slice(-160) }}…</span>
      </div>
    </div>

    <!-- 骨架 -->
    <el-skeleton v-if="loading && !options.length" :rows="8" animated />

    <template v-else>
      <SushiCards
        mode="ensemble"
        :items="cardItems"
        :picked-id="pickedId"
        item-title-field="title"
        item-subtitle-field="hooks"
        item-content-field="summary"
        :plate-count="3"
        :allow-custom="true"
        :allow-regenerate="true"
        rerun-label="🔄 再抽 3 版"
        custom-label="✏️ 我自己写一版"
        empty-label="点下方按钮生成 3 版方向卡"
        @pick="onPick"
        @rerun="onRerun"
        @custom-write="onCustomWrite"
        @edit-card="onEditCard"
      >
        <template #item="{ item }">
          <div style="display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 4px">
            <el-tag v-if="item.beat_type" size="small" type="warning" effect="plain">{{ BEAT_LABELS[item.beat_type] || item.beat_type }}</el-tag>
            <el-tag v-if="item.day" size="small" type="info" effect="plain">第{{ item.day }}天</el-tag>
          </div>
          <div v-if="(item.foreshadow_advance || []).length" style="font-size: 11px; color: #e6a23c; line-height: 1.6">
            🪝 推进：{{ (item.foreshadow_advance || []).join(' · ') }}
          </div>
          <div v-if="(item.entities_on_stage || []).length" style="font-size: 11px; color: #909399; line-height: 1.6; margin-top: 2px">
            👥 登台：{{ (item.entities_on_stage || []).join(' · ') }}
          </div>
        </template>
      </SushiCards>

      <!-- 选中后：补充修改输入 -->
      <div v-if="pickedIndex != null" class="brief-extra">
        <div class="brief-extra-title">📝 对选中版补充说明（可选）</div>
        <el-input
          v-model="extraEdits"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 5 }"
          placeholder="比如：节奏放慢一点 / 不要死配角 / 钩子再明确 / 对话多一些…… 不填就按原卡走"
        />
      </div>
    </template>

    <template #footer>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span class="brief-footer-hint">选版只是倾向，任何时候都可重抽、自编、微调</span>
        <div>
          <el-button @click="close">取消</el-button>
          <el-button
            type="primary"
            :loading="loading"
            :disabled="pickedIndex == null"
            @click="confirmSelect"
          >就按这版开写 ✍</el-button>
        </div>
      </div>
    </template>

    <!-- 自定义编写对话框 -->
    <el-dialog v-model="showCustomWrite" title="✏️ 自己写本章方向" width="560px" append-to-body>
      <el-input
        v-model="customText"
        type="textarea"
        :autosize="{ minRows: 6, maxRows: 14 }"
        placeholder="写下你心中本章的方向：开篇场景、推进什么、冲突点、章尾钩子…… 20 字以上即可"
      />
      <template #footer>
        <el-button @click="showCustomWrite = false">取消</el-button>
        <el-button type="primary" @click="submitCustomWrite">就用我的</el-button>
      </template>
    </el-dialog>

    <!-- 就地编辑对话框 -->
    <el-dialog v-model="editingVisible" title="✏️ 编辑这版方向" width="560px" append-to-body @close="editingIndex = null">
      <el-input
        v-model="editingText"
        type="textarea"
        :autosize="{ minRows: 6, maxRows: 14 }"
        placeholder="直接修改这版方向描述"
      />
      <template #footer>
        <el-button @click="editingIndex = null">取消</el-button>
        <el-button type="primary" @click="applyEdit">保存修改</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<style scoped>
.brief-modal :deep(.el-dialog__body) {
  background: linear-gradient(180deg, #0f1419 0%, #1a1f2b 100%);
  color: #d4d7de;
  padding: 16px 20px;
}
.brief-modal :deep(.el-dialog__header) {
  background: #0f1419;
  color: #e6a23c;
  border-bottom: 1px solid #2a3040;
}
.brief-modal :deep(.el-dialog__title) {
  color: #e6a23c;
  letter-spacing: 1px;
}
.brief-modal :deep(.el-dialog__footer) {
  background: #0f1419;
  border-top: 1px solid #2a3040;
}

.brief-info-bar {
  background: rgba(230, 162, 60, 0.08);
  border: 1px solid rgba(230, 162, 60, 0.25);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 12px;
  font-size: 12px;
}
.brief-info-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 6px;
  align-items: center;
}
.brief-day-select {
  width: 180px;
}
.brief-day-select :deep(.el-select__wrapper) {
  background: #1a1f2b !important;
  box-shadow: 0 0 0 1px #2a3040 inset !important;
}
.brief-day-select :deep(.el-select__placeholder),
.brief-day-select :deep(.el-select__selected-item) {
  color: #d4d7de !important;
  font-size: 12px;
}
.brief-foreshadows {
  margin-top: 6px;
  line-height: 1.8;
}
.brief-foreshadow-label {
  color: #f56c6c;
  font-weight: 600;
  margin-right: 4px;
}
.brief-prev-tail {
  margin-top: 6px;
  color: #909399;
  line-height: 1.6;
}
.brief-prev-label {
  color: #909399;
  margin-right: 4px;
}
.brief-prev-text {
  font-style: italic;
}

.brief-extra {
  margin-top: 12px;
  padding: 10px 12px;
  background: rgba(103, 194, 58, 0.06);
  border: 1px solid rgba(103, 194, 58, 0.2);
  border-radius: 6px;
}
.brief-extra-title {
  font-size: 13px;
  color: #67c23a;
  font-weight: 600;
  margin-bottom: 6px;
}
.brief-footer-hint {
  font-size: 12px;
  color: #909399;
}

/* SushiCards 在暗色背景下的微调：通过 deep 穿透 */
.brief-modal :deep(.sushi-card-block) {
  background: transparent;
  border-color: #2a3040;
}
.brief-modal :deep(.sushi-cand) {
  background: #1a1f2b;
  border-color: #2a3040;
  color: #d4d7de;
}
.brief-modal :deep(.sushi-cand:hover) {
  border-color: #e6a23c;
  box-shadow: 0 2px 12px rgba(230, 162, 60, 0.2);
}
.brief-modal :deep(.sushi-cand--picked) {
  border-color: #67c23a;
  box-shadow: 0 0 0 2px rgba(103, 194, 58, 0.25);
}
.brief-modal :deep(.sushi-cand-title) { color: #e6a23c; }
.brief-modal :deep(.sushi-cand-subtitle) { color: #f0a050; }
.brief-modal :deep(.sushi-cand-text) { color: #c0c4cc; }
.brief-modal :deep(.sushi-cand-axis) {
  background: rgba(230, 162, 60, 0.12);
  color: #e6a23c;
}
.brief-modal :deep(.sushi-cand-icon-btn) {
  background: #1a1f2b;
  border-color: #2a3040;
  color: #d4d7de;
}
.brief-modal :deep(.sushi-hint),
.brief-modal :deep(.sushi-axis-simple) { color: #909399; }
.brief-modal :deep(.sushi-ensemble-toolbar .el-input__wrapper) {
  background: #1a1f2b;
  box-shadow: 0 0 0 1px #2a3040 inset;
}
.brief-modal :deep(.sushi-ensemble-toolbar .el-input__inner) {
  color: #d4d7de;
}
</style>
