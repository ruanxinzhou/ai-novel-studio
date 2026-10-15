<script setup lang="ts">
import { onMounted, ref } from 'vue'
import HomeView from './views/HomeView.vue'
import { currentProjectId } from './state'

interface ProjectMeta {
  id: string
  title: string
  intro: string
  created_at: string
  updated_at: string
}

const projects = ref<ProjectMeta[]>([])

async function refreshProjects() {
  try {
    const r = await fetch('/api/projects')
    const d = await r.json()
    // 兼容后端返回 {projects:[...]} 或直接数组
    projects.value = d.projects || (Array.isArray(d) ? d : [])
    if (currentProjectId.value && !projects.value.some((p) => p.id === currentProjectId.value)) {
      currentProjectId.value = ''
    }
  } catch {
    projects.value = []
  }
}

onMounted(() => {
  refreshProjects()
})
</script>

<template>
  <!-- 项目选择门 -->
  <div v-if="!currentProjectId" class="project-gate">
    <div class="gate-card">
      <h1 class="gate-title">《第七日的亡者》· 写作间</h1>
      <p class="gate-sub">选择一个小说项目开始工作</p>
      <el-select
        v-model="currentProjectId"
        placeholder="选小说项目"
        style="width: 320px"
        size="large"
        filterable
      >
        <el-option
          v-for="p in projects"
          :key="p.id"
          :value="p.id"
          :label="`《${p.title}》  (${p.id})`"
        />
      </el-select>
      <p class="gate-hint" v-if="projects.length === 0">暂无项目，请先创建项目。</p>
    </div>
  </div>

  <!-- 主界面 -->
  <HomeView v-else />
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
  padding: 0;
}
#app {
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  color: #222;
}
</style>

<style scoped>
.project-gate {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f5f7fa 0%, #e4ecfb 100%);
}
.gate-card {
  background: #fff;
  padding: 48px 56px;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.08);
  text-align: center;
}
.gate-title {
  margin: 0 0 8px;
  font-size: 24px;
  color: #303133;
}
.gate-sub {
  margin: 0 0 24px;
  color: #909399;
  font-size: 14px;
}
.gate-hint {
  margin: 16px 0 0;
  font-size: 12px;
  color: #c0c4cc;
}
</style>
