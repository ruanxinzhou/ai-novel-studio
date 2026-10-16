<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { currentProjectId } from '../state'

const emit = defineEmits<{
  (e: 'done'): void
}>()

// ---- 卡片元数据 ----
// 列表卡条目的字段 schema
interface FieldSpec {
  key: string
  label: string
  type: 'text' | 'textarea' | 'number' | 'switch'
  placeholder?: string
  width?: number // 列宽百分比
}

interface ListCardSpec {
  kind: 'list'
  itemLabel: (idx: number, item: any) => string
  fields: FieldSpec[]
  newItem: () => any
}
interface DictCardSpec {
  kind: 'dict'
}
type CardSpec = ListCardSpec | DictCardSpec

const COST_SPEC: ListCardSpec = {
  kind: 'list',
  itemLabel: (i, it) => `${i + 1}. ${it.obsession_name || '(未命名执念)'}`,
  fields: [
    { key: 'field_no', label: '卷号', type: 'number', width: 12, placeholder: '1' },
    { key: 'obsession_name', label: '执念名', type: 'text', width: 28, placeholder: '如：伞下求归' },
    { key: 'side_effect', label: '副作用 / 代价', type: 'textarea', width: 36, placeholder: '具体副作用描述' },
    { key: 'due_hint', label: '兑现时机', type: 'text', width: 16, placeholder: '近场/三卷内/终局' },
    { key: 'paid', label: '已还', type: 'switch', width: 8 },
  ],
  newItem: () => ({ field_no: 1, obsession_name: '', side_effect: '', due_hint: '近场', paid: false }),
}

const PAGES_SPEC: ListCardSpec = {
  kind: 'list',
  itemLabel: (i, it) => `${i + 1}. 第${it.field_no ?? '?'}卷 · ${(it.ash_text || '').slice(0, 24) || '残页'}`,
  fields: [
    { key: 'field_no', label: '卷号', type: 'number', width: 14, placeholder: '1' },
    { key: 'ash_text', label: '讣告残页 / 灰烬语录', type: 'textarea', width: 86, placeholder: '例：唐迟捏着张湿漉漉的讣告，才发现自己的名字已被新的墨迹盖住一半。' },
  ],
  newItem: () => ({ field_no: 1, ash_text: '' }),
}

const NOTES_SPEC: ListCardSpec = {
  kind: 'list',
  itemLabel: (i, it) => `${i + 1}. 第${it.field_no ?? '?'}卷 · ${(it.one_liner || '').slice(0, 24) || '一句话'}`,
  fields: [
    { key: 'field_no', label: '卷号', type: 'number', width: 14, placeholder: '1' },
    { key: 'one_liner', label: '杀青一句话', type: 'textarea', width: 86, placeholder: '例：第七日，唐迟才发现自己从未真正离开过那把伞。' },
  ],
  newItem: () => ({ field_no: 1, one_liner: '' }),
}

const CARDS_META: {
  key: string
  title: string
  hint: string
  spec: CardSpec
}[] = [
  { key: 'premise', title: '核心设定（含讣告五铁律）', hint: '世界观、主角初入无限流时的"讣告"铁律、基础异常设定。JSON 对象。', spec: { kind: 'dict' } },
  { key: 'tangchi', title: '唐迟起始状态', hint: '主角唐迟的初始身体、职业、性格、初始羁绊、侵蚀度。JSON 对象。', spec: { kind: 'dict' } },
  { key: 'endgame', title: '长线伏笔与终局', hint: '七层结局递进、关键长线伏笔。JSON 对象。', spec: { kind: 'dict' } },
  { key: 'cost_debts', title: '代价与欠账', hint: '未兑现的执念代价账（每卷杀青时自动追加）。条目式，空列表代表暂无欠账。', spec: COST_SPEC },
  { key: 'remaining_pages', title: '剩余页码（全书规模）', hint: '讣告残页语录集（每卷杀青产生一句）。条目式，初为空。', spec: PAGES_SPEC },
  { key: 'seven_day_pulse', title: '七日脉冲（节奏规律）', hint: '每卷 7 天的脉冲节奏模板。JSON 对象（含 day_beats 数组）。', spec: { kind: 'dict' } },
  { key: 'finale_notes', title: '终局注记', hint: '每卷杀青时的一句话注记。条目式，初为空。', spec: NOTES_SPEC },
  { key: 'craft', title: '写作宪法', hint: '文风、禁区、黄金三章规则。JSON 对象。', spec: { kind: 'dict' } },
]

