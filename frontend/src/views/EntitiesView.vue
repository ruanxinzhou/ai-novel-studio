<script setup lang="ts">
// 实体库：角色/地点/道具/情节线/伏笔——手动补录 + 从正文 LLM 抽取
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ENTITY_STATUS_LABELS,
  ENTITY_TYPES,
  ENTITY_TYPE_LABELS,
  entitiesApi,
  type EntityInfo,
} from '../api'
import { currentProjectId } from '../state'

const all = ref<EntityInfo[]>([])
const counts = ref<Record<string, number>>({})
const typeFilter = ref('')
const keyword = ref('')
const loading = ref(false)

const dialogVisible = ref(false)
const editingId = ref<string | null>(null) // null = 新建
const form = ref({ type: 'character', name: '', summary: '', details: '', status: '' })
const saving = ref(false)

const extractVisible = ref(false)
const extractText = ref('')
const extractChapter = ref<number | null>(null)
const extracting = ref(false)
const extractResult = ref<{ created: number; updated: number; dropped: number } | null>(null)

const statusOptions = computed(() => {
  const base = ['active', 'retired']
  return form.value.type === 'foreshadow' ? ['planted', 'paid', 'dropped'] : base
})

const filtered = computed(() =>
  all.value.filter((e) => {
    if (typeFilter.value && e.type !== typeFilter.value) return false
    const k = keyword.value.trim()
    if (k && !(e.name.includes(k) || e.summary.includes(k) || e.details.includes(k))) return false
    return true
  }),
)

