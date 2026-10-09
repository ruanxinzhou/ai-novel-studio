<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import CorpusView from './views/CorpusView.vue'
import EntitiesView from './views/EntitiesView.vue'
import ImportView from './views/ImportView.vue'
import MarkersView from './views/MarkersView.vue'
import PipelineView from './views/PipelineView.vue'
import ProjectsView from './views/ProjectsView.vue'
import ProjectView from './views/ProjectView.vue'
import SkillsView from './views/SkillsView.vue'
import WorkshopView from './views/WorkshopView.vue'
import { projectsApi, type ProjectMeta } from './api'
import { currentProjectId } from './state'

const tab = ref('projects')
const projects = ref<ProjectMeta[]>([])

async function refreshProjects() {
  try {
    projects.value = (await projectsApi.list()).projects
    if (currentProjectId.value && !projects.value.some((p) => p.id === currentProjectId.value)) {
      currentProjectId.value = ''
    }
  } catch {
    projects.value = []
  }
}
onMounted(refreshProjects)
// 管理页新建/删除/进入后，顶部下拉同步刷新
watch(currentProjectId, () => refreshProjects())
</script>

<template>
  <el-container style="max-width: 1400px; margin: 0 auto; padding: 16px">
    <el-header style="height: auto; padding: 0 0 12px">
      <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap">
        <h1 style="margin: 0; font-size: 22px">Novel Studio</h1>
        <el-select
          v-model="currentProjectId"
          placeholder="快速切换小说项目"
          style="width: 240px"
          clearable
          filterable
        >
          <el-option v-for="p in projects" :key="p.id" :value="p.id" :label="`《${p.title}》`" />
        </el-select>
        <span style="color: #888; font-size: 13px">人工监督分步创作 · 大纲 → 剧情 → 正文</span>
      </div>
      <el-tabs v-model="tab" style="margin-top: 8px">
        <el-tab-pane label="项目管理" name="projects" />
        <el-tab-pane label="创作导航" name="project" />
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
      <ProjectsView v-show="tab === 'projects'" />
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
