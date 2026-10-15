<script setup lang="ts">
import { computed, ref } from 'vue'
import { drawerOpen } from '../state'

const open = computed({
  get: () => drawerOpen.value,
  set: (v: boolean) => (drawerOpen.value = v),
})

const activeNames = ref(['manuscript'])

const groups = [
  { key: 'manuscript', icon: '📖', title: '书稿', desc: '书卡、卷章、终局等核心创作配置' },
  { key: 'entities', icon: '🗂', title: '实体库', desc: '角色、地点、道具、伏笔管理' },
  { key: 'materials', icon: '📚', title: '资料', desc: '语料、参考书、素材库' },
  { key: 'craft', icon: '📝', title: '写作规范', desc: '写作宪法、风格指南、模块规则' },
  { key: 'advanced', icon: '🧪', title: '高级', desc: 'Prompt 工坊、运行台、导入导出' },
  { key: 'project', icon: '📂', title: '项目', desc: '项目管理、Vault 导出、项目删除' },
]
</script>

<template>
  <el-drawer v-model="open" title="设置" direction="rtl" size="380px">
    <el-collapse v-model="activeNames">
      <el-collapse-item
        v-for="g in groups"
        :key="g.key"
        :name="g.key"
        :title="`${g.icon} ${g.title}`"
      >
        <p class="group-desc">{{ g.desc }}</p>
        <el-empty description="内容迁移中" :image-size="60" />
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
</style>