async function refresh() {
  if (!currentProjectId.value) {
    all.value = []
    return
  }
  loading.value = true
  try {
    const r = await entitiesApi.list(currentProjectId.value)
    all.value = r.entities
    counts.value = r.counts
  } catch (e) {
    ElMessage.error(`实体加载失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.value = { type: typeFilter.value || 'character', name: '', summary: '', details: '', status: '' }
  dialogVisible.value = true
}

function openEdit(e: EntityInfo) {
  editingId.value = e.id
  form.value = { type: e.type, name: e.name, summary: e.summary, details: e.details, status: e.status }
  dialogVisible.value = true
}

async function save() {
  if (!currentProjectId.value) return
  if (!form.value.name.trim()) {
    ElMessage.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    if (editingId.value) {
      await entitiesApi.update(currentProjectId.value, editingId.value, {
        name: form.value.name,
        summary: form.value.summary,
        details: form.value.details,
        status: form.value.status || undefined,
      })
      ElMessage.success('已保存')
    } else {
      await entitiesApi.add(currentProjectId.value, {
        type: form.value.type,
        name: form.value.name,
        summary: form.value.summary,
        details: form.value.details,
      })
      ElMessage.success('已添加')
    }
    dialogVisible.value = false
    await refresh()
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    saving.value = false
  }
}

async function remove(e: EntityInfo) {
  try {
    await ElMessageBox.confirm(`删除${ENTITY_TYPE_LABELS[e.type]}「${e.name}」？`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await entitiesApi.remove(currentProjectId.value!, e.id)
    ElMessage.success('已删除')
    await refresh()
  } catch (err) {
    ElMessage.error(`删除失败：${(err as Error).message}`)
  }
}

async function doExtract() {
  if (!currentProjectId.value) return
  if (!extractText.value.trim()) {
    ElMessage.warning('请粘贴要抽取的正文/剧情文本')
    return
  }
  extracting.value = true
  extractResult.value = null
  try {
    const r = await entitiesApi.extract(
      currentProjectId.value,
      extractText.value,
      extractChapter.value || undefined,
    )
    extractResult.value = {
      created: r.created.length,
      updated: r.updated.length,
      dropped: r.dropped.length,
    }
    ElMessage.success(`抽取完成：新增 ${r.created.length}，补充 ${r.updated.length}`)
    all.value = r.entities
    counts.value = r.counts
  } catch (e) {
    ElMessage.error(`抽取失败：${(e as Error).message}`)
  } finally {
    extracting.value = false
  }
}

onMounted(refresh)
watch(currentProjectId, () => {
  extractVisible.value = false
  extractResult.value = null
  refresh()
})
</script>

<template>
  <!-- 单根包裹：v-show 依赖单根元素，多根时 display:none 不生效 -->
  <div>
    <div v-if="!currentProjectId">
      <el-empty description="先在顶部选择一个小说项目" />
    </div>
    <template v-else>
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px">
          <span style="font-weight: 600">实体库（图谱节点）</span>
          <div style="display: flex; gap: 8px">
            <el-button type="primary" plain @click="extractVisible = true">从正文抽取</el-button>
            <el-button type="primary" @click="openCreate">手动补录</el-button>
          </div>
        </div>
      </template>

      <div style="display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px">
        <el-radio-group v-model="typeFilter" size="small">
          <el-radio-button value="">全部（{{ all.length }}）</el-radio-button>
          <el-radio-button v-for="t in ENTITY_TYPES" :key="t" :value="t">
            {{ ENTITY_TYPE_LABELS[t] }}（{{ counts[t] ?? 0 }}）
          </el-radio-button>
        </el-radio-group>
        <el-input
          v-model="keyword"
          placeholder="搜索名称/摘要/设定"
          size="small"
          clearable
          style="width: 220px; margin-left: auto"
        />
      </div>

      <el-table :data="filtered" v-loading="loading" size="small">
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.type === 'foreshadow' ? 'warning' : 'info'">
              {{ ENTITY_TYPE_LABELS[row.type] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="160" show-overflow-tooltip />
        <el-table-column prop="summary" label="一句话" show-overflow-tooltip />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'active' || row.status === 'paid' ? 'success' : row.status === 'planted' ? 'warning' : 'info'">
              {{ ENTITY_STATUS_LABELS[row.status] ?? row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="出现章节" width="140">
          <template #default="{ row }">
            <span v-if="row.appearances.length" style="font-size: 12px">第 {{ row.appearances.join('、') }} 章</span>
            <span v-else style="color: #ccc">—</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="80">
          <template #default="{ row }">
            <span style="font-size: 12px; color: #888">{{ row.source === 'extracted' ? '抽取' : '手动' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button size="small" plain @click="openEdit(row)">编辑</el-button>
            <el-button size="small" plain type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div style="margin-top: 12px; font-size: 12px; color: #aaa">
        实体按项目隔离；「从正文抽取」用个人成本通道，抽取结果合并进库（同名只补空字段与章节，不覆盖人工内容）。
        「项目」页可一键导出 Obsidian Vault——实体互链成图谱，可在 Obsidian 关系图里点选查看。
      </div>
    </el-card>

    <!-- 新建/编辑 -->
    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑实体' : '手动补录实体'" width="560">
      <el-form label-width="70px">
        <el-form-item label="类型">
          <el-select v-model="form.type" :disabled="!!editingId" style="width: 100%">
            <el-option v-for="t in ENTITY_TYPES" :key="t" :value="t" :label="ENTITY_TYPE_LABELS[t]" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="如：韩立 / 掌天瓶 / 青牛镇" />
        </el-form-item>
        <el-form-item label="一句话">
          <el-input v-model="form.summary" placeholder="一句话说清它是什么" />
        </el-form-item>
        <el-form-item label="设定">
          <el-input v-model="form.details" type="textarea" :rows="4" placeholder="外貌、性格、能力、来历、状态等设定（可空）" />
        </el-form-item>
        <el-form-item v-if="editingId" label="状态">
          <el-select v-model="form.status" clearable style="width: 100%">
            <el-option v-for="s in statusOptions" :key="s" :value="s" :label="ENTITY_STATUS_LABELS[s]" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 从正文抽取 -->
    <el-dialog v-model="extractVisible" title="从正文抽取实体" width="640">
      <el-form label-width="70px">
        <el-form-item label="章节号">
          <el-input-number v-model="extractChapter" :min="1" placeholder="可空" style="width: 160px" />
          <span style="margin-left: 8px; font-size: 12px; color: #888">填写后自动记入实体出现章节</span>
        </el-form-item>
        <el-form-item label="文本">
          <el-input
            v-model="extractText"
            type="textarea"
            :rows="12"
            placeholder="粘贴本章正文或剧情（也可以去「项目」页复制已采纳版本）"
          />
        </el-form-item>
      </el-form>
      <el-alert
        v-if="extractResult"
        :title="`新增 ${extractResult.created} 个，补充 ${extractResult.updated} 个，丢弃 ${extractResult.dropped} 个无效项`"
        type="success"
        :closable="false"
        style="margin-bottom: 8px"
      />
      <template #footer>
        <el-button @click="extractVisible = false">关闭</el-button>
        <el-button type="primary" :loading="extracting" @click="doExtract">开始抽取</el-button>
      </template>
    </el-dialog>
    </template>
  </div>
</template>