type CardVM = {
  key: string
  title: string
  hint: string
  spec: CardSpec
  filled: boolean
  data: any
  editing: boolean
  draft: string   // dict 卡：字符串编辑；list 卡：使用 listItems
  listItems: any[]
  saving: boolean
}

const cards = ref<CardVM[]>(
  CARDS_META.map((m) => ({
    key: m.key,
    title: m.title,
    hint: m.hint,
    spec: m.spec,
    filled: false,
    data: null,
    editing: false,
    draft: '',
    listItems: [],
    saving: false,
  })),
)

const activeNames = ref<string[]>(['premise'])
const loading = ref(true)
const filledCount = ref(0)

function isFilled(c: CardVM): boolean {
  if (c.data == null) return false
  if (Array.isArray(c.data)) return true // 列表卡合法初始就是 []
  if (typeof c.data === 'object') return Object.keys(c.data).length > 0
  return !!c.data
}

function pretty(v: any): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

async function checkCards() {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  let filled = 0
  await Promise.all(
    cards.value.map(async (c) => {
      try {
        const r = await fetch(`/api/projects/${pid}/book/${c.key}`)
        if (!r.ok) {
          c.filled = false
          c.data = null
          c.draft = ''
          c.listItems = []
          return
        }
        const d = await r.json()
        const data = d.data ?? null
        c.data = data
        if (c.spec.kind === 'list') {
          c.listItems = Array.isArray(data) ? data.map((it) => ({ ...it })) : []
          c.draft = ''
        } else {
          c.draft = pretty(data)
          c.listItems = []
        }
        c.filled = isFilled(c)
        if (c.filled) filled++
      } catch {
        c.filled = false
        c.data = null
        c.draft = ''
        c.listItems = []
      }
    }),
  )
  filledCount.value = filled
  loading.value = false
}

function startEdit(c: CardVM) {
  c.editing = true
  if (c.spec.kind === 'list') {
    c.listItems = Array.isArray(c.data) ? c.data.map((it: any) => ({ ...it })) : []
  } else {
    c.draft = pretty(c.data)
  }
}

function cancelEdit(c: CardVM) {
  c.editing = false
}

function addListItem(c: CardVM) {
  if (c.spec.kind !== 'list') return
  c.listItems.push((c.spec as ListCardSpec).newItem())
}

function removeListItem(c: CardVM, idx: number) {
  c.listItems.splice(idx, 1)
}

