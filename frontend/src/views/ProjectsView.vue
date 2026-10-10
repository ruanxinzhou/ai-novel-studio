<script setup lang="ts">
// 项目管理页：所有小说项目的卡片总览——进入/切换、新建、编辑、删除
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  LAYER_LABELS,
  LAYERS,
  STATION_LABELS,
  projectsApi,
  type ProjectMeta,
} from '../api'
import { activeTab, currentProjectId } from '../state'

const projects = ref<ProjectMeta[]>([])
const loading = ref(false)

// 分页：项目卡片按 9 本/页切
const projectPage = ref(1)
const projectPageSize = 9
const paged = computed(() =>
  projects.value.slice((projectPage.value - 1) * projectPageSize, projectPage.value * projectPageSize),
)

const createVisible = ref(false)
const createForm = ref({ title: '', intro: '' })
const creating = ref(false)

const editVisible = ref(false)
const editId = ref('')
const editForm = ref({ title: '', intro: '' })
const editing = ref(false)

async function refresh() {
  loading.value = true
  try {
    projects.value = (await projectsApi.list()).projects
    if (currentProjectId.value && !projects.value.some((p) => p.id === currentProjectId.value)) {
      currentProjectId.value = ''
    }
  } catch (e) {
    ElMessage.error(`项目列表加载失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

function enter(p: ProjectMeta) {
  currentProjectId.value = p.id
  // 进入即达主路：未毕业送去向导，已毕业直达章台
  activeTab.value = p.wizard_summary?.graduated ? 'desk' : 'wizard'
  ElMessage.success(`当前项目：《${p.title}》`)
}

async function create() {
  if (!createForm.value.title.trim()) {
    ElMessage.warning('请填写书名')
    return
  }
  creating.value = true
  try {
    const meta = await projectsApi.create(createForm.value.title, createForm.value.intro)
    currentProjectId.value = meta.id
    createVisible.value = false
    createForm.value = { title: '', intro: '' }
    ElMessage.success(`已创建《${meta.title}》，可进入开始创作`)
    await refresh()
  } catch (e) {
    ElMessage.error(`创建失败：${(e as Error).message}`)
  } finally {
    creating.value = false
  }
}

function openEdit(p: ProjectMeta) {
  editId.value = p.id
  editForm.value = { title: p.title, intro: p.intro }
  editVisible.value = true
}

async function saveEdit() {
  if (!editForm.value.title.trim()) {
    ElMessage.warning('书名不能为空')
    return
  }
  editing.value = true
  try {
    await projectsApi.update(editId.value, editForm.value.title, editForm.value.intro)
    editVisible.value = false
    ElMessage.success('已保存')
    await refresh()
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    editing.value = false
  }
}

async function remove(p: ProjectMeta) {
  try {
    await ElMessageBox.confirm(
      `删除《${p.title}》？该项目下的全部层版本、实体、标记、拆解收据将一并删除，此操作不可恢复。`,
      '删除项目',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await projectsApi.remove(p.id)
    if (currentProjectId.value === p.id) currentProjectId.value = ''
    ElMessage.success(`已删除《${p.title}》`)
    await refresh()
  } catch (e) {
    ElMessage.error(`删除失败：${(e as Error).message}`)
  }
}

onMounted(refresh)
</script>

<template>
  <div>
    <el-card shadow="never">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span style="font-weight: 600">项目管理（{{ projects.length }} 本）</span>
          <el-button type="primary" @click="createVisible = true">新建项目</el-button>
        </div>
      </template>

      <div v-if="!projects.length && !loading" style="text-align: center; padding: 40px 0">
        <el-empty description="还没有小说项目，点右上角「新建项目」开一本新书" />
      </div>

      <el-row v-else :gutter="12">
        <el-col v-for="p in paged" :key="p.id" :xs="24" :sm="12" :md="8" style="margin-bottom: 12px">
          <el-card
            shadow="hover"
            :body-style="{ padding: '14px' }"
            :style="p.id === currentProjectId ? 'border: 2px solid #409eff' : ''"
          >
            <div style="display: flex; justify-content: space-between; align-items: center">
              <b style="font-size: 15px">《{{ p.title }}》</b>
              <span style="display: flex; gap: 4px; align-items: center">
                <el-tag v-if="p.kind === 'reference'" size="small" type="warning">参考书</el-tag>
                <el-tag v-if="p.id === currentProjectId" size="small" type="primary">当前</el-tag>
              </span>
            </div>
            <div
              v-if="p.intro"
              style="font-size: 12px; color: #888; margin: 6px 0; min-height: 32px; line-height: 1.4"
            >
              {{ p.intro.length > 60 ? p.intro.slice(0, 60) + '…' : p.intro }}
            </div>
            <div v-else style="min-height: 32px; margin: 6px 0"></div>
            <div style="margin: 6px 0; font-size: 12px; min-height: 24px">
              <template v-if="p.kind === 'reference'">
                <el-tag type="warning" size="small" effect="plain">拆书参考</el-tag>
                <span style="margin-left: 8px; color: #666">
                  {{ p.current?.intro ? '节奏体检 + 全书脉络已入库' : '待导入' }}，仅借鉴不写作
                </span>
              </template>
              <template v-else-if="p.wizard_summary?.graduated">
                <el-tag type="success" size="small">已开书</el-tag>
                <span style="margin-left: 8px; color: #666">
                  章台 {{ p.wizard_summary.chapters_finalized }} / {{ p.wizard_summary.chapters_total }} 章定稿
                </span>
              </template>
              <template v-else-if="p.wizard_summary">
                <el-tag size="small" type="info">开书向导 {{ p.wizard_summary.done }}/6 站</el-tag>
                <span v-if="p.wizard_summary.current_station" style="margin-left: 8px; color: #bbb">
                  当前：{{ STATION_LABELS[p.wizard_summary.current_station] ?? p.wizard_summary.current_station }}
                </span>
              </template>
              <template v-else>
                <span v-for="l in LAYERS" :key="l" style="margin-right: 10px">
                  <template v-if="p.current?.[l]">
                    <span style="color: #67c23a">●</span>
                    {{ LAYER_LABELS[l] }} v{{ p.current[l] }}
                  </template>
                  <template v-else>
                    <span style="color: #dcdfe6">●</span>
                    <span style="color: #bbb">{{ LAYER_LABELS[l] }}</span>
                  </template>
                </span>
              </template>
            </div>
            <div style="font-size: 12px; color: #aaa; margin-bottom: 10px">
              更新于 {{ p.updated_at || '—' }}
            </div>
            <div style="display: flex; gap: 6px">
              <el-button
                size="small"
                :type="p.id === currentProjectId ? 'success' : 'primary'"
                :disabled="p.id === currentProjectId"
                @click="enter(p)"
              >
                {{ p.id === currentProjectId ? '进行中' : '进入' }}
              </el-button>
              <el-button size="small" plain @click="openEdit(p)">编辑</el-button>
              <el-button size="small" plain type="danger" @click="remove(p)">删除</el-button>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-pagination
        v-if="projects.length > projectPageSize"
        v-model:current-page="projectPage"
        :page-size="projectPageSize"
        :total="projects.length"
        layout="prev, pager, next, total"
        size="small"
        style="margin-top: 4px; justify-content: flex-end"
      />

      <div style="font-size: 12px; color: #aaa; margin-top: 8px">
        推荐流程：新建项目 → 「创作导航」里逐层生成并采纳（简介→大纲→剧情→正文→润色）→
        「实体库」维护角色/道具/伏笔 → 「项目」页导出 Obsidian 图谱。
        已有完本/连载？直接进「导入拆解」导入原文反向拆解。
      </div>
    </el-card>

    <!-- 新建 -->
    <el-dialog v-model="createVisible" title="新建小说项目" width="480">
      <el-form label-width="60px" label-position="left">
        <el-form-item label="书名">
          <el-input v-model="createForm.title" placeholder="如：凡人修仙传" @keyup.enter="create" />
        </el-form-item>
        <el-form-item label="点子">
          <el-input
            v-model="createForm.intro"
            type="textarea"
            :rows="3"
            placeholder="一句话点子（可空，之后在创作导航的简介层生成）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="create">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑 -->
    <el-dialog v-model="editVisible" title="编辑项目信息" width="480">
      <el-form label-width="60px" label-position="left">
        <el-form-item label="书名">
          <el-input v-model="editForm.title" @keyup.enter="saveEdit" />
        </el-form-item>
        <el-form-item label="点子">
          <el-input v-model="editForm.intro" type="textarea" :rows="3" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editing" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
