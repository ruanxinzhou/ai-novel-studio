<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  drawerOpen,
  currentProjectId,
  currentPhase,
} from '../state'
import {
  projectsApi,
  entitiesApi,
  corpusApi,
  skillsApi,
  vaultApi,
  ENTITY_TYPE_LABELS,
  ENTITY_STATUS_LABELS,
  LAYERS,
  LAYER_LABELS,
  type ProjectMeta,
  type ProjectDetail,
  type EntityInfo,
  type SkillModule,
} from '../api'
import VoiceprintEditor from './VoiceprintEditor.vue'
import MemoryEditor from './MemoryEditor.vue'

const open = computed({
  get: () => drawerOpen.value,
  set: (v: boolean) => (drawerOpen.value = v),
})

const activeNames = ref([
  'manuscript', 'entities', 'materials', 'craft', 'advanced', 'project',
])

// ============== 加载状态 ==============
const loaded = ref<Record<string, boolean>>({})
const loading = ref<Record<string, boolean>>({})

// ============== 当前项目 ==============
const currentProject = ref<ProjectDetail | null>(null)

async function loadProject() {
  if (!currentProjectId.value) return
  loading.value.project = true
  try {
    currentProject.value = await projectsApi.detail(currentProjectId.value)
    loaded.value.project = true
  } catch (e: any) {
    ElMessage.error(`加载项目失败: ${e?.message ?? e}`)
  } finally {
    loading.value.project = false
  }
}

// ============== 书稿：项目列表 ==============
const projectList = ref<ProjectMeta[]>([])

async function loadProjectList() {
  loading.value.projectList = true
  try {
    const r = await projectsApi.list(1, 100)
    projectList.value = r.projects ?? []
    loaded.value.projectList = true
  } catch (e: any) {
    ElMessage.error(`加载项目列表失败: ${e?.message ?? e}`)
  } finally {
    loading.value.projectList = false
  }
}

function switchProject(pid: string) {
  if (pid === currentProjectId.value) return
  currentProjectId.value = pid
  drawerOpen.value = false
  ElMessage.success('已切换项目')
}

async function openBookSetup() {
  currentPhase.value = 'setup'
  drawerOpen.value = false
}

async function createProject() {
  try {
    const { value: title } = await ElMessageBox.prompt('请输入新书名', '新建项目', {
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
      inputPattern: /.+/,
      inputErrorMessage: '书名不能为空',
    })
    const { value: intro } = await ElMessageBox.prompt('请输入一句话简介（可留空）', '新建项目', {
      confirmButtonText: '创建',
      cancelButtonText: '取消',
      inputValue: '',
    })
    const p = await projectsApi.create(title, intro ?? '')
    currentProjectId.value = p.id
    drawerOpen.value = false
    ElMessage.success(`已创建项目《${p.title}》`)
  } catch {
    /* 用户取消 */
  }
}

async function openProjectListDialog() {
  await loadProjectList()
  // 简单用 ElMessageBox 做选择：项目列表展示为 HTML（不可交互），用户输入项目 ID；
  // 更友好的方式是走一个独立弹窗，这里用 prompt 列表提示再选
  const lines = projectList.value
    .map((p, i) => `${i + 1}. [${p.id}] 《${p.title}》`)
    .join('\n')
  try {
    const { value: choice } = await ElMessageBox.prompt(
      `选择项目序号或直接输入项目 ID：\n\n${lines}`,
      '切换项目',
      {
        confirmButtonText: '切换',
        cancelButtonText: '取消',
        inputPlaceholder: '例如 1 或项目 ID',
      },
    )
    const trimmed = (choice ?? '').trim()
    if (!trimmed) return
    const idx = Number(trimmed)
    let pid = ''
    if (!Number.isNaN(idx) && idx >= 1 && idx <= projectList.value.length) {
      pid = projectList.value[idx - 1].id
    } else {
      pid = trimmed
    }
    switchProject(pid)
  } catch {
    /* cancel */
  }
}

// ============== 实体 ==============
const entities = ref<EntityInfo[]>([])

async function loadEntities() {
  if (!currentProjectId.value) return
  loading.value.entities = true
  try {
    const r = await entitiesApi.list(currentProjectId.value, {})
    entities.value = r.entities ?? []
    loaded.value.entities = true
  } catch (e: any) {
    ElMessage.error(`加载实体失败: ${e?.message ?? e}`)
  } finally {
    loading.value.entities = false
  }
}

