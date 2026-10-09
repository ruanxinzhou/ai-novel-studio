<script setup lang="ts">
// 层运行台：选层 → 输入 x → 勾模块 → 检索范文 → 运行 → 结果与断点收据
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  LAYER_LABELS,
  LAYERS,
  corpusApi,
  markersApi,
  projectsApi,
  reviewApi,
  runsApi,
  skillsApi,
  type ChapterReview,
  type Excerpt,
  type LayerRunResult,
  type SkillModule,
} from '../api'
import { currentProjectId } from '../state'

const layer = ref<string>('draft')
const userDirective = ref('')
const xLines = ref('') // 每行一个 x 块
const xChunkLength = ref<number | null>(null)
const chapterNo = ref<number | null>(null) // 提供时注入该章必现标记
const running = ref(false)
const result = ref<LayerRunResult | null>(null)
const reviewing = ref(false)
const review = ref<ChapterReview | null>(null)
const adoptNote = ref('')
const adopting = ref(false)

const modules = ref<SkillModule[]>([])
const selectedModules = ref<string[]>([])
const allExcerptTypes = ref<{ type: string; count: number }[]>([])
const corpusType = ref('')
const corpusTag = ref('')
const corpusKeyword = ref('')
const corpusHits = ref<Excerpt[]>([])
const injectedExcerpts = ref<Excerpt[]>([])

const defaultsForLayer = computed(() =>
  modules.value.filter((m) => m.default_for.includes(layer.value)).map((m) => m.name),
)

