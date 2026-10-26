<script setup lang="ts">
/**
 * 角色记忆碎片编辑器（魂穿专用）。
 *
 * 回答的问题是「唐迟穿进这具身体之前，这具身体经历过什么」。
 * 每条碎片带一个「入戏第几天想起」——正文注入时按本章所在的 Day 闸门过滤，
 * 所以第 1 章不会泄出第 7 天的底牌。这是这个功能唯一不能出错的地方。
 *
 * 与声音档案同一套交互契约：
 * - draft 只返回不写库（作者手写的碎片和调整过的解锁日不会被一次生成冲掉）
 * - 逐条勾选、勾选前可改解锁日和文案
 * - 落库走 PUT 整体覆盖，未勾选的条数不会被草稿动
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  memoriesApi,
  type MemoryFragment,
} from '../api'

const props = defineProps<{
  pid: string
  /** 预选角色（从实体库点进来时带上） */
  presetId?: string
  /** 预选卷号（从卷面板点进来时带上，决定按哪一卷原剧本生成） */
  volNo?: number | null
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const DAY_MIN = 1
const DAY_MAX = 7

/** 主角的展示名（后端没有单独的列表接口，沿用声音档案那套角色来源） */
interface Row {
  id: string
  name: string
  is_protagonist: boolean
  memory_count: number
}
const rows = ref<Row[]>([])
const loading = ref(false)
const saving = ref(false)
const generating = ref(false)
const selectedId = ref<string>('')
const detailName = ref<string>('')
const current = ref<MemoryFragment[]>([])

const editing = ref<MemoryFragment[]>([])
const showPreview = ref(false)

function newLocalId() {
  return `mem-local-${Math.random().toString(36).slice(2, 8)}`
}

async function loadRows() {
  if (!props.pid) return
  loading.value = true
  try {
    const r = await memoriesApi.get(props.pid, 'character-protagonist-tangchi')
    current.value = r.memories || []
  } catch {
    current.value = []
  } finally {
    loading.value = false
  }
}

async function loadDetail(eid: string) {
  if (!props.pid || !eid) return
  try {
    const r = await memoriesApi.get(props.pid, eid)
    current.value = r.memories || []
  } catch {
    current.value = []
  }
}

function dayTag(d: number) {
  if (d <= 2) return { type: 'success' as const, text: `第${d}天 · 醒来就有` }
  if (d <= 5) return { type: 'warning' as const, text: `第${d}天 · 中段浮起` }
  return { type: 'danger' as const, text: `第${d}天 · 压到最后的底牌` }
}

function addBlank() {
  editing.value = [
    ...editing.value,
    {
      id: newLocalId(),
      text: '',
      recovers_at: 3,
      revealed_at: 0,
      source: '',
      note: '',
    },
  ]
}

function removeAt(i: number) {
  editing.value = editing.value.filter((_, idx) => idx !== i)
}

async function save() {
  if (!props.pid || !selectedId.value) return
  const body = editing.value
    .filter((m) => m.text.trim().length > 0)
    .map((m) => ({
      id: m.id,
      text: m.text.trim(),
      recovers_at: m.recovers_at,
      revealed_at: m.revealed_at || 0,
      source: m.source || '',
      note: m.note || '',
    }))
  saving.value = true
  try {
    await memoriesApi.save(props.pid, selectedId.value, body)
    ElMessage.success(`已保存 ${body.length} 条记忆碎片`)
    await loadDetail(selectedId.value)
  } catch (e: any) {
    ElMessage.error(`保存失败：${e?.message ?? e}`)
  } finally {
    saving.value = false
  }
}

/** 草稿是**为哪个角色**生成的。
 *
 * 与 VoiceprintEditor 同一个坑：预览弹窗是 append-to-body 的独立浮层，
 * 作者在弹窗打开期间点得到底下的角色列表。切了 selectedId 就变了，
 * 于是「陆骁的碎片」会被写进主角名下。
 * 草稿必须绑定生成时的角色，采用时写回那个角色。
 */
const draftOwnerId = ref<string>('')

/** 切换选中的角色。草稿预览打开时拒绝切换。 */
function selectRole(id: string) {
  if (showPreview.value) {
    ElMessage.info('草稿预览中，先采用或关闭预览再切换角色')
    return
  }
  selectedId.value = id
}

