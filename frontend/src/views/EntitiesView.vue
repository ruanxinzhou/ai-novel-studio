<script setup lang="ts">
// 实体库：角色/地点/道具/情节线/伏笔——手动补录 + 从正文 LLM 抽取
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ENTITY_STATUS_LABELS,
  ENTITY_TYPES,
  ENTITY_TYPE_LABELS,
  entitiesApi,
  protagonistApi,
  type EntityInfo,
  type ProtagonistState,
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

// ---- 主角（唐迟）档案 ----
const protagonist = ref<ProtagonistState | null>(null)
const proEdit = ref(false)
const proSaving = ref(false)
const proForm = ref({
  current_body_identity: '',
  current_location: '',
  sanity_hint: '',
  contamination_level: 0,
  blue_star_bonds_text: '' as string,  // 一行一条 "名字|关系|深度|备注"
  known_truths_text: '' as string,
})

async function loadProtagonist() {
  if (!currentProjectId.value) { protagonist.value = null; return }
  try {
    const r = await protagonistApi.get(currentProjectId.value)
    protagonist.value = r.protagonist
  } catch (e) {
    console.warn('load protagonist failed', e)
  }
}

function openProEdit() {
  if (!protagonist.value) return
  const p = protagonist.value
  proForm.value = {
    current_body_identity: p.current_body_identity || '',
    current_location: p.current_location || '',
    sanity_hint: p.sanity_hint || '',
    contamination_level: p.contamination_level || 0,
    blue_star_bonds_text: (p.blue_star_bonds || [])
      .map(b => `${b.name}|${b.relation}|${b.depth}|${b.note || ''}`).join('\n'),
    known_truths_text: (p.known_truths || []).join('\n'),
  }
  proEdit.value = true
}

async function saveProtagonist() {
  if (!currentProjectId.value || !protagonist.value) return
  proSaving.value = true
  try {
    const bonds = proForm.value.blue_star_bonds_text.split('\n')
      .map(l => l.trim()).filter(Boolean)
      .map(line => {
        const [name, relation, depth, note] = line.split('|').map(s => (s || '').trim())
        return { name: name || '?', relation: relation || '?', depth: depth || '浅', note: note || '' }
      })
    const truths = proForm.value.known_truths_text.split('\n')
      .map(l => l.trim()).filter(Boolean)
    const body = {
      current_body_identity: proForm.value.current_body_identity,
      current_location: proForm.value.current_location,
      sanity_hint: proForm.value.sanity_hint,
      contamination_level: Math.max(0, Math.min(100, Number(proForm.value.contamination_level) || 0)),
      blue_star_bonds: bonds,
      known_truths: truths,
    }
    const r = await protagonistApi.patch(currentProjectId.value, body)
    protagonist.value = r.protagonist
    proEdit.value = false
    ElMessage.success('主角档案已保存')
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    proSaving.value = false
  }
}

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

// 分页：过滤结果按 20/页切，翻筛条件自动回第 1 页
const entityPage = ref(1)
const entityPageSize = 20
const paged = computed(() =>
  filtered.value.slice((entityPage.value - 1) * entityPageSize, entityPage.value * entityPageSize),
)
watch([typeFilter, keyword], () => {
  entityPage.value = 1
})

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

onMounted(() => { refresh(); loadProtagonist() })
watch(currentProjectId, () => {
  extractVisible.value = false
  extractResult.value = null
  refresh()
  loadProtagonist()
})
</script>

