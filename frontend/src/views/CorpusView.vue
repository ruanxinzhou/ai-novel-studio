<script setup lang="ts">
// 范文语料检索：类型/标签/关键词，用于写作前借结构
import { onMounted, reactive, ref } from 'vue'
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
const page = ref(1)
const pageSize = 10

// 导入拆书目录（tools/chaishu 产物）
const impVisible = ref(false)
const impLoading = ref(false)
const impForm = reactive({ dir: '', title: '', tags: '', sourceTxt: '' })
const impResult = ref<{
  added: number
  by_type: Record<string, number>
  title: string
  chapters_total: number
  labeled: number
  entities?: Record<string, number>
  entities_preextracted?: boolean
} | null>(null)

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

async function doSearch(p = 1) {
  searching.value = true
  try {
    const r = await corpusApi.search(
      {
        excerpt_type: qType.value || undefined,
        tag: qTag.value || undefined,
        keyword: qKeyword.value || undefined,
      },
      p,
      pageSize,
    )
    hits.value = r.excerpts
    total.value = r.total ?? r.excerpts.length
    page.value = r.page ?? p
  } catch (e) {
    ElMessage.error(`检索失败：${(e as Error).message}`)
  } finally {
    searching.value = false
  }
}

async function doImport() {
  if (!impForm.dir.trim()) {
    ElMessage.warning('请填写拆书工具的输出目录')
    return
  }
  impLoading.value = true
  impResult.value = null
  try {
    const extra = impForm.tags.split(/[,，\s]+/).filter(Boolean)
    const r = await corpusApi.importChaishu(
      impForm.dir.trim(), impForm.title.trim(), extra, impForm.sourceTxt.trim())
    impResult.value = r
    ElMessage.success(`已导入《${r.title}》${r.added} 条`)
    const [t, g] = await Promise.all([corpusApi.types(), corpusApi.tags()])
    types.value = t.types
    tags.value = g.tags
    await doSearch(1)
  } catch (e) {
    ElMessage.error(`导入失败：${(e as Error).message}`)
  } finally {
    impLoading.value = false
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
        <el-input v-model="qKeyword" clearable placeholder="标题/正文子串" style="width: 180px" @keyup.enter="doSearch()" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="searching" @click="doSearch()">检索</el-button>
        <el-button @click="impResult = null; impVisible = true">导入拆书目录</el-button>
      </el-form-item>
    </el-form>

    <el-divider />
    <div style="color: #888; font-size: 13px; margin-bottom: 12px">
      命中 {{ total }} 条，第 {{ page }}/{{ Math.max(1, Math.ceil(total / pageSize)) }} 页——运行台可勾选注入作为参考资料
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
    <div v-if="total > pageSize" style="margin-top: 14px; display: flex; justify-content: center">
      <el-pagination
        layout="prev, pager, next, jumper"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="(p: number) => doSearch(p)"
      />
    </div>

    <el-dialog v-model="impVisible" title="导入拆书目录" width="580px">
      <div style="color: #888; font-size: 12px; line-height: 1.8; margin-bottom: 10px">
        导入独立拆书工具（tools/chaishu）的输出目录：生成三类语料条目 + 参考项目（简介/大纲/剧情/正文四层 + 实体库），
        与你的小说项目平行，互相可作范文。同书重复导入 = 覆盖更新。
      </div>
      <el-form label-width="80px">
        <el-form-item label="目录路径" required>
          <el-input v-model="impForm.dir" placeholder="拆书 CLI 的输出目录，如 C:\Users\你\Downloads\拆书-书名" />
        </el-form-item>
        <el-form-item label="原书 txt">
          <el-input v-model="impForm.sourceTxt" placeholder="可选；旧版底料无正文时按它补齐（新版工具已自带正文）" />
        </el-form-item>
        <el-form-item label="书名">
          <el-input v-model="impForm.title" placeholder="留空则从目录名「拆书-」之后推断" />
        </el-form-item>
        <el-form-item label="附加标签">
          <el-input v-model="impForm.tags" placeholder="逗号分隔，可选，如：克苏鲁, 无限流" />
        </el-form-item>
      </el-form>
      <el-alert v-if="impResult" type="success" :closable="false">
        <template #title>
          《{{ impResult.title }}》共导入 {{ impResult.added }} 条（已标注 {{ impResult.labeled }}/{{ impResult.chapters_total }} 章）
        </template>
        <el-tag v-for="(n, t) in impResult.by_type" :key="t" size="small" style="margin: 4px 6px 0 0">{{ t }} ×{{ n }}</el-tag>
        <div v-if="impResult.entities" style="margin-top: 6px; font-size: 12px">
          实体库：{{ impResult.entities.character || 0 }} 角色 / {{ impResult.entities.location || 0 }} 地点 /
          {{ impResult.entities.item || 0 }} 道具 / {{ impResult.entities.foreshadow || 0 }} 伏笔
        </div>
      </el-alert>
      <el-alert v-if="impResult && impResult.entities_preextracted === false" type="warning" :closable="false"
        title="产物中无 entities.jsonl：实体库只导入了伏笔。重跑拆书工具（默认已含实体层）后重新导入即可补齐角色/地点/道具"
        style="margin-top: 8px" />
      <template #footer>
        <el-button @click="impVisible = false">关闭</el-button>
        <el-button type="primary" :loading="impLoading" @click="doImport">导入</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>