onMounted(async () => {
  try {
    const [mods, types] = await Promise.all([skillsApi.list(), corpusApi.types()])
    modules.value = mods.modules
    allExcerptTypes.value = types.types
    syncDefaults()
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
})

function syncDefaults() {
  selectedModules.value = [...defaultsForLayer.value]
}

async function previewCorpus() {
  try {
    const r = await corpusApi.search({
      excerpt_type: corpusType.value || undefined,
      tag: corpusTag.value || undefined,
      keyword: corpusKeyword.value || undefined,
      limit: 5,
    })
    corpusHits.value = r.excerpts
  } catch (e) {
    ElMessage.error(`检索失败：${(e as Error).message}`)
  }
}

function buildXPairs(): [string, string][] {
  return xLines.value
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((x) => [x, ''] as [string, string])
}

const body = () => ({
  x_pairs: buildXPairs(),
  user_directive: userDirective.value || undefined,
  x_chunk_length: xChunkLength.value || undefined,
  modules: selectedModules.value.length ? selectedModules.value : undefined,
  corpus_query: injectedExcerpts.value.length
    ? {
        excerpt_type: corpusType.value || undefined,
        tag: corpusTag.value || undefined,
        keyword: corpusKeyword.value || undefined,
        limit: Math.max(1, injectedExcerpts.value.length),
      }
    : undefined,
  chapter_no: chapterNo.value || undefined,
  project: currentProjectId.value || undefined,
})

async function doRun(kind: 'run' | 'retry') {
  const pairs = buildXPairs()
  if (!pairs.length) {
    ElMessage.warning('请至少输入一行 x 内容（每行一个块）')
    return
  }
  running.value = true
  result.value = null
  review.value = null
  try {
    result.value = kind === 'run' ? await runsApi.run(layer.value, body()) : await runsApi.retry(layer.value, body())
    ElMessage.success(
      `完成：${result.value.journal.completed}/${result.value.journal.total} 块` +
        (result.value.resumed_indices.length
          ? `，复用收据 ${result.value.resumed_indices.length} 块`
          : ''),
    )
  } catch (e) {
    ElMessage.error(`运行失败：${(e as Error).message}`)
  } finally {
    running.value = false
  }
}

async function doReview() {
  if (!result.value?.y) {
    ElMessage.warning('先运行生成正文再审稿')
    return
  }
  reviewing.value = true
  review.value = null
  try {
    review.value = await reviewApi.chapter(xLines.value, result.value.y)
    if (review.value.verdict === 'blocked') {
      ElMessage.warning(`审稿不通过：${review.value.unstable_count} 项不稳，不能交稿`)
    } else {
      ElMessage.success('审稿通过')
    }
  } catch (e) {
    ElMessage.error(`审稿失败：${(e as Error).message}`)
  } finally {
    reviewing.value = false
  }
}

async function unstableToMarkers() {
  if (!review.value) return
  const problems = review.value.items
    .filter((i) => i.status === '不稳')
    .map((i) => `${i.name}：${i.problem || '未说明'}`)
  if (!problems.length) return
  try {
    const next = (chapterNo.value || 1) + 1
    await markersApi.fromReview(problems, next, currentProjectId.value || undefined)
    ElMessage.success(`已把 ${problems.length} 项不稳转成第 ${next} 章必现修复标记`)
  } catch (e) {
    ElMessage.error(`落位失败：${(e as Error).message}`)
  }
}

async function adoptToProject() {
  if (!result.value?.y) return
  if (!currentProjectId.value) {
    ElMessage.warning('先在顶部选择或新建小说项目')
    return
  }
  adopting.value = true
  try {
    await projectsApi.adopt(
      currentProjectId.value,
      layer.value,
      result.value.y,
      result.value.pairs,
      adoptNote.value || `运行 ${result.value.run_id.slice(0, 8)}`,
    )
    ElMessage.success(`已采纳到项目「${LAYER_LABELS[layer.value]}」层，新版本入库`)
  } catch (e) {
    ElMessage.error(`采纳失败：${(e as Error).message}`)
  } finally {
    adopting.value = false
  }
}
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="9">
      <el-card shadow="never">
        <template #header>运行配置</template>
        <el-form label-width="90px" label-position="left">
          <el-form-item label="层">
            <el-select v-model="layer" @change="syncDefaults">
              <el-option v-for="l in LAYERS" :key="l" :value="l" :label="`${LAYER_LABELS[l]} (${l})`" />
            </el-select>
          </el-form-item>
          <el-form-item label="x 块">
            <el-input
              v-model="xLines"
              type="textarea"
              :rows="8"
              placeholder="每行一个块，例如正文层每行一段剧情；大纲层每行「第1章」"
            />
          </el-form-item>
          <el-form-item label="块长度">
            <el-input-number v-model="xChunkLength" :min="0" :step="500" placeholder="默认用层规格" style="width: 100%" />
          </el-form-item>
          <el-form-item label="章号">
            <el-input-number v-model="chapterNo" :min="1" placeholder="选填" style="width: 100%" />
            <div style="font-size: 12px; color: #888">填写后自动注入该章待兑现必现标记</div>
          </el-form-item>
          <el-form-item label="作者指令">
            <el-input v-model="userDirective" type="textarea" :rows="2" placeholder="本节附加指令（可空）" />
          </el-form-item>
          <el-form-item label="写作模块">
            <el-select v-model="selectedModules" multiple clearable style="width: 100%" placeholder="空 = 用层默认集">
              <el-option v-for="m in modules" :key="m.name" :value="m.name" :label="`${m.name} — ${m.title}`" />
            </el-select>
          </el-form-item>
        </el-form>

        <el-divider>范文注入（参考资料）</el-divider>
        <el-form inline>
          <el-form-item>
            <el-select v-model="corpusType" clearable filterable placeholder="类型" style="width: 150px">
              <el-option v-for="t in allExcerptTypes" :key="t.type" :value="t.type" :label="`${t.type} (${t.count})`" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-input v-model="corpusTag" placeholder="标签" style="width: 120px" clearable />
          </el-form-item>
          <el-form-item>
            <el-input v-model="corpusKeyword" placeholder="关键词" style="width: 120px" clearable />
          </el-form-item>
          <el-form-item>
            <el-button @click="previewCorpus">检索预览</el-button>
          </el-form-item>
        </el-form>
        <div v-if="corpusHits.length" style="max-height: 220px; overflow: auto">
          <el-checkbox-group v-model="injectedExcerpts">
            <div v-for="h in corpusHits" :key="h.excerpt_id" style="margin-bottom: 6px">
              <el-checkbox :value="h">
                <span style="font-size: 12px">[{{ h.excerpt_type }}]《{{ h.title }}》</span>
              </el-checkbox>
              <div style="font-size: 12px; color: #888; margin-left: 24px; white-space: pre-wrap">{{ h.text.slice(0, 80) }}…</div>
            </div>
          </el-checkbox-group>
        </div>

        <el-button type="primary" :loading="running" style="width: 100%; margin-top: 12px" @click="doRun('run')">
          运行 {{ LAYER_LABELS[layer] }} 层
        </el-button>
        <el-button :loading="running" style="width: 100%; margin-top: 8px" @click="doRun('retry')">
          幂等重试（只补失败块）
        </el-button>
        <div v-if="result?.markers_injected?.length" style="margin-top: 10px">
          <el-alert type="warning" :closable="false" show-icon>
            <template #title>本章必现标记已注入（{{ result.markers_injected.length }} 条）</template>
            <div v-for="m in result.markers_injected" :key="m.id" style="font-size: 12px">
              · {{ m.content }}
            </div>
          </el-alert>
        </div>
      </el-card>
    </el-col>

    <el-col :span="15">
      <el-card v-if="result" shadow="never">
        <template #header>
          <div style="display: flex; gap: 12px; align-items: center">
            <span>运行结果 {{ result.run_id.slice(0, 8) }}</span>
            <el-tag size="small">块 {{ result.journal.completed }}/{{ result.journal.total }}</el-tag>
            <el-tag v-if="result.journal.failed" type="danger" size="small">失败 {{ result.journal.failed }}</el-tag>
            <el-tag v-if="result.resumed_indices.length" type="success" size="small">
              复用收据 {{ result.resumed_indices.length }}
            </el-tag>
            <el-tag v-for="s in result.skills_injected" :key="s" type="warning" size="small">{{ s }}</el-tag>
          </div>
        </template>
        <div style="margin-bottom: 10px; display: flex; gap: 8px; align-items: center">
          <el-input
            v-model="adoptNote"
            placeholder="版本备注（可空，如：调整了第3章节奏）"
            size="small"
            style="width: 280px"
          />
          <el-button type="success" :loading="adopting" @click="adoptToProject">
            采纳到当前项目
          </el-button>
          <span v-if="!currentProjectId" style="font-size: 12px; color: #c00">
            未选择项目，顶部新建/选择后可入库
          </span>
        </div>
        <el-tabs>
          <el-tab-pane label="成文">
            <pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.9">{{ result.y }}</pre>
          </el-tab-pane>
          <el-tab-pane :label="`分块 (${result.pairs.length})`">
            <el-collapse>
              <el-collapse-item v-for="(p, i) in result.pairs" :key="i" :title="`块 ${i + 1}（${p[1].length} 字）`">
                <div style="color: #888; font-size: 12px; margin-bottom: 6px">x: {{ p[0].slice(0, 120) }}…</div>
                <pre style="white-space: pre-wrap; font-family: inherit">{{ p[1] }}</pre>
              </el-collapse-item>
            </el-collapse>
          </el-tab-pane>
          <el-tab-pane :label="`收据 (${result.journal.receipts.length})`">
            <el-table :data="result.journal.receipts" size="small">
              <el-table-column prop="index" label="#" width="50" />
              <el-table-column prop="status" label="状态" width="90">
                <template #default="{ row }">
                  <el-tag :type="row.status === 'completed' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="completion_status" label="完成态" width="110" />
              <el-table-column prop="attempts" label="尝试" width="60" />
              <el-table-column prop="x_hash" label="x_hash" width="130" />
              <el-table-column prop="error" label="错误" show-overflow-tooltip />
            </el-table>
          </el-tab-pane>
          <el-tab-pane label="审稿">
            <div style="margin-bottom: 10px">
              <el-button type="primary" plain :loading="reviewing" @click="doReview">
                六项一致性审稿
              </el-button>
              <el-button v-if="review && review.unstable_count > 0" type="warning" @click="unstableToMarkers">
                不稳项转下章必现修复
              </el-button>
            </div>
            <template v-if="review">
              <el-alert
                :type="review.verdict === 'pass' ? 'success' : 'error'"
                :closable="false"
                show-icon
                style="margin-bottom: 10px"
              >
                <template #title>
                  {{ review.verdict === 'pass' ? '审稿通过，可交稿' : `审稿不通过：${review.unstable_count} 项不稳，不能交稿` }}
                </template>
                {{ review.summary }}
              </el-alert>
              <el-table :data="review.items" size="small">
                <el-table-column prop="name" label="检查项" width="110" />
                <el-table-column prop="status" label="结论" width="80">
                  <template #default="{ row }">
                    <el-tag :type="row.status === '稳' ? 'success' : 'danger'" size="small">{{ row.status }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="problem" label="问题与证据" show-overflow-tooltip />
              </el-table>
            </template>
            <el-empty v-else description="点击「六项一致性审稿」对当前成文做逐项审查（不稳 ≥2 不交稿）" />
          </el-tab-pane>
        </el-tabs>
      </el-card>
      <el-empty v-else description="配置左侧参数后运行；同输入重跑会命中断点收据，不重复调用 LLM" />
    </el-col>
  </el-row>
</template>
