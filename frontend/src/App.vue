<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CorpusView from './views/CorpusView.vue'
import EntitiesView from './views/EntitiesView.vue'
import MarkersView from './views/MarkersView.vue'
import PipelineView from './views/PipelineView.vue'
import ProjectView from './views/ProjectView.vue'
import SkillsView from './views/SkillsView.vue'
import WorkshopView from './views/WorkshopView.vue'
import { projectsApi, type ProjectMeta } from './api'
import { currentProjectId } from './state'

const tab = ref('project')
const projects = ref<ProjectMeta[]>([])
const newTitle = ref('')
const newIntro = ref('')
const creating = ref(false)

async function refreshProjects() {
  try {
    projects.value = (await projectsApi.list()).projects
    if (currentProjectId.value && !projects.value.some((p) => p.id === currentProjectId.value)) {
      currentProjectId.value = ''
    }
  } catch (e) {
    ElMessage.error(`项目列表加载失败：${(e as Error).message}`)
  }
}
onMounted(refreshProjects)

async function createProject() {
  if (!newTitle.value.trim()) {
    ElMessage.warning('请填写书名')
    return
  }
  creating.value = true
  try {
    const meta = await projectsApi.create(newTitle.value, newIntro.value)
    currentProjectId.value = meta.id
    newTitle.value = ''
    newIntro.value = ''
    ElMessage.success(`已创建《${meta.title}》`)
    await refreshProjects()
  } catch (e) {
    ElMessage.error(`创建失败：${(e as Error).message}`)
  } finally {
    creating.value = false
  }
}

async function removeProject() {
  const p = projects.value.find((x) => x.id === currentProjectId.value)
  if (!p) return
  try {
    await ElMessageBox.confirm(
      `删除《${p.title}》及其全部版本、标记？此操作不可恢复。`, '删除项目',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await projectsApi.remove(p.id)
    currentProjectId.value = ''
    ElMessage.success('已删除')
    await refreshProjects()
  } catch (e) {
    ElMessage.error(`删除失败：${(e as Error).message}`)
  }
}
</script>

<template>
  <el-container style="max-width: 1400px; margin: 0 auto; padding: 16px">
    <el-header style="height: auto; padding: 0 0 12px">
      <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap">
        <h1 style="margin: 0; font-size: 22px">Novel Studio</h1>
        <el-select
          v-model="currentProjectId"
          placeholder="选择小说项目"
          style="width: 240px"
          clearable
          filterable
        >
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="`《${p.title}》`" />
        </el-select>
        <el-popover width="360" trigger="click">
          <template #reference>
            <el-button type="primary" plain>新建项目</el-button>
          </template>
          <el-form label-width="60px" label-position="left">
            <el-form-item label="书名">
              <el-input v-model="newTitle" placeholder="如：凡人修仙传" @keyup.enter="createProject" />
            </el-form-item>
            <el-form-item label="点子">
              <el-input v-model="newIntro" type="textarea" :rows="2" placeholder="一句话点子（可空，之后在简介层生成）" />
            </el-form-item>
            <el-button type="primary" :loading="creating" style="width: 100%" @click="createProject">创建</el-button>
          </el-form>
        </el-popover>
        <el-button v-if="currentProjectId" type="danger" plain size="small" @click="removeProject">
          删除当前项目
        </el-button>
        <span style="color: #888; font-size: 13px">人工监督分步创作 · 大纲 → 剧情 → 正文</span>
      </div>
      <el-tabs v-model="tab" style="margin-top: 8px">
        <el-tab-pane label="项目" name="project" />
        <el-tab-pane label="实体库" name="entities" />
        <el-tab-pane label="导入拆解" name="import" />
        <el-tab-pane label="运行台" name="pipeline" />
        <el-tab-pane label="审稿与标记" name="markers" />
        <el-tab-pane label="Prompt 工坊" name="workshop" />
        <el-tab-pane label="写作模块" name="skills" />
        <el-tab-pane label="范文语料" name="corpus" />
      </el-tabs>
    </el-header>
    <el-main style="padding: 0">
      <ProjectView v-show="tab === 'project'" />
      <EntitiesView v-show="tab === 'entities'" />
      <ImportView v-show="tab === 'import'" />
      <PipelineView v-show="tab === 'pipeline'" />
      <MarkersView v-show="tab === 'markers'" />
      <WorkshopView v-show="tab === 'workshop'" />
      <SkillsView v-show="tab === 'skills'" />
      <CorpusView v-show="tab === 'corpus'" />
    </el-main>
  </el-container>
</template>
