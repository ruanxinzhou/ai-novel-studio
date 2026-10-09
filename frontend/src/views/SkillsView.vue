<script setup lang="ts">
// 写作模块库：查看规则全文与层默认集
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { LAYER_LABELS, skillsApi, type SkillModule } from '../api'

const modules = ref<SkillModule[]>([])
const activeName = ref('')
const rules = ref('')
const loadingRule = ref(false)

onMounted(async () => {
  try {
    modules.value = (await skillsApi.list()).modules
    if (modules.value.length) await show(modules.value[0].name)
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
})

async function show(name: string) {
  activeName.value = name
  loadingRule.value = true
  try {
    rules.value = (await skillsApi.detail(name)).rules
  } catch (e) {
    ElMessage.error(`加载规则失败：${(e as Error).message}`)
  } finally {
    loadingRule.value = false
  }
}
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="8">
      <el-card shadow="never">
        <template #header>模块（{{ modules.length }}）</template>
        <div v-for="m in modules" :key="m.name" class="mod-item" :class="{ active: m.name === activeName }" @click="show(m.name)">
          <div>
            <b>{{ m.name }}</b>
            <el-tag v-for="d in m.default_for" :key="d" size="small" type="success" style="margin-left: 6px">
              {{ LAYER_LABELS[d] ?? d }}默认
            </el-tag>
          </div>
          <div style="font-size: 12px; color: #888; margin-top: 4px">{{ m.title }}</div>
          <div style="font-size: 12px; color: #aaa; margin-top: 2px">
            适用：{{ m.stages.map((s) => LAYER_LABELS[s] ?? s).join(' / ') }} · {{ Math.round(m.rules_bytes / 1024) }}KB
          </div>
        </div>
      </el-card>
    </el-col>
    <el-col :span="16">
      <el-card shadow="never" v-loading="loadingRule">
        <template #header>
          <span>{{ activeName }} 运行规则</span>
          <span style="color: #888; font-size: 12px; margin-left: 12px">
            运行时作为 REQUIRED 材料整体进入上下文；层默认集可在运行台调整
          </span>
        </template>
        <pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.8">{{ rules }}</pre>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.mod-item {
  padding: 10px 8px;
  border-radius: 6px;
  cursor: pointer;
}
.mod-item:hover {
  background: #f5f7fa;
}
.mod-item.active {
  background: #ecf5ff;
}
</style>