<template>
  <!-- 单根包裹：v-show 依赖单根元素，多根时 display:none 不生效 -->
  <div>
    <div v-if="!currentProjectId">
      <el-empty description="先在顶部选择一个小说项目" />
    </div>
    <template v-else>
    <!-- 主角档案卡 -->
    <el-card shadow="never" style="margin-bottom: 12px; border-left: 4px solid #e6a23c">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">
            主角 · {{ protagonist?.current_body_name || '唐迟' }}
            <el-tag size="small" type="warning" style="margin-left: 8px">污染 {{ protagonist?.contamination_level ?? 0 }}/100</el-tag>
            <el-tag size="small" type="info" style="margin-left: 4px">已完成 {{ protagonist?.field_count ?? 0 }} 场</el-tag>
          </span>
          <el-button size="small" type="primary" plain @click="openProEdit">编辑档案</el-button>
        </div>
      </template>
      <div v-if="protagonist" style="font-size: 13px; line-height: 1.7; color: #333">
        <div style="margin-bottom: 6px">
          <b>当前位置：</b>{{ protagonist.current_location || '—' }}
          <span v-if="protagonist.current_day" style="margin-left: 12px"><b>天数：</b>第 {{ protagonist.current_day }} 天</span>
        </div>
        <div style="margin-bottom: 6px"><b>人设/身份：</b>{{ protagonist.current_body_identity || '（未填写）' }}</div>
        <div style="margin-bottom: 6px"><b>精神状态：</b>{{ protagonist.sanity_hint || '状态稳定' }}</div>
        <div v-if="protagonist.blue_star_bonds?.length" style="margin-bottom: 6px">
          <b>蓝星羁绊：</b>
          <span v-for="(b, i) in protagonist.blue_star_bonds" :key="i" style="margin-right: 12px">
            <el-tag size="small" :type="b.depth === '深' ? 'danger' : b.depth === '中' ? 'warning' : 'info'">{{ b.name }}</el-tag>
            <span style="color: #666; margin-left: 2px">{{ b.relation }}（{{ b.depth }}）</span>
          </span>
        </div>
        <div v-if="protagonist.active_crafts?.length" style="margin-bottom: 6px">
          <b>已掌握手艺：</b>{{ protagonist.active_crafts.map(c => c.name + (c.proficiency ? `(${c.proficiency})` : '')).join('、') }}
        </div>
        <div v-if="protagonist.dead_echoes?.length" style="margin-bottom: 6px">
          <b>死者残影：</b>{{ protagonist.dead_echoes.map(e => e.name).join('、') }}
        </div>
        <div v-if="protagonist.god_marks?.length" style="margin-bottom: 6px">
          <b>邪神标记：</b>{{ protagonist.god_marks.map(m => `${m.god}·${m.seal_form || '?'}`).join('、') }}
        </div>
        <div v-if="protagonist.brought_items?.length" style="margin-bottom: 6px">
          <b>带回物品：</b>{{ protagonist.brought_items.map(b => `${b.name}(污${b.contam ?? 0})`).join('、') }}
        </div>
        <div v-if="protagonist.known_truths?.length" style="margin-bottom: 6px">
          <b>已知真相：</b>
          <ul style="margin: 4px 0 0 20px; padding: 0">
            <li v-for="(t, i) in protagonist.known_truths" :key="i" style="list-style: disc">{{ t }}</li>
          </ul>
        </div>
        <div v-if="protagonist.kill_record?.length" style="margin-bottom: 6px">
          <b>杀青账：</b>
          <ul style="margin: 4px 0 0 20px; padding: 0">
            <li v-for="(k, i) in protagonist.kill_record" :key="i" style="list-style: disc; font-family: monospace">{{ k }}</li>
          </ul>
        </div>
      </div>
    </el-card>

    <!-- 主角编辑弹窗 -->
    <el-dialog v-model="proEdit" title="编辑主角档案" width="640px">
      <el-form label-position="top" size="small">
        <el-form-item label="人设/身份（长文本，可以直接写一段描述）">
          <el-input v-model="proForm.current_body_identity" type="textarea" :rows="4" placeholder="怂、惜命、加班成瘾的普通社畜；..." />
        </el-form-item>
        <el-form-item label="当前位置">
          <el-input v-model="proForm.current_location" placeholder="蓝星·出租屋 / ××副本·xx镇" />
        </el-form-item>
        <el-form-item label="精神/状态提示">
          <el-input v-model="proForm.sanity_hint" placeholder="未触发副本，精神稳定；长期睡眠不足..." />
        </el-form-item>
        <el-form-item label="累积污染度（0-100）">
          <el-input-number v-model="proForm.contamination_level" :min="0" :max="100" :step="1" />
        </el-form-item>
        <el-form-item label="蓝星羁绊（每行一条，格式：名字|关系|深度（浅/中/深）|备注）">
          <el-input v-model="proForm.blue_star_bonds_text" type="textarea" :rows="4"
            placeholder="煤球|猫（三花田园猫）|深|唐迟捡回来的，唯一情绪锚&#10;楼下阿姨|邻居|浅|总塞咸菜给唐迟" />
        </el-form-item>
        <el-form-item label="已知真相碎片（每行一条）">
          <el-input v-model="proForm.known_truths_text" type="textarea" :rows="3" placeholder="讣告必须签收..." />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="proEdit = false">取消</el-button>
        <el-button type="primary" :loading="proSaving" @click="saveProtagonist">保存</el-button>
      </template>
    </el-dialog>

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

      <el-table :data="paged" v-loading="loading" size="small">
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
      <div v-if="filtered.length > entityPageSize" style="margin-top: 14px; display: flex; justify-content: center">
        <el-pagination
          layout="total, prev, pager, next"
          :total="filtered.length"
          :page-size="entityPageSize"
          :current-page="entityPage"
          @current-change="(p: number) => (entityPage = p)"
        />
      </div>

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
