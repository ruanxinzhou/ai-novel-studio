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
  <el-drawer v-model="open" title="设置与工具" direction="rtl" size="400px">
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
          <el-button size="small" text disabled>完整实体库（Task 14 后开放）</el-button>
        </div>
        <el-table
          v-loading="loading.entities"
          :data="entities"
          size="small"
          stripe
          style="width: 100%"
          max-height="240"
        >
          <el-table-column prop="name" label="名称" min-width="100" show-overflow-tooltip />
          <el-table-column label="类型" width="70">
            <template #default="{ row }">
              <el-tag size="small" type="info">{{ ENTITY_TYPE_LABELS[row.type] ?? row.type }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="70">
            <template #default="{ row }">
              <el-tag size="small">{{ ENTITY_STATUS_LABELS[row.status] ?? row.status }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
        <div class="empty-hint" v-if="!loading.entities && entities.length === 0">暂无实体</div>
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
          <el-tooltip content="技能编辑器将在 Task 14 从旧 WorkshopView 迁移" placement="top">
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
</template>

<style scoped>
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
