<script setup lang="ts">
// 导入拆解：粘贴已有小说 → 预览切分 → 入库为正文 → 逐章拆解（实体+大纲，幂等可续跑）→ 汇总采纳大纲
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  importApi,
  type ImportPreviewChapter,
  type ImportStatus,
} from '../api'
import { currentProjectId } from '../state'

const rawText = ref('')
const preview = ref<ImportPreviewChapter[] | null>(null)
const importing = ref(false)
const importedVersion = ref<number | null>(null)

const status = ref<ImportStatus | null>(null)
const analyzing = ref(false)
const analyzeCurrent = ref('')
const analyzeLog = ref<string[]>([])
const adoptingOutline = ref(false)

const percent = computed(() =>
  status.value && status.value.total
    ? Math.round((status.value.done / status.value.total) * 100)
    : 0,
)

// 分页：拆解进度和切分预览各按 20/页切（数据全量持有，拆解扫描不受影响）
const statusPage = ref(1)
const previewPage = ref(1)
const tablePageSize = 20
const pagedStatusChapters = computed(() =>
  status.value
    ? status.value.chapters.slice(
        (statusPage.value - 1) * tablePageSize,
        statusPage.value * tablePageSize,
      )
    : [],
)
const pagedPreview = computed(() =>
  preview.value
    ? preview.value.slice((previewPage.value - 1) * tablePageSize, previewPage.value * tablePageSize)
    : [],
)

async function refreshStatus() {
  if (!currentProjectId.value) return
  try {
    status.value = await importApi.status(currentProjectId.value)
  } catch {
    status.value = null
  }
}

async function doPreview() {
  if (!currentProjectId.value) return
  if (!rawText.value.trim()) {
    ElMessage.warning('先粘贴小说文本')
    return
  }
  importing.value = true
  try {
    const r = await importApi.text(currentProjectId.value, rawText.value, true)
    preview.value = r.chapters
    ElMessage.success(`识别出 ${r.total} 个章节`)
  } catch (e) {
    ElMessage.error(`切分失败：${(e as Error).message}`)
  } finally {
    importing.value = false
  }
}

async function doImport() {
  if (!currentProjectId.value || !rawText.value.trim()) return
  importing.value = true
  try {
    const r = await importApi.text(currentProjectId.value, rawText.value, false)
    importedVersion.value = r.draft_version ?? null
    preview.value = r.chapters
    ElMessage.success(`已入库为正文 v${r.draft_version}（${r.total} 章）`)
    await refreshStatus()
  } catch (e) {
    ElMessage.error(`导入失败：${(e as Error).message}`)
  } finally {
    importing.value = false
  }
}

function onFilePick(ev: Event) {
  const file = (ev.target as HTMLInputElement).files?.[0]
  if (!file) return
  const reader = new FileReader()
  reader.onload = () => {
    rawText.value = String(reader.result ?? '')
    preview.value = null
  }
  reader.readAsText(file, 'utf-8')
}

async function doAnalyzeAll(force = false) {
  if (!currentProjectId.value || !status.value) return
  const pending = status.value.chapters
    .filter((c) => force || c.entities_status !== 'completed' || c.outline_status !== 'completed')
    .map((c) => c.no)
  if (!pending.length) {
    ElMessage.info('所有章节都已拆解完成（要重来请用「全部重拆」）')
    return
  }
  analyzing.value = true
  analyzeLog.value = []
  let ok = 0
  let failed = 0
  for (let i = 0; i < pending.length; i++) {
    const no = pending[i]
    analyzeCurrent.value = `第 ${no} 章（${i + 1}/${pending.length}）`
    try {
      const r = await importApi.analyzeChapter(currentProjectId.value, no, { force })
      const ent = r.entities
      const entMsg = ent.reused
        ? '实体复用'
        : `实体 +${ent.created?.length ?? 0}/~${ent.updated?.length ?? 0}`
      analyzeLog.value.unshift(`第${no}章：${entMsg}，大纲${r.outline_reused ? '复用' : '生成'}`)
      ok++
    } catch (e) {
      analyzeLog.value.unshift(`第${no}章：失败 — ${(e as Error).message}`)
      failed++
    }
    const s = await importApi.status(currentProjectId.value)
    status.value = s
  }
  analyzing.value = false
  analyzeCurrent.value = ''
  if (failed) ElMessage.warning(`拆解完成：成功 ${ok}，失败 ${failed}（失败章可重跑续接）`)
  else ElMessage.success(`拆解完成：${ok} 章`)
}

async function doAdoptOutline() {
  if (!currentProjectId.value) return
  adoptingOutline.value = true
  try {
    const r = await importApi.adoptOutline(currentProjectId.value)
    ElMessage.success(`已采纳大纲 v${r.seq}（汇总 ${r.chapters} 章）`)
  } catch (e) {
    ElMessage.error(`汇总失败：${(e as Error).message}`)
  } finally {
    adoptingOutline.value = false
  }
}

onMounted(refreshStatus)
watch(currentProjectId, () => {
  rawText.value = ''
  preview.value = null
  importedVersion.value = null
  analyzeLog.value = []
  statusPage.value = 1
  previewPage.value = 1
  refreshStatus()
})
</script>

