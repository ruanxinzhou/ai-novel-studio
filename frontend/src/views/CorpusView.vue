<script setup lang="ts">
// 范文语料检索：类型/标签/关键词，用于写作前借结构
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { corpusApi, type Excerpt } from '../api'

const types = ref<{ type: string; count: number }[]>([])
const tags = ref<{ tag: string; count: number }[]>([])
const qType = ref('')
const qTag = ref('')
const qKeyword = ref('')
const hits = ref<Excerpt[]>([])
const total = ref(0)
const searching = ref(false)

onMounted(async () => {
  try {
    const [t, g] = await Promise.all([corpusApi.types(), corpusApi.tags()])
    types.value = t.types
    tags.value = g.tags
    await doSearch()
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
})

async function doSearch() {
  searching.value = true
  try {
    const r = await corpusApi.search({
      excerpt_type: qType.value || undefined,
      tag: qTag.value || undefined,
      keyword: qKeyword.value || undefined,
      limit: 10,
    })
    hits.value = r.excerpts
    total.value = r.total
  } catch (e) {
    ElMessage.error(`检索失败：${(e as Error).message}`)
  } finally {
    searching.value = false
  }
}
</script>

<template>
  <el-card shadow="never">
    <el-form inline>
      <el-form-item label="类型">
        <el-select v-model="qType" clearable filterable placeholder="全部" style="width: 180px">
          <el-option v-for="t in types" :key="t.type" :value="t.type" :label="`${t.type} (${t.count})`" />
        </el-select>
      </el-form-item>
      <el-form-item label="标签">
        <el-select v-model="qTag" clearable filterable placeholder="全部" style="width: 180px">
          <el-option v-for="t in tags" :key="t.tag" :value="t.tag" :label="`${t.tag} (${t.count})`" />
        </el-select>
      </el-form-item>
      <el-form-item label="关键词">
        <el-input v-model="qKeyword" clearable placeholder="标题/正文子串" style="width: 180px" @keyup.enter="doSearch" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="searching" @click="doSearch">检索</el-button>
      </el-form-item>
    </el-form>

    <el-divider />
    <div style="color: #888; font-size: 13px; margin-bottom: 12px">
      命中 {{ total }} 条（显示前 {{ hits.length }} 条）——运行台可勾选注入作为参考资料
    </div>
    <el-empty v-if="!hits.length" description="无命中" />
    <el-collapse v-else>
      <el-collapse-item v-for="h in hits" :key="h.excerpt_id">
        <template #title>
          <el-tag size="small" style="margin-right: 8px">{{ h.excerpt_type }}</el-tag>
          <span>《{{ h.title }}》</span>
          <el-tag v-for="t in h.tags.slice(0, 4)" :key="t" size="small" type="info" style="margin-left: 6px">{{ t }}</el-tag>
        </template>
        <pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.9">{{ h.text }}</pre>
      </el-collapse-item>
    </el-collapse>
  </el-card>
</template>