async function generateDraft() {
  if (!props.pid || !selectedId.value) return
  generating.value = true
  try {
    const r = await memoriesApi.draft(props.pid, selectedId.value, props.volNo ?? null)
    if (!r.ok || !r.memories) {
      ElMessage.error(r.error || '生成失败，可手动填写')
      return
    }
    // 草稿归属锁定为生成时的角色
    draftOwnerId.value = selectedId.value
    editing.value = (r.memories || []).map((m) => ({ ...m }))
    showPreview.value = true
  } catch (e: any) {
    ElMessage.error(`生成失败：${e?.message ?? e}`)
  } finally {
    generating.value = false
  }
}

async function applyDraft() {
  if (!props.pid) return
  // 写回**草稿的归属角色**，不是当前选中的角色
  const owner = draftOwnerId.value || selectedId.value
  if (!owner) return
  const picked = editing.value.filter((m) => m.text.trim().length > 0)
  if (!picked.length) {
    ElMessage.warning('至少留一条再采用')
    return
  }
  saving.value = true
  try {
    await memoriesApi.save(
      props.pid,
      owner,
      picked.map((m) => ({
        id: m.id,
        text: m.text.trim(),
        recovers_at: m.recovers_at,
        revealed_at: m.revealed_at || 0,
        source: m.source || '',
        note: m.note || '',
      })),
    )
    ElMessage.success(`已采用 ${picked.length} 条碎片`)
    showPreview.value = false
    // 切回草稿归属角色，让作者看到它落到位了
    if (selectedId.value !== owner) {
      selectedId.value = owner
    } else {
      await loadDetail(owner)
    }
  } catch (e: any) {
    ElMessage.error(`采用失败：${e?.message ?? e}`)
  } finally {
    saving.value = false
  }
}

const dayCounts = computed(() => {
  const counts: Record<number, number> = {}
  for (const m of editing.value) {
    if (!m.text.trim()) continue
    counts[m.recovers_at] = (counts[m.recovers_at] || 0) + 1
  }
  return counts
})

watch(
  () => props.pid,
  async () => {
    await loadRows()
    if (!selectedId.value) selectedId.value = props.presetId || ''
  },
  { immediate: true },
)
watch(selectedId, (v) => {
  // 草稿预览期间不改归属：草稿是为上一个角色生成的，
  // 采用时要写回那个角色（draftOwnerId），这里只管刷新表单。
  if (!v) return
  if (showPreview.value) return
  loadDetail(v)
})
watch(
  () => props.presetId,
  (v) => {
    if (v) selectedId.value = v
  },
)
// 切换角色时把已保存的碎片载入编辑区，避免误以为「切换后内容还在」
watch(selectedId, async (v) => {
  if (!v) return
  await loadDetail(v)
  editing.value = current.value.map((m) => ({ ...m }))
})
</script>