async function addEntity() {
  if (!currentProjectId.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  try {
    const { value: name } = await ElMessageBox.prompt('实体名称', '添加实体', {
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
      inputPattern: /.+/,
      inputErrorMessage: '名称不能为空',
    })
    const typeOptions = Object.entries(ENTITY_TYPE_LABELS)
      .map(([k, v], i) => `${i + 1}. ${v} (${k})`)
      .join('\n')
    const { value: typeChoice } = await ElMessageBox.prompt(
      `选择类型（输入序号或英文 key）:\n${typeOptions}`,
      '添加实体',
      { confirmButtonText: '下一步', cancelButtonText: '取消', inputValue: '1' },
    )
    let type = (typeChoice ?? '').trim()
    const idx = Number(type)
    const typeKeys = Object.keys(ENTITY_TYPE_LABELS)
    if (!Number.isNaN(idx) && idx >= 1 && idx <= typeKeys.length) {
      type = typeKeys[idx - 1]
    }
    if (!ENTITY_TYPE_LABELS[type]) {
      ElMessage.error('无效的类型')
      return
    }
    const { value: summary } = await ElMessageBox.prompt('简介（可留空）', '添加实体', {
      confirmButtonText: '添加',
      cancelButtonText: '取消',
      inputValue: '',
    })
    await entitiesApi.add(currentProjectId.value, {
      name, type, summary: summary ?? '', status: 'active',
    })
    ElMessage.success('已添加实体')
    await loadEntities()
  } catch {
    /* cancel */
  }
}

// ============== 角色声音档案 Dialog ==============
const voiceprintDialogOpen = ref(false)
function openVoiceprints(presetId?: string) {
  voiceprintDialogOpen.value = true
  // 组件内自己按 presetId 预选
  // 注意：presetId 必须是 string——@click="openVoiceprints" 会把
  // PointerEvent 当第一个参数传进来，不挡一下的话 presetId 变成事件对象，
  // 组件会拿它去请求 /voiceprints/[object PointerEvent]（404）。
  if (typeof presetId === 'string' && presetId) voiceprintPreset.value = presetId
}
const voiceprintPreset = ref<string | undefined>(undefined)

// ============== 角色记忆碎片 Dialog ==============
// 魂穿设定专用：这具身体在被附身之前经历过什么。与声音档案同一套
// 「生成草稿 → 预览 → 逐条勾选采用」的交互契约。
const memoryDialogOpen = ref(false)
const memoryPreset = ref<string | undefined>(undefined)
const memoryVolNo = ref<number | null>(null)
function openMemories(presetId?: string, volNo?: number | null) {
  memoryDialogOpen.value = true
  if (presetId) memoryPreset.value = presetId
  memoryVolNo.value = volNo ?? null
}

// ============== 完整实体库 Dialog ==============
const vaultDialogOpen = ref(false)
const vaultLoading = ref(false)
const vaultFilter = ref({ type: '', status: '', keyword: '' })
const vaultList = ref<EntityInfo[]>([])
const vaultCounts = ref<Record<string, number>>({})
const vaultSelected = ref<EntityInfo | null>(null)
const vaultEditing = ref(false)
const vaultForm = ref<{
  name: string; summary: string; details: string; status: string; type: string
  state_attrs: Record<string, string>
}>({
  name: '', summary: '', details: '', status: 'active', type: 'character',
  state_attrs: {},
})
const vaultSaving = ref(false)

/**
 * spec 3 · 角色知情边界字段定义。
 *
 * 只给 character 挂这两个字段，因为知情边界是**角色**的核心属性——
 * 无限流设定的张力全靠信息不对称撑着。
 *
 * `knows` / `blinded_to` 刻意与 `knows_about` 关系边分开：关系边记「谁认识谁」，
 * 这两个记「知道什么 / 不知道什么」。不自动推断，作者自己填。
 */
const KNOWLEDGE_FIELDS = [
  {
    key: 'knows',
    label: '已知信息',
    ph: '例：唐迟是第七个住客、老陈的孙女在三年前失踪',
    tip: '写他时可以据此行动、说出口的信息',
  },
  {
    key: 'blinded_to',
    label: '不知情',
    ph: '例：猫眼里的东西、房东其实是那个神',
    tip: '他看不到、说不出、不会凭直觉猜到。章节里出现相关内容时只能表现为困惑或无知——审稿会据此报警',
  },
] as const

/** spec 3：伏笔推进状态展示（仅 foreshadow 用）。 */
function foreshadowProgress(e: EntityInfo | null): { text: string; cls: string } {
  if (!e || e.type !== 'foreshadow') return { text: '', cls: '' }
  const last = e.last_advanced_ch ?? 0
  const cnt = e.advance_count ?? 0
  if (!last) return { text: '从未推进', cls: 'fs-never' }
  return { text: `第 ${last} 章推进过（累计 ${cnt} 次）`, cls: cnt >= 2 ? 'fs-ok' : 'fs-thin' }
}

async function loadVault() {
  if (!currentProjectId.value) return
  vaultLoading.value = true
  try {
    const q: Record<string, string> = {}
    if (vaultFilter.value.type) q.type = vaultFilter.value.type
    if (vaultFilter.value.status) q.status = vaultFilter.value.status
    if (vaultFilter.value.keyword) q.keyword = vaultFilter.value.keyword
    const r = await entitiesApi.list(currentProjectId.value, q)
    vaultList.value = r.entities ?? []
    vaultCounts.value = r.counts ?? {}
    if (vaultSelected.value) {
      const still = vaultList.value.find((e) => e.id === vaultSelected.value!.id)
      vaultSelected.value = still ?? null
    }
  } catch (e: any) {
    ElMessage.error(`加载实体库失败: ${e?.message ?? e}`)
  } finally {
    vaultLoading.value = false
  }
}

function openVault() {
  vaultDialogOpen.value = true
  vaultFilter.value = { type: '', status: '', keyword: '' }
  vaultSelected.value = null
  vaultEditing.value = false
  loadVault()
}

function selectEntity(e: EntityInfo) {
  vaultSelected.value = e
  vaultEditing.value = false
}

function startEdit() {
  if (!vaultSelected.value) return
  const e = vaultSelected.value
  vaultForm.value = {
    name: e.name, summary: e.summary ?? '', details: e.details ?? '',
    status: e.status, type: e.type,
    state_attrs: { ...(e.state_attrs || {}) },
  }
  vaultEditing.value = true
}

async function saveEntity() {
  if (!currentProjectId.value || !vaultSelected.value) return
  vaultSaving.value = true
  try {
    // spec 3：state_attrs 走**合并**语义（空串=删除该键），所以只传这两个字段即可，
    // 不必把其他实体的状态属性一起 PUT 回去。
    const attrs: Record<string, string> = {}
    if (vaultForm.value.type === 'character') {
      for (const f of KNOWLEDGE_FIELDS) {
        attrs[f.key] = (vaultForm.value.state_attrs[f.key] ?? '').trim()
      }
    }
    const updated = await entitiesApi.update(
      currentProjectId.value,
      vaultSelected.value.id,
      {
        name: vaultForm.value.name,
        summary: vaultForm.value.summary,
        details: vaultForm.value.details,
        status: vaultForm.value.status,
        state_attrs: attrs,
      },
    )
    Object.assign(vaultSelected.value, updated)
    vaultEditing.value = false
    ElMessage.success('已保存')
    await Promise.all([loadVault(), loadEntities()])
  } catch (e: any) {
    ElMessage.error(`保存失败: ${e?.message ?? e}`)
  } finally {
    vaultSaving.value = false
  }
}

async function removeEntity() {
  if (!currentProjectId.value || !vaultSelected.value) return
  const e = vaultSelected.value
  try {
    await ElMessageBox.confirm(`确定删除实体「${e.name}」？`, '删除实体', {
      confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning',
    })
  } catch {
    return
  }
  try {
    await entitiesApi.remove(currentProjectId.value, e.id)
    ElMessage.success('已删除')
    vaultSelected.value = null
    await Promise.all([loadVault(), loadEntities()])
  } catch (e2: any) {
    ElMessage.error(`删除失败: ${e2?.message ?? e2}`)
  }
}

async function addVaultEntity() {
  if (!currentProjectId.value) return
  try {
    const { value: name } = await ElMessageBox.prompt('实体名称', '新增实体', {
      confirmButtonText: '下一步', cancelButtonText: '取消',
      inputPattern: /.+/, inputErrorMessage: '名称不能为空',
    })
    const typeOptions = Object.entries(ENTITY_TYPE_LABELS)
      .map(([k, v], i) => `${i + 1}. ${v} (${k})`).join('\n')
    const { value: typeChoice } = await ElMessageBox.prompt(
      `选择类型（输入序号或英文 key）:\n${typeOptions}`,
      '新增实体',
      { confirmButtonText: '添加', cancelButtonText: '取消', inputValue: '1' },
    )
    let type = (typeChoice ?? '').trim()
    const idx = Number(type)
    const typeKeys = Object.keys(ENTITY_TYPE_LABELS)
    if (!Number.isNaN(idx) && idx >= 1 && idx <= typeKeys.length) {
      type = typeKeys[idx - 1]
    }
    if (!ENTITY_TYPE_LABELS[type]) {
      ElMessage.error('无效的类型')
      return
    }
    await entitiesApi.add(currentProjectId.value, { name, type, status: 'active' })
    ElMessage.success('已添加')
    await Promise.all([loadVault(), loadEntities()])
  } catch {
    /* cancel */
  }
}


// ============== 资料 ==============
const corpusTypes = ref<{ type: string; count: number }[]>([])
const corpusTags = ref<{ tag: string; count: number }[]>([])

async function loadCorpus() {
  loading.value.corpus = true
  try {
    const [t, g] = await Promise.all([corpusApi.types(), corpusApi.tags()])
    corpusTypes.value = t.types ?? []
    corpusTags.value = g.tags ?? []
    loaded.value.corpus = true
  } catch (e: any) {
    ElMessage.error(`加载语料统计失败: ${e?.message ?? e}`)
  } finally {
    loading.value.corpus = false
  }
}

async function importChaishu() {
  try {
    const { value: dir } = await ElMessageBox.prompt('拆书目录路径', '导入拆书', {
      confirmButtonText: '下一步',
      cancelButtonText: '取消',
      inputPattern: /.+/,
      inputErrorMessage: '目录路径不能为空',
    })
    const { value: title } = await ElMessageBox.prompt('书名（可留空）', '导入拆书', {
      confirmButtonText: '导入',
      cancelButtonText: '取消',
      inputValue: '',
    })
    ElMessage.info('正在导入拆书，请稍候…')
    const r = await corpusApi.importChaishu(dir, title ?? '')
    ElMessage.success(`导入完成：新增 ${r.added} 条片段`)
    await loadCorpus()
  } catch (e: any) {
    if (e !== 'cancel' && e !== 'close') {
      ElMessage.error(`导入失败: ${e?.message ?? e}`)
    }
  }
}

// ============== 写作规范 ==============
const skillModules = ref<SkillModule[]>([])

async function loadSkills() {
  loading.value.skills = true
  try {
    const r = await skillsApi.list()
    skillModules.value = r.modules ?? []
    loaded.value.skills = true
  } catch (e: any) {
    ElMessage.error(`加载技能模块失败: ${e?.message ?? e}`)
  } finally {
    loading.value.skills = false
  }
}

// ============== 高级 ==============
const vaultExportDir = ref('')
const vaultExporting = ref(false)
const currentModel = 'MiniMax-M3.1-Flash-Preview'

async function doVaultExport() {
  if (!currentProjectId.value) {
    ElMessage.warning('请先选择项目')
    return
  }
  vaultExporting.value = true
  try {
    const r = await vaultApi.export(currentProjectId.value)
    vaultExportDir.value = r.out_dir
    ElMessage.success(`Vault 导出完成: ${r.out_dir}`)
  } catch (e: any) {
    ElMessage.error(`导出失败: ${e?.message ?? e}`)
  } finally {
    vaultExporting.value = false
  }
}

function openPromptWorkshop() {
  ElMessage.info('Prompt 工坊将在后续版本独立入口')
}

// ============== 返回首页 ==============
async function goHome() {
  try {
    await ElMessageBox.confirm('确定要返回项目选择页吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    currentProjectId.value = ''
    drawerOpen.value = false
    currentPhase.value = ''
  } catch {
    /* cancel */
  }
}

// ============== 统一加载 ==============
async function loadAll() {
  await Promise.allSettled([
    loadProject(),
    loadEntities(),
    loadCorpus(),
    loadSkills(),
  ])
}

watch(drawerOpen, (v) => {
  if (v && !loaded.value.all) {
    loaded.value.all = true
    loadAll()
  }
})

onMounted(() => {
  if (drawerOpen.value) {
    loaded.value.all = true
    loadAll()
  }
})

function formatDate(s?: string) {
  if (!s) return '-'
  try {
    return new Date(s).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return s
  }
}

function layerCompleted(layer: string): boolean {
  const l = currentProject.value?.layers?.[layer]
  return !!(l && l.current)
}
</script>

<template>
  <el-drawer v-model="open" title="设置与工具" direction="rtl" size="480px">
    <el-collapse v-model="activeNames">
      <!-- 📖 书稿 -->
      <el-collapse-item name="manuscript" title="📖 书稿">
        <p class="group-desc">书卡、卷章、终局等核心创作配置</p>
        <div class="manuscript-block">
          <div class="current-project-card" v-if="currentProject">
            <div class="cp-title">《{{ currentProject.title }}》</div>
            <div class="cp-intro" v-if="currentProject.intro">{{ currentProject.intro }}</div>
            <div class="cp-meta">ID: {{ currentProject.id }}</div>
          </div>
          <div v-else class="empty-hint">尚未加载项目</div>
          <div class="btn-row">
            <el-tooltip content="打开书卡 8 张设定界面" placement="top">
              <el-button size="small" type="primary" @click="openBookSetup">
                📖 书卡设定
              </el-button>
            </el-tooltip>
            <el-tooltip content="列出所有小说项目并切换" placement="top">
              <el-button size="small" @click="openProjectListDialog" :loading="loading.projectList">
                📂 项目列表
              </el-button>
            </el-tooltip>
            <el-tooltip content="创建新小说项目" placement="top">
              <el-button size="small" @click="createProject">
                ➕ 新建项目
              </el-button>
            </el-tooltip>
          </div>
        </div>
      </el-collapse-item>

      <!-- 🗂 实体库 -->
      <el-collapse-item name="entities" title="🗂 实体库">
        <p class="group-desc">角色、地点、道具、伏笔管理</p>
        <div class="btn-row" style="margin-bottom: 8px">
          <el-tooltip content="快速添加一个实体" placement="top">
            <el-button size="small" type="primary" @click="addEntity">
              ➕ 快速添加
            </el-button>
          </el-tooltip>
          <el-button size="small" type="primary" plain @click="openVault">
            📖 打开实体库
          </el-button>
          <el-button size="small" type="warning" plain @click="openVoiceprints">
            🎭 角色声音档案
          </el-button>
          <el-tooltip
            content="魂穿专用：这具身体在被附身之前经历过什么。每条标一个「入戏第几天想起」，正文按天解锁注入"
            placement="top"
          >
            <el-button size="small" type="warning" plain @click="openMemories()">
              🧩 角色记忆碎片
            </el-button>
          </el-tooltip>
        </div>
        <div
          v-loading="loading.entities"
          class="entity-mini-list"
          style="max-height: 320px; overflow-y: auto; border: 1px solid #ebeef5; border-radius: 4px"
        >
          <div v-if="!loading.entities && entities.length === 0" class="empty-hint" style="padding: 16px">暂无实体</div>
          <div
            v-for="e in entities"
            :key="e.id"
            style="padding: 8px 10px; border-bottom: 1px solid #f5f7fa"
          >
            <div style="display: flex; gap: 6px; align-items: center; flex-wrap: wrap">
              <b style="font-size: 13px">{{ e.name }}</b>
              <el-tag size="small" type="info">{{ ENTITY_TYPE_LABELS[e.type] ?? e.type }}</el-tag>
              <el-tag size="small">{{ ENTITY_STATUS_LABELS[e.status] ?? e.status }}</el-tag>
              <span v-if="(e.appearances ?? []).length" style="font-size: 11px; color: #909399; margin-left: auto">
                出场 {{ e.appearances.length }} 章
              </span>
            </div>
            <div
              v-if="e.summary"
              style="font-size: 12px; color: #606266; margin-top: 4px; line-height: 1.5; white-space: pre-wrap; word-break: break-word"
            >{{ e.summary }}</div>
          </div>
        </div>
      </el-collapse-item>

      <!-- 📚 资料 -->
      <el-collapse-item name="materials" title="📚 资料">
        <p class="group-desc">语料、参考书、素材库</p>
        <div v-loading="loading.corpus">
          <div class="section-sub">类型统计</div>
          <div class="tag-wrap">
            <el-tag
              v-for="t in corpusTypes"
              :key="t.type"
              size="small"
              class="stat-tag"
            >{{ t.type }} × {{ t.count }}</el-tag>
            <span v-if="corpusTypes.length === 0" class="empty-hint">暂无</span>
          </div>
          <div class="section-sub">标签统计</div>
          <div class="tag-wrap" style="max-height: 120px; overflow-y: auto">
            <el-tag
              v-for="t in corpusTags"
              :key="t.tag"
              size="small"
              type="info"
              class="stat-tag"
            >{{ t.tag }} × {{ t.count }}</el-tag>
            <span v-if="corpusTags.length === 0" class="empty-hint">暂无</span>
          </div>
        </div>
        <div class="btn-row" style="margin-top: 8px">
          <el-tooltip content="从拆书目录导入并切片入语料库" placement="top">
            <el-button size="small" type="primary" @click="importChaishu">
              📥 导入拆书
            </el-button>
          </el-tooltip>
        </div>
      </el-collapse-item>

      <!-- 📝 写作规范 -->
      <el-collapse-item name="craft" title="📝 写作规范">
        <p class="group-desc">写作宪法、风格指南、模块规则</p>
        <div v-loading="loading.skills">
          <div class="tag-wrap" style="max-height: 240px; overflow-y: auto">
            <el-tag
              v-for="m in skillModules"
              :key="m.name"
              size="small"
              class="stat-tag"
              effect="plain"
            >{{ m.title }}</el-tag>
            <span v-if="!loading.skills && skillModules.length === 0" class="empty-hint">暂无模块</span>
          </div>
        </div>
        <div class="btn-row" style="margin-top: 8px">
          <el-tooltip content="技能编辑器规划中" placement="top">
            <el-button size="small" disabled>查看/编辑模块</el-button>
          </el-tooltip>
        </div>
      </el-collapse-item>

      <!-- 🧪 高级 -->
      <el-collapse-item name="advanced" title="🧪 高级">
        <p class="group-desc">Prompt 工坊、模型、导入导出</p>
        <div class="kv-row">
          <span class="k">当前模型</span>
          <span class="v"><el-tag size="small" type="success">{{ currentModel }}</el-tag></span>
        </div>
        <div class="btn-row">
          <el-tooltip content="Prompt 工坊后续版本开放" placement="top">
            <el-button size="small" @click="openPromptWorkshop">🔧 Prompt 工坊</el-button>
          </el-tooltip>
          <el-tooltip content="将当前项目导出为 Obsidian Vault" placement="top">
            <el-button
              size="small"
              type="primary"
              :loading="vaultExporting"
              @click="doVaultExport"
            >📤 导出 Vault</el-button>
          </el-tooltip>
        </div>
        <div v-if="vaultExportDir" class="vault-out">
          导出路径：<code>{{ vaultExportDir }}</code>
        </div>
      </el-collapse-item>

      <!-- 📂 项目 -->
      <el-collapse-item name="project" title="📂 项目">
        <p class="group-desc">项目管理、详情、回首页</p>
        <div v-if="currentProject" v-loading="loading.project" class="project-detail">
          <div class="kv-row">
            <span class="k">项目 ID</span><span class="v mono">{{ currentProject.id }}</span>
          </div>
          <div class="kv-row">
            <span class="k">创建时间</span><span class="v">{{ formatDate(currentProject.created_at) }}</span>
          </div>
          <div class="kv-row">
            <span class="k">更新时间</span><span class="v">{{ formatDate(currentProject.updated_at) }}</span>
          </div>
          <div class="section-sub">已完成层数</div>
          <div class="layer-tags">
            <el-tag
              v-for="layer in LAYERS"
              :key="layer"
              size="small"
              :type="layerCompleted(layer) ? 'success' : 'info'"
              class="stat-tag"
            >{{ LAYER_LABELS[layer] }}{{ layerCompleted(layer) ? '✓' : '' }}</el-tag>
          </div>
        </div>
        <div v-else class="empty-hint">尚未加载项目</div>
        <div class="btn-row" style="margin-top: 12px">
          <el-tooltip content="清空当前项目，返回首页选择项目" placement="top">
            <el-button size="small" type="danger" plain @click="goHome">
              🏠 返回首页
            </el-button>
          </el-tooltip>
        </div>
      </el-collapse-item>
    </el-collapse>
  </el-drawer>

  <!-- 角色声音档案 Dialog -->
  <el-dialog
    v-model="voiceprintDialogOpen"
    title="🎭 角色声音档案"
    width="1000px"
    top="5vh"
    destroy-on-close
  >
    <VoiceprintEditor
      v-if="currentProjectId"
      :pid="currentProjectId"
      :preset-id="voiceprintPreset"
      @close="voiceprintDialogOpen = false"
    />
  </el-dialog>

  <!-- 角色记忆碎片 Dialog：魂穿专用，按天解锁注入 -->
  <el-dialog
    v-model="memoryDialogOpen"
    title="🧩 角色记忆碎片"
    width="900px"
    top="5vh"
    destroy-on-close
  >
    <MemoryEditor
      v-if="currentProjectId"
      :pid="currentProjectId"
      :preset-id="memoryPreset"
      :vol-no="memoryVolNo"
      @close="memoryDialogOpen = false"
    />
  </el-dialog>

  <!-- 实体库 Dialog -->
  <el-dialog v-model="vaultDialogOpen" title="🗂 实体库" width="960px" top="5vh" destroy-on-close>
    <div style="display: flex; gap: 12px; margin-bottom: 10px; align-items: center; flex-wrap: wrap">
      <el-input v-model="vaultFilter.keyword" placeholder="搜索名称/简介" clearable style="width: 220px" @keyup.enter="loadVault" />
      <el-select v-model="vaultFilter.type" placeholder="类型" clearable style="width: 120px" @change="loadVault">
        <el-option v-for="(label, k) in ENTITY_TYPE_LABELS" :key="k" :label="label" :value="k" />
      </el-select>
      <el-select v-model="vaultFilter.status" placeholder="状态" clearable style="width: 120px" @change="loadVault">
        <el-option v-for="(label, k) in ENTITY_STATUS_LABELS" :key="k" :label="label" :value="k" />
      </el-select>
      <el-button size="small" @click="loadVault">筛选</el-button>
      <span style="flex: 1" />
      <span style="font-size: 12px; color: #909399">
        共 {{ vaultList.length }} 条 ·
        <span v-for="(label, k) in ENTITY_TYPE_LABELS" :key="k" style="margin-left: 6px">
          {{ label }} {{ vaultCounts[k] ?? 0 }}
        </span>
      </span>
      <el-button size="small" type="primary" @click="addVaultEntity">➕ 新增</el-button>
    </div>

    <div v-loading="vaultLoading" style="display: flex; gap: 12px; height: 480px">
      <!-- 左侧列表 -->
      <div style="flex: 1; border: 1px solid #ebeef5; border-radius: 4px; overflow: auto">
        <div
          v-for="e in vaultList"
          :key="e.id"
          @click="selectEntity(e)"
          :style="{
            padding: '10px 12px',
            cursor: 'pointer',
            borderBottom: '1px solid #f5f7fa',
            background: vaultSelected?.id === e.id ? '#ecf5ff' : '#fff',
          }"
        >
          <div style="display: flex; gap: 6px; align-items: center">
            <b>{{ e.name }}</b>
            <el-tag size="small" type="info">{{ ENTITY_TYPE_LABELS[e.type] ?? e.type }}</el-tag>
            <el-tag size="small">{{ ENTITY_STATUS_LABELS[e.status] ?? e.status }}</el-tag>
          </div>
          <div v-if="e.summary" style="font-size: 12px; color: #909399; margin-top: 4px; line-height: 1.4">
            {{ e.summary.slice(0, 80) }}{{ e.summary.length > 80 ? '…' : '' }}
          </div>
          <div style="font-size: 11px; color: #c0c4cc; margin-top: 2px">
            出场章：{{ (e.appearances ?? []).length ? e.appearances.join(', ') : '未出场' }} · 更新 {{ e.updated_at?.slice(0, 16).replace('T', ' ') }}
          </div>
        </div>
        <el-empty v-if="!vaultLoading && vaultList.length === 0" description="暂无实体" :image-size="80" />
      </div>

      <!-- 右侧详情 -->
      <div style="flex: 1; border: 1px solid #ebeef5; border-radius: 4px; padding: 12px; overflow: auto">
        <el-empty v-if="!vaultSelected" description="选择左侧实体查看/编辑" :image-size="80" />
        <template v-else>
          <div v-if="!vaultEditing">
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
              <h3 style="margin: 0">{{ vaultSelected.name }}</h3>
              <el-tag size="small" type="info">{{ ENTITY_TYPE_LABELS[vaultSelected.type] ?? vaultSelected.type }}</el-tag>
              <el-tag size="small">{{ ENTITY_STATUS_LABELS[vaultSelected.status] ?? vaultSelected.status }}</el-tag>
            </div>
            <div style="font-size: 12px; color: #909399; margin-bottom: 10px">
              来源：{{ vaultSelected.source || '-' }} ·
              出场章：{{ (vaultSelected.appearances ?? []).length ? vaultSelected.appearances.join(', ') : '未出场' }} ·
              创建 {{ vaultSelected.created_at?.slice(0, 10) }}
            </div>
            <div style="margin-bottom: 10px">
              <b>简介</b>
              <div style="white-space: pre-wrap; margin-top: 4px; font-size: 13px; color: #606266; line-height: 1.5">
                {{ vaultSelected.summary || '（未填）' }}
              </div>
            </div>
            <div style="margin-bottom: 10px">
              <b>细节档案</b>
              <div style="white-space: pre-wrap; margin-top: 4px; font-size: 13px; color: #606266; line-height: 1.5">
                {{ vaultSelected.details || '（未填）' }}
              </div>
            </div>

            <!-- spec 3：伏笔推进状态。账龄按「最近一次真正推进」算，
                 「从未推进」的那条才是真正该被催的。 -->
            <div v-if="vaultSelected.type === 'foreshadow'" style="margin-bottom: 10px">
              <b>推进状态</b>
              <div style="margin-top: 4px">
                <el-tag size="small" :class="['fs-tag', foreshadowProgress(vaultSelected).cls]">
                  {{ foreshadowProgress(vaultSelected).text }}
                </el-tag>
                <span style="font-size: 12px; color: #909399; margin-left: 8px">
                  埋设章 {{ (vaultSelected.appearances ?? [])[0] ?? '?' }}
                </span>
              </div>
              <div style="font-size: 11px; color: #c0c4cc; margin-top: 4px; line-height: 1.5">
                「从未推进」指被反复提起但从没往前推——这种伏笔在定稿回账时才会被记为 advanced。
              </div>
            </div>

            <!-- spec 3：知情边界。空值=不检查，不自动推断。 -->
            <div
              v-if="vaultSelected.type === 'character' && (vaultSelected.state_attrs?.knows || vaultSelected.state_attrs?.blinded_to)"
              style="margin-bottom: 10px"
            >
              <b>知情边界</b>
              <div v-if="vaultSelected.state_attrs?.knows" style="margin-top: 4px; font-size: 13px">
                <el-tag size="small" type="success" style="margin-right: 6px">已知</el-tag>
                <span style="color: #606266; line-height: 1.5">{{ vaultSelected.state_attrs.knows }}</span>
              </div>
              <div v-if="vaultSelected.state_attrs?.blinded_to" style="margin-top: 6px; font-size: 13px">
                <el-tag size="small" type="danger" style="margin-right: 6px">不知情</el-tag>
                <span style="color: #606266; line-height: 1.5">{{ vaultSelected.state_attrs.blinded_to }}</span>
              </div>
              <div style="font-size: 11px; color: #c0c4cc; margin-top: 6px; line-height: 1.5">
                「不知情」会进审稿：他在本章表现出知道这些内容时会被拦下报警（软提醒）。
              </div>
            </div>
            <div
              v-else-if="vaultSelected.type === 'character'"
              style="margin-bottom: 10px; font-size: 12px; color: #c0c4cc"
            >
              未声明知情边界——点「编辑」可补充。不知道也没关系：空值表示不检查。
            </div>

            <div style="display: flex; gap: 8px; margin-top: 12px">
              <el-button size="small" type="primary" @click="startEdit">编辑</el-button>
              <el-button size="small" type="danger" plain @click="removeEntity">删除</el-button>
            </div>
          </div>

          <el-form v-else label-position="top" size="small">
            <el-form-item label="名称">
              <el-input v-model="vaultForm.name" />
            </el-form-item>
            <el-form-item label="类型">
              <el-select v-model="vaultForm.type" disabled>
                <el-option v-for="(label, k) in ENTITY_TYPE_LABELS" :key="k" :label="label" :value="k" />
              </el-select>
            </el-form-item>
            <el-form-item label="状态">
              <el-select v-model="vaultForm.status">
                <el-option v-for="(label, k) in ENTITY_STATUS_LABELS" :key="k" :label="label" :value="k" />
              </el-select>
            </el-form-item>
            <el-form-item label="简介">
              <el-input v-model="vaultForm.summary" type="textarea" :rows="3" />
            </el-form-item>
            <el-form-item label="细节档案">
              <el-input v-model="vaultForm.details" type="textarea" :rows="8" placeholder="外貌、关系、伏笔、性格、线索…" />
            </el-form-item>
            <!-- spec 3：知情边界编辑。留空=不检查，不自动推断。 -->
            <template v-if="vaultForm.type === 'character'">
              <el-form-item v-for="f in KNOWLEDGE_FIELDS" :key="f.key">
                <template #label>
                  <span>{{ f.label }}</span>
                  <span style="font-size: 11px; color: #c0c4cc; margin-left: 6px; font-weight: normal">
                    {{ f.tip }}
                  </span>
                </template>
                <el-input
                  v-model="vaultForm.state_attrs[f.key]"
                  type="textarea"
                  :rows="2"
                  :placeholder="f.ph"
                />
              </el-form-item>
            </template>
            <div style="display: flex; gap: 8px">
              <el-button type="primary" :loading="vaultSaving" @click="saveEntity">保存</el-button>
              <el-button @click="vaultEditing = false">取消</el-button>
            </div>
          </el-form>
        </template>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
/* spec 3：伏笔推进状态标签。从未推进 = 最该被催（红色），只推进过一次偏薄（橙色）。 */
.fs-tag.fs-never {
  background: #fef0f0;
  color: #c45656;
  border-color: #fbc4c4;
}
.fs-tag.fs-thin {
  background: #fdf6ec;
  color: #b88230;
  border-color: #f5dab1;
}
.fs-tag.fs-ok {
  background: #f0f9eb;
  color: #67c23a;
  border-color: #c2e7b0;
}
.group-desc {
  font-size: 12px;
  color: #909399;
  margin: 0 0 12px;
}
.btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.empty-hint {
  font-size: 12px;
  color: #c0c4cc;
  padding: 4px 0;
}
.section-sub {
  font-size: 12px;
  color: #606266;
  margin: 8px 0 4px;
  font-weight: 500;
}
.tag-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.stat-tag {
  margin: 0;
}

/* 书稿 */
.current-project-card {
  background: #f5f7fa;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 10px;
}
.cp-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.cp-intro {
  font-size: 12px;
  color: #606266;
  margin-top: 4px;
  line-height: 1.5;
}
.cp-meta {
  font-size: 11px;
  color: #909399;
  margin-top: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

/* 项目详情 */
.kv-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  padding: 4px 0;
  border-bottom: 1px dashed #ebeef5;
}
.kv-row:last-child {
  border-bottom: none;
}
.k {
  color: #909399;
}
.v {
  color: #303133;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
}
.layer-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.vault-out {
  margin-top: 8px;
  font-size: 12px;
  color: #67c23a;
  word-break: break-all;
}
.vault-out code {
  background: #f0f9eb;
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