<template>
  <!-- 单根包裹：v-show 依赖单根元素，多根时 display:none 不生效 -->
  <div>
    <div v-if="!currentProjectId">
      <el-empty description="先在顶部选择一个小说项目，导入的原文与拆解结果都归入该项目" />
    </div>
    <template v-else>
    <el-row :gutter="12">
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span style="font-weight: 600">1. 粘贴 / 载入已有小说</span>
              <label style="font-size: 12px; cursor: pointer; color: #409eff">
                从 .txt 文件载入
                <input type="file" accept=".txt" style="display: none" @change="onFilePick" />
              </label>
            </div>
          </template>
          <el-input
            v-model="rawText"
            type="textarea"
            :rows="16"
            placeholder="粘贴整本或若干章正文。支持「第一章 / 第12章 / 第一百二十三章」等章节标签自动切分。"
          />
          <div style="margin-top: 10px; display: flex; gap: 8px">
            <el-button :loading="importing" @click="doPreview">预览切分</el-button>
            <el-button type="primary" :loading="importing" @click="doImport">
              导入为正文层
            </el-button>
            <span v-if="importedVersion" style="font-size: 12px; color: #67c23a; align-self: center">
              已入库：正文 v{{ importedVersion }}
            </span>
          </div>
          <el-divider />
          <div style="font-size: 12px; color: #aaa">
            导入即「采纳」为正文层新版本（历史留痕可回滚）。切分只按行首「第N章」识别，
            没有章节标签的文本会整体作为一份处理。
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never">
          <template #header>
            <span style="font-weight: 600">2. 逐章拆解（实体抽取 + 反向大纲）</span>
          </template>
          <div v-if="status" style="margin-bottom: 10px">
            <el-progress
              :percentage="percent"
              :status="percent === 100 ? 'success' : undefined"
              :stroke-width="14"
            />
            <div style="font-size: 12px; color: #888; margin-top: 4px">
              共 {{ status.total }} 章，已完成 {{ status.done }} 章
              <span v-if="analyzing"> — 正在拆 {{ analyzeCurrent }}</span>
            </div>
          </div>
          <div style="display: flex; gap: 8px; margin-bottom: 10px">
            <el-button type="primary" :loading="analyzing" :disabled="!status?.total" @click="doAnalyzeAll(false)">
              {{ status?.done ? '续跑未完成章' : '开始拆解' }}
            </el-button>
            <el-button :loading="analyzing" :disabled="!status?.done" @click="doAnalyzeAll(true)">
              全部重拆
            </el-button>
            <el-button type="success" :loading="adoptingOutline" :disabled="!status?.done" @click="doAdoptOutline">
              汇总采纳大纲
            </el-button>
          </div>
          <el-table v-if="status?.chapters?.length" :data="pagedStatusChapters" size="small" max-height="360">
            <el-table-column prop="no" label="章" width="60" />
            <el-table-column prop="title" label="章名" width="140" show-overflow-tooltip />
            <el-table-column prop="chars" label="字数" width="80" />
            <el-table-column label="实体" width="80">
              <template #default="{ row }">
                <el-tag v-if="row.entities_status === 'completed'" size="small" type="success">完成</el-tag>
                <el-tag v-else-if="row.entities_status === 'failed'" size="small" type="danger">失败</el-tag>
                <span v-else style="color: #ccc">—</span>
              </template>
            </el-table-column>
            <el-table-column label="大纲" width="80">
              <template #default="{ row }">
                <el-tag v-if="row.outline_status === 'completed'" size="small" type="success">完成</el-tag>
                <el-tag v-else-if="row.outline_status === 'failed'" size="small" type="danger">失败</el-tag>
                <span v-else style="color: #ccc">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="outline_preview" label="大纲预览" show-overflow-tooltip />
          </el-table>
          <el-empty v-else description="导入正文后这里显示拆解进度" :image-size="60" />
          <el-pagination
            v-if="status && status.chapters.length > tablePageSize"
            v-model:current-page="statusPage"
            :page-size="tablePageSize"
            :total="status.chapters.length"
            layout="prev, pager, next"
            size="small"
            style="margin-top: 8px; justify-content: center"
          />
          <div v-if="analyzeLog.length" style="margin-top: 8px; max-height: 120px; overflow: auto; font-size: 12px; color: #888">
            <div v-for="(line, i) in analyzeLog" :key="i">{{ line }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>
    <el-card v-if="preview" shadow="never" style="margin-top: 12px">
      <template #header>
        <span style="font-weight: 600">切分预览（{{ preview.length }} 章）</span>
      </template>
      <el-table :data="pagedPreview" size="small" max-height="260">
        <el-table-column prop="no" label="章号" width="90">
          <template #default="{ row }">
            <span v-if="row.no === 0" style="color: #d90">前言/全文</span>
            <span v-else>第 {{ row.no }} 章</span>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="章名" />
        <el-table-column prop="chars" label="字数" width="120" />
      </el-table>
      <el-pagination
        v-if="preview && preview.length > tablePageSize"
        v-model:current-page="previewPage"
        :page-size="tablePageSize"
        :total="preview.length"
        layout="prev, pager, next, total"
        size="small"
        style="margin-top: 8px; justify-content: flex-end"
      />
    </el-card>
    </template>
  </div>
</template>