async function saveCard(c: CardVM) {
  const pid = currentProjectId.value
  if (!pid) return
  c.saving = true
  try {
    let payload: any
    if (c.spec.kind === 'list') {
      // 列表卡：过滤掉完全空的条目
      const spec = c.spec as ListCardSpec
      payload = c.listItems.filter((it) => {
        // 只要任意一个"正文"字段非空就保留
        return spec.fields.some((f) => {
          if (f.type === 'switch') return false // switch 不算正文
          const v = it[f.key]
          return v !== null && v !== undefined && String(v).trim() !== ''
        })
      })
    } else {
      const trimmed = c.draft.trim()
      if (!trimmed) {
        payload = {}
      } else {
        try {
          payload = JSON.parse(trimmed)
          if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
            payload = { text: trimmed }
          }
        } catch {
          payload = { text: trimmed }
        }
      }
    }
    const r = await fetch(`/api/projects/${pid}/book/${c.key}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: payload }),
    })
    if (!r.ok) {
      const t = await r.text()
      throw new Error(t || `HTTP ${r.status}`)
    }
    const d = await r.json()
    c.data = d.data ?? payload
    if (c.spec.kind === 'list') {
      c.listItems = Array.isArray(c.data) ? c.data.map((it: any) => ({ ...it })) : []
    } else {
      c.draft = pretty(c.data)
    }
    c.filled = isFilled(c)
    c.editing = false
    ElMessage.success(`《${c.title}》已保存`)
    filledCount.value = cards.value.filter((x) => x.filled).length
  } catch (e: any) {
    ElMessage.error(`保存失败：${e?.message || e}`)
  } finally {
    c.saving = false
  }
}

function onDone() {
  emit('done')
}

function fieldValue(item: any, f: FieldSpec): any {
  const v = item[f.key]
  if (f.type === 'switch') return !!v
  if (f.type === 'number') return v == null || v === '' ? null : Number(v)
  return v == null ? '' : String(v)
}

function setField(item: any, f: FieldSpec, val: any) {
  if (f.type === 'number') {
    item[f.key] = val === '' || val == null ? null : Number(val)
  } else if (f.type === 'switch') {
    item[f.key] = !!val
  } else {
    item[f.key] = val
  }
}

onMounted(() => {
  checkCards()
})

defineExpose({ refresh: checkCards })
</script>

<template>
  <div class="book-setup">
    <div class="bs-header">
      <h2>开书设定</h2>
      <p class="bs-sub">
        8 张书卡共同定义这本书的基盘。对象卡（JSON）直接编辑，列表卡条目式增删。
        <el-tag size="small" :type="filledCount === 8 ? 'success' : 'warning'">
          {{ filledCount }}/8 已填
        </el-tag>
        <el-button size="small" text @click="checkCards" :loading="loading">🔄 刷新</el-button>
      </p>
    </div>

    <el-skeleton v-if="loading" :rows="8" animated />

    <el-collapse v-else v-model="activeNames" class="bs-collapse">
      <el-collapse-item
        v-for="c in cards"
        :key="c.key"
        :name="c.key"
      >
        <template #title>
          <div class="card-title-row">
            <span>{{ c.title }}</span>
            <el-tag
              v-if="c.filled"
              size="small"
              type="success"
              effect="plain"
            >✓ {{ c.spec.kind === 'list' ? `${c.data?.length || 0} 条` : '已填' }}</el-tag>
            <el-tag
              v-else
              size="small"
              type="danger"
              effect="plain"
            >{{ c.spec.kind === 'list' ? '空列表' : '未填' }}</el-tag>
          </div>
        </template>
        <div class="card-body">
          <p class="card-hint">{{ c.hint }}</p>

          <!-- 对象卡：JSON textarea -->
          <template v-if="c.spec.kind === 'dict'">
            <div v-if="!c.editing" class="card-view">
              <pre v-if="c.filled" class="card-pre">{{ pretty(c.data) }}</pre>
              <el-empty v-else description="这张卡还没填" :image-size="50" />
              <div class="card-actions">
                <el-button size="small" type="primary" @click="startEdit(c)">
                  {{ c.filled ? '✏️ 编辑' : '＋ 开始填写' }}
                </el-button>
              </div>
            </div>
            <div v-else class="card-edit">
              <el-input
                v-model="c.draft"
                type="textarea"
                :rows="14"
                placeholder="请填写 JSON 对象，也可直接写文字（自动包一层 text 字段）。"
                class="card-textarea"
              />
              <div class="card-actions">
                <el-button size="small" @click="cancelEdit(c)">取消</el-button>
                <el-button size="small" type="primary" :loading="c.saving" @click="saveCard(c)">
                  💾 保存
                </el-button>
              </div>
            </div>
          </template>

          <!-- 列表卡：条目式表单 -->
          <template v-else-if="c.spec.kind === 'list'">
            <div v-if="!c.editing" class="card-view">
              <div v-if="Array.isArray(c.data) && c.data.length > 0" class="list-items-view">
                <div
                  v-for="(it, i) in c.data"
                  :key="i"
                  class="list-item-row"
                >
                  <span class="list-item-label">{{ (c.spec as any).itemLabel(i, it) }}</span>
                  <el-tag v-if="it.paid === true" size="small" type="success" effect="plain">已还</el-tag>
                  <el-tag v-else-if="it.paid === false" size="small" type="warning" effect="plain">未还</el-tag>
                </div>
              </div>
              <el-empty v-else description="还没有任何条目" :image-size="50">
                <template #description>
                  <span style="color:#909399; font-size:13px">空列表是合法初始态；后续每卷杀青时会自动追加条目，也可以现在手动加。</span>
                </template>
              </el-empty>
              <div class="card-actions">
                <el-button size="small" type="primary" @click="startEdit(c)">
                  ✏️ {{ Array.isArray(c.data) && c.data.length > 0 ? '编辑条目' : '＋ 添加条目' }}
                </el-button>
              </div>
            </div>

            <div v-else class="card-edit list-edit">
              <div v-if="c.listItems.length === 0" class="list-empty-hint">暂无条目，点"＋ 添加一条"新增。</div>
              <div
                v-for="(it, i) in c.listItems"
                :key="i"
                class="list-item-form"
              >
                <div class="list-item-head">
                  <span class="list-item-idx">#{{ i + 1 }}</span>
                  <el-button size="small" text type="danger" @click="removeListItem(c, i)">🗑 删除</el-button>
                </div>
                <div class="list-fields">
                  <div
                    v-for="f in (c.spec as ListCardSpec).fields"
                    :key="f.key"
                    class="list-field"
                    :style="{ flexBasis: (f.width || 25) + '%' }"
                  >
                    <label class="list-field-label">{{ f.label }}</label>
                    <el-input
                      v-if="f.type === 'text'"
                      :model-value="fieldValue(it, f)"
                      @update:model-value="(v:any) => setField(it, f, v)"
                      :placeholder="f.placeholder"
                      size="small"
                    />
                    <el-input-number
                      v-else-if="f.type === 'number'"
                      :model-value="fieldValue(it, f)"
                      @update:model-value="(v:any) => setField(it, f, v)"
                      :min="0"
                      :controls="false"
                      size="small"
                      class="list-number"
                    />
                    <el-switch
                      v-else-if="f.type === 'switch'"
                      :model-value="fieldValue(it, f)"
                      @update:model-value="(v:any) => setField(it, f, v)"
                      active-text="已还"
                      inactive-text="未还"
                    />
                    <el-input
                      v-else-if="f.type === 'textarea'"
                      :model-value="fieldValue(it, f)"
                      @update:model-value="(v:any) => setField(it, f, v)"
                      :placeholder="f.placeholder"
                      type="textarea"
                      :rows="2"
                      size="small"
                    />
                  </div>
                </div>
              </div>
              <el-button size="small" plain @click="addListItem(c)">＋ 添加一条</el-button>
              <div class="card-actions">
                <el-button size="small" @click="cancelEdit(c)">取消</el-button>
                <el-button size="small" type="primary" :loading="c.saving" @click="saveCard(c)">
                  💾 保存
                </el-button>
              </div>
            </div>
          </template>
        </div>
      </el-collapse-item>
    </el-collapse>

    <div class="bs-footer">
      <p class="bs-hint" v-if="filledCount < 8">
        提示：三张列表卡（代价/残页/注记）允许空列表，是合法初始态，会随着写作推进自动追加条目。
      </p>
      <el-button type="primary" size="large" @click="onDone" :loading="loading">
        完成开书设定，开始第一卷
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.book-setup {
  max-width: 900px;
  margin: 0 auto;
}
.bs-header {
  margin-bottom: 16px;
}
.bs-header h2 {
  margin: 0 0 4px;
  font-size: 22px;
  color: #e4e7ed;
}
.bs-sub {
  margin: 0;
  color: #909399;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.bs-collapse {
  background: #1c1f24;
  border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.08);
}
:deep(.el-collapse-item__header) {
  background: transparent;
  color: #e4e7ed;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
:deep(.el-collapse-item__wrap) {
  background: transparent;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
:deep(.el-collapse-item__content) {
  color: #c0c4cc;
}
.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding-right: 16px;
}
.card-body {
  padding: 8px 0 16px;
}
.card-hint {
  margin: 0 0 10px;
  font-size: 12px;
  color: #909399;
}
.card-pre {
  margin: 0;
  padding: 12px 14px;
  background: #0f1217;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  color: #c0c4cc;
  font-family: 'Consolas', 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
}
.card-textarea :deep(textarea) {
  font-family: 'Consolas', 'Courier New', monospace !important;
  font-size: 12.5px !important;
  background: #0f1217 !important;
  color: #c0c4cc !important;
}
.list-items-view {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #0f1217;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  padding: 10px 12px;
}
.list-item-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #c0c4cc;
}
.list-item-label {
  flex: 1;
  font-family: 'Consolas', monospace;
  font-size: 12.5px;
}
/* 列表编辑 */
.list-empty-hint {
  padding: 12px;
  text-align: center;
  color: #606266;
  font-size: 13px;
  border: 1px dashed rgba(255,255,255,0.1);
  border-radius: 6px;
  margin-bottom: 10px;
}
.list-item-form {
  background: #0f1217;
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.list-item-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.list-item-idx {
  font-family: 'Consolas', monospace;
  font-size: 12px;
  color: #7aa2c1;
  font-weight: bold;
}
.list-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 12px;
}
.list-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 120px;
}
.list-field-label {
  font-size: 11px;
  color: #909399;
}
.list-number {
  width: 100%;
}
.list-number :deep(.el-input__wrapper) {
  background: #181c24 !important;
}
.card-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  align-items: center;
}
.bs-footer {
  margin-top: 24px;
  text-align: center;
}
.bs-hint {
  font-size: 12px;
  color: #909399;
  margin: 0 0 12px;
}

/* 输入框深色风格统一 */
:deep(.el-input__wrapper), :deep(.el-textarea__inner) {
  background: #181c24 !important;
  color: #c0c4cc !important;
  box-shadow: 0 0 0 1px rgba(255,255,255,0.08) inset !important;
}
:deep(.el-input__wrapper.is-focus), :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 1px #7aa2c1 inset !important;
}
:deep(.el-input-number .el-input__wrapper) {
  background: #181c24 !important;
}
</style>
