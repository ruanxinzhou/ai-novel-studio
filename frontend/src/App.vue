<script setup lang="ts">
import { ref } from 'vue'
import CorpusView from './views/CorpusView.vue'
import PipelineView from './views/PipelineView.vue'
import SkillsView from './views/SkillsView.vue'
import WorkshopView from './views/WorkshopView.vue'

const tab = ref('pipeline')
</script>

<template>
  <el-container style="max-width: 1400px; margin: 0 auto; padding: 16px">
    <el-header style="height: auto; padding: 0 0 12px">
      <div style="display: flex; align-items: baseline; gap: 16px">
        <h1 style="margin: 0; font-size: 22px">Novel Studio</h1>
        <span style="color: #888; font-size: 13px">人工监督分步创作 · 大纲 → 剧情 → 正文</span>
      </div>
      <el-tabs v-model="tab" style="margin-top: 8px">
        <el-tab-pane label="运行台" name="pipeline" />
        <el-tab-pane label="Prompt 工坊" name="workshop" />
        <el-tab-pane label="写作模块" name="skills" />
        <el-tab-pane label="范文语料" name="corpus" />
      </el-tabs>
    </el-header>
    <el-main style="padding: 0">
      <PipelineView v-show="tab === 'pipeline'" />
      <WorkshopView v-show="tab === 'workshop'" />
      <SkillsView v-show="tab === 'skills'" />
      <CorpusView v-show="tab === 'corpus'" />
    </el-main>
  </el-container>
</template>