<template>
  <div style="display: flex; gap: 12px; height: 100%; min-height: 420px">
    <div style="flex: 1; border: 1px solid #ebeef5; border-radius: 4px; padding: 12px; overflow: auto">
      <el-alert type="info" :closable="false" style="margin-bottom: 12px">
        <template #title>
          <b>记忆碎片</b>：唐迟附身之前这具身体经历过什么，拆成若干条，每条标一个
          <b>「入戏第几天想起」</b>。写正文时按本章所在的 Day 闸门注入——
          第 1 章不会泄出第 7 天的底牌，这是这个功能唯一不能出错的地方。
        </template>
      </el-alert>

      <el-alert type="warning" :closable="false" style="margin-bottom: 12px">
        <template #title>
          <b>填在宿主名下 = 唐迟脑中的残忆</b>：本卷魂穿宿主（卷 2 是「陆骁」）的碎片
          不会写成「陆骁想起什么」，而是以主角视角出现在唐迟身上——他只知道
          <b>这双手做过什么</b>，不知道为什么。其余角色的碎片则是该角色本人记得的事。
          所以宿主的碎片建议用<b>第三人称、具体场景</b>来写，别写成设定说明。
        </template>
      </el-alert>

      <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 10px">
        <el-input
          v-model="selectedId"
          placeholder="角色实体 ID（主角填 character-protagonist-tangchi）"
          style="flex: 1"
          size="small"
        />
        <el-button size="small" @click="addBlank">＋ 新增一条</el-button>
        <el-button size="small" :loading="generating" @click="generateDraft">
          ✨ AI 生成草稿
        </el-button>
        <el-button size="small" type="primary" :loading="saving" @click="save">
          保存
        </el-button>
        <div style="flex: 1" />
        <el-button size="small" @click="emit('close')">完成</el-button>
      </div>

      <div style="font-size: 12px; color: #909399; margin-bottom: 10px">
        当前编辑：{{ detailName || selectedId || '（未选角色）' }}
        <span v-if="dayCounts && Object.keys(dayCounts).length">
          ；解锁分布
          <el-tag
            v-for="d in [1, 2, 3, 4, 5, 6, 7]"
            :key="d"
            size="small"
            effect="plain"
            :type="dayCounts[d] ? 'primary' : 'info'"
            style="margin-left: 4px"
          >
            第{{ d }}天 {{ dayCounts[d] || 0 }}
          </el-tag>
        </span>
      </div>

      <el-empty v-if="!editing.length" description="还没有碎片，点「AI 生成草稿」或「＋ 新增一条」" :image-size="70" />

      <div style="display: flex; flex-direction: column; gap: 10px">
        <div
          v-for="(m, i) in editing"
          :key="m.id || i"
          style="border: 1px solid #ebeef5; border-radius: 4px; padding: 10px"
        >
          <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 6px; flex-wrap: wrap">
            <el-tag size="small" :type="dayTag(m.recovers_at).type" effect="plain">
              {{ dayTag(m.recovers_at).text }}
            </el-tag>
            <el-select v-model.number="m.recovers_at" size="small" style="width: 110px">
              <el-option v-for="d in 7" :key="d" :value="d" :label="`第 ${d} 天想起`" />
            </el-select>
            <el-input-number
              v-model="m.revealed_at"
              :min="0"
              :max="999"
              size="small"
              controls-position="right"
              style="width: 120px"
              placeholder="未写出"
            />
            <span style="font-size: 11px; color: #909399">已写出章号（0 = 未写）</span>
            <div style="flex: 1" />
            <el-button size="small" type="danger" text @click="removeAt(i)">删除</el-button>
          </div>

          <el-input
            v-model="m.text"
            type="textarea"
            :rows="2"
            placeholder="一条记忆，30-200 字。写具体到能成画面：一种残留的身体感觉、一个他解释不了的动作、一段他想起就发冷的经历。"
          />
          <el-input
            v-model="m.source"
            size="small"
            style="margin-top: 6px"
            placeholder="溯源（可选）：来自哪条档案或原剧本哪一幕"
          />
        </div>
      </div>
    </div>
  </div>

  <!-- AI 草稿预览：逐条确认 + 可改解锁日，改完再采用 -->
  <el-dialog
    v-model="showPreview"
    :title="`✨ ${detailName || selectedId} 的记忆碎片草稿`"
    width="760px"
    top="6vh"
    append-to-body
  >
    <el-alert type="warning" :closable="false" style="margin-bottom: 12px">
      <template #title>
        这是 AI 依据实体档案 + 本卷原剧本推导的<b>草稿</b>，还没写进档案库。
        逐条确认：勾选的才会保存，不勾的保持你原来的内容。<b>解锁日请重点核对</b>——
        把底牌排到第 1 天会让唐迟一睁眼就看穿整个副本，先知感和悬疑张力全部归零。
      </template>
    </el-alert>

    <div style="display: flex; flex-direction: column; gap: 10px; max-height: 56vh; overflow: auto; padding-right: 4px">
      <div
        v-for="(m, i) in editing"
        :key="m.id || i"
        style="border: 1px solid #ebeef5; border-radius: 4px; padding: 10px"
      >
        <div style="display: flex; gap: 8px; align-items: center; margin-bottom: 6px">
          <el-select v-model.number="m.recovers_at" size="small" style="width: 120px">
            <el-option v-for="d in 7" :key="d" :value="d" :label="`第 ${d} 天想起`" />
          </el-select>
          <el-tag size="small" :type="dayTag(m.recovers_at).type" effect="plain">
            {{ dayTag(m.recovers_at).text }}
          </el-tag>
          <div style="flex: 1" />
          <el-button size="small" type="danger" text @click="removeAt(i)">丢弃这条</el-button>
        </div>
        <el-input v-model="m.text" type="textarea" :rows="2" />
        <el-input v-model="m.source" size="small" style="margin-top: 6px" placeholder="溯源（可选）" />
      </div>
    </div>

    <template #footer>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span style="font-size: 12px; color: #909399">
          共 {{ editing.length }} 条草稿
        </span>
        <div>
          <el-button @click="showPreview = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="applyDraft">采用勾选碎片</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>