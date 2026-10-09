<script setup lang="ts">
// 今日章台：连载期日更微循环——剧情 → 正文 → 审稿 → 润色 → 定稿（保鲜飞轮）
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deskApi,
  refineApi,
  type ChapterReview,
  type DeskChapterDetail,
  type DeskDetail,
  type FinalizeResult,
} from '../api'
import { activeTab, currentProjectId } from '../state'

const loading = ref(false)
const overview = ref<DeskDetail | null>(null)
const currentNo = ref(0)
const detail = ref<DeskChapterDetail | null>(null)

const STAGES = [
  { key: 'plot', label: '剧情', tip: '这一场戏谁要什么、遇到什么阻力' },
  { key: 'draft', label: '正文', tip: '按剧情展开成可读的章节正文' },
  { key: 'polish', label: '润色', tip: '收束节奏、强化章尾钩子' },
] as const
type StageKey = (typeof STAGES)[number]['key']

const text = reactive<Record<StageKey, string>>({ plot: '', draft: '', polish: '' })
const directive = reactive<Record<StageKey, string>>({ plot: '', draft: '', polish: '' })
const force = reactive<Record<StageKey, boolean>>({ plot: false, draft: false, polish: false })
const thinking = reactive<Record<StageKey, string>>({ plot: '', draft: '', polish: '' })
const stageNotes = reactive<Record<StageKey, string[]>>({ plot: [], draft: [], polish: [] })
const busy = ref('')
const review = ref<ChapterReview | null>(null)
const lastFinalize = ref<FinalizeResult | null>(null)

// 生成中的正文框自动滚到底
const stagePanes = reactive<Record<StageKey, HTMLElement | null>>({ plot: null, draft: null, polish: null })
watch(text, () => {
  if (!busy.value) return
  const el = stagePanes[busy.value as StageKey]
  if (el) el.scrollTop = el.scrollHeight
})

const finalizedCount = computed(() => overview.value?.finalized ?? 0)
const totalCount = computed(() => overview.value?.total ?? 0)
// 今日应写：第一章未定稿的
const todayNo = computed(
  () => overview.value?.chapters.find((c) => !c.finalized_at)?.no ?? 0,
)

// 分页：章节列表按 30/页切，全量拉取保证「今日应写」跨页计算正确
const deskPage = ref(1)
const deskPageSize = 30
const pagedChapters = computed(() =>
  overview.value
    ? overview.value.chapters.slice((deskPage.value - 1) * deskPageSize, deskPage.value * deskPageSize)
    : [],
)

async function loadOverview(keepSelection = true) {
  if (!currentProjectId.value) {
    overview.value = null
    return
  }
  loading.value = true
  try {
    overview.value = await deskApi.detail(currentProjectId.value)
    if (!keepSelection || !currentNo.value) {
      currentNo.value = todayNo.value || overview.value.chapters[0]?.no || 0
    }
    if (currentNo.value && !overview.value.chapters.some((c) => c.no === currentNo.value)) {
      currentNo.value = overview.value.chapters[0]?.no || 0
    }
  } catch (e) {
    ElMessage.error(`章台加载失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

async function selectChapter(no: number) {
  if (busy.value) {
    ElMessage.warning('有任务进行中，请稍候')
    return
  }
  currentNo.value = no
  if (!no) {
    detail.value = null
    return
  }
  try {
    detail.value = await deskApi.chapter(currentProjectId.value, no)
    const ch = detail.value.chapter
    text.plot = ch.plot?.text ?? ''
    text.draft = ch.draft?.text ?? ''
    text.polish = ch.polish?.text ?? ''
    directive.plot = ''
    directive.draft = ''
    directive.polish = ''
    review.value = ch.review
    lastFinalize.value = null
  } catch (e) {
    ElMessage.error(`章节加载失败：${(e as Error).message}`)
  }
}

function setStagePane(key: StageKey) {
  return (el: unknown) => {
    const comp = el as { $el?: HTMLElement } | null
    stagePanes[key] = comp?.$el?.querySelector('textarea') ?? null
  }
}

const refiningStage = ref<StageKey | ''>('')

// 迭代润色：在当前文本基础上按附加指令调整——原文被替换但未保存，不满意可直接改回或重新生成
async function refineStage(stage: StageKey) {
  if (busy.value) return
  if (!text[stage].trim()) {
    ElMessage.warning('当前没有内容可调整')
    return
  }
  if (!directive[stage].trim()) {
    ElMessage.warning('先在指令框写调整要求，例：开头放慢、对话再口语化、加强结尾悬念')
    return
  }
  busy.value = stage
  refiningStage.value = stage
  thinking[stage] = ''
  stageNotes[stage] = ['正在按要求调整当前文本——改完还没保存，不满意可手动改回']
  const original = text[stage]
  text[stage] = ''
  try {
    const r = await refineApi.stream(original, directive[stage].trim(), {
      onThinking: (t) => {
        thinking[stage] += t
      },
      onDelta: (t) => {
        text[stage] += t
      },
      onNotice: (n) => {
        stageNotes[stage].push(n)
      },
    })
    text[stage] = r.y
    ElMessage.success(`已按要求调整（${r.y.length} 字）。满意点「保存修改」，不满意可继续调整或手动改`)
  } catch (e) {
    text[stage] = original // 失败还回原稿，绝不丢作者内容
    ElMessage.error(`调整失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
    refiningStage.value = ''
  }
}

async function runStage(stage: StageKey) {
  if (busy.value) return
  busy.value = stage
  // 流式生成：清空旧文，正文增量实时打进编辑框
  text[stage] = ''
  thinking[stage] = ''
  stageNotes[stage] = []
  try {
    const r = await deskApi.runStream(
      currentProjectId.value,
      currentNo.value,
      stage,
      { directive: directive[stage].trim() || undefined, force: force[stage] },
      {
        onThinking: (t) => {
          thinking[stage] += t
        },
        onDelta: (t) => {
          text[stage] += t
        },
        onResumed: () => {
          stageNotes[stage].push('命中收据，秒回（未重调模型）')
        },
        onNotice: (n) => {
          stageNotes[stage].push(n)
        },
      },
    )
    ElMessage.success(
      `${STAGES.find((s) => s.key === stage)!.label}已生成 ${r.y.length} 字` +
        (r.resumed_indices.length ? '（命中收据）' : ''),
    )
    busy.value = '' // 先解锁再刷新，selectChapter 有 busy 守卫
    await loadOverview()
    if (currentNo.value) await selectChapter(currentNo.value)
  } catch (e) {
    ElMessage.error(`生成失败：${(e as Error).message}`)
    busy.value = ''
  }
}

async function saveStage(stage: StageKey) {
  if (!text[stage].trim()) {
    ElMessage.warning('内容为空，无需保存')
    return
  }
  try {
    await deskApi.saveText(currentProjectId.value, currentNo.value, stage, text[stage])
    ElMessage.success('已保存（手工内容不会被机器覆盖）')
    await loadOverview()
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  }
}

async function runReview() {
  if (busy.value) return
  busy.value = 'review'
  try {
    review.value = await deskApi.review(currentProjectId.value, currentNo.value)
    ElMessage.success(review.value.verdict === 'pass' ? '审稿通过' : '审稿发现不稳项，请处理后再定稿')
  } catch (e) {
    ElMessage.error(`审稿失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

async function doFinalize() {
  if (busy.value) return
  try {
    await ElMessageBox.confirm(
      `定稿第 ${currentNo.value} 章？将自动执行保鲜飞轮：抽取实体、核验必现标记、整本合成采纳为新版本。`,
      '定稿确认',
      { confirmButtonText: '定稿', cancelButtonText: '再改改', type: 'info' },
    )
  } catch {
    return
  }
  busy.value = 'finalize'
  try {
    lastFinalize.value = await deskApi.finalize(currentProjectId.value, currentNo.value)
    ElMessage.success(`第 ${currentNo.value} 章已定稿，飞轮运转完成`)
    await loadOverview()
    const next = overview.value?.chapters.find((c) => !c.finalized_at)
    if (next) await selectChapter(next.no)
  } catch (e) {
    ElMessage.error(`定稿失败：${(e as Error).message}`)
  } finally {
    busy.value = ''
  }
}

onMounted(async () => {
  const focus = (window as unknown as { __deskFocus?: { value: number } }).__deskFocus
  await loadOverview(false)
  if (focus?.value) {
    await selectChapter(focus.value)
    focus.value = 0
  }
})

watch(currentProjectId, () => {
  currentNo.value = 0
  detail.value = null
  overview.value = null
  deskPage.value = 1
  loadOverview(false)
})

// 跳运行台续写大纲：置位标志，PipelineView 读取后进入续写模式并清零
function goContinueOutline() {
  const w = window as unknown as { __pipelineContinue?: { value: boolean } }
  if (w.__pipelineContinue) w.__pipelineContinue.value = true
  else (window as unknown as { __pipelineContinue: { value: boolean } }).__pipelineContinue = { value: true }
  activeTab.value = 'pipeline'
}
</script>

<template>
  <div v-if="!currentProjectId">
    <el-empty description="先选择或创建一个小说项目">
      <el-button type="primary" @click="activeTab = 'projects'">去项目管理</el-button>
    </el-empty>
  </div>

  <div v-else-if="!overview">
    <el-empty :description="loading ? '章台加载中…' : '章台加载失败'">
      <el-button v-if="!loading" @click="loadOverview(false)">重试</el-button>
    </el-empty>
  </div>

  <div v-else-if="!overview.chapters.length">
    <el-empty description="还没有章节可写——先在创作向导的「故事架构」站生成大纲，章台才有章可做">
      <el-button type="primary" @click="activeTab = 'wizard'">去创作向导</el-button>
    </el-empty>
  </div>

  <el-row v-else :gutter="16">
    <!-- 左栏：章节列表 -->
    <el-col :span="7">
      <el-card shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>章节进度</span>
            <el-tag type="success" size="small">{{ finalizedCount }} / {{ totalCount }} 章定稿</el-tag>
          </div>
        </template>
        <div v-if="todayNo" style="margin-bottom: 10px; font-size: 13px; color: #666">
          今天写到第 <b style="color: var(--el-color-primary)">{{ todayNo }}</b> 章
          <el-button
            size="small"
            type="primary"
            plain
            style="margin-left: 10px"
            @click="goContinueOutline"
          >
            续写 / 补新章
          </el-button>
        </div>
        <div v-if="overview && overview.outline_chapters < overview.total" style="margin-bottom: 10px; font-size: 12px; color: #e6a23c">
          有 {{ overview.total - overview.outline_chapters }} 章还没有大纲，做完也能写，但建议回向导补
        </div>
        <el-scrollbar max-height="560px">
          <div
            v-for="c in pagedChapters"
            :key="c.no"
            class="chapter-item"
            :class="{ active: c.no === currentNo, today: c.no === todayNo }"
            @click="selectChapter(c.no)"
          >
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 6px">
              <span>第 {{ c.no }} 章 · {{ c.title || '（未命名）' }}</span>
              <el-tag v-if="c.finalized_at" type="success" size="small">定稿</el-tag>
            </div>
            <div style="display: flex; gap: 4px; margin-top: 4px; align-items: center">
              <el-tag :type="c.has_outline ? 'info' : 'warning'" size="small" effect="plain">
                {{ c.has_outline ? '有大纲' : '无大纲' }}
              </el-tag>
              <el-tag :type="c.plot_done ? 'success' : 'info'" size="small" effect="plain">剧情</el-tag>
              <el-tag :type="c.draft_done ? 'success' : 'info'" size="small" effect="plain">正文</el-tag>
              <el-tag :type="c.polish_done ? 'success' : 'info'" size="small" effect="plain">润色</el-tag>
              <el-tag
                v-if="c.review_verdict"
                :type="c.review_verdict === 'pass' ? 'success' : 'danger'"
                size="small"
                effect="plain"
              >
                审{{ c.review_verdict === 'pass' ? '过' : '卡' }}
              </el-tag>
              <span v-if="c.chars" style="font-size: 12px; color: #999">{{ c.chars }} 字</span>
            </div>
          </div>
        </el-scrollbar>
        <el-pagination
          v-if="totalCount > deskPageSize"
          v-model:current-page="deskPage"
          :page-size="deskPageSize"
          :total="totalCount"
          layout="prev, pager, next"
          size="small"
          style="margin-top: 8px; justify-content: center"
        />
      </el-card>
    </el-col>

    <!-- 右栏：单章微循环 -->
    <el-col :span="17">
      <template v-if="detail">
        <el-card shadow="never" style="margin-bottom: 14px">
          <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
            <h3 style="margin: 0">第 {{ detail.chapter.no }} 章 · {{ detail.chapter.title || '（未命名）' }}</h3>
            <el-tag v-if="detail.chapter.finalized_at" type="success">已定稿 {{ detail.chapter.finalized_at.slice(0, 16) }}</el-tag>
            <el-tag v-else-if="currentNo === todayNo" type="primary" effect="plain">今日目标</el-tag>
          </div>
          <el-collapse style="margin-top: 10px">
            <el-collapse-item title="本章大纲" name="outline">
              <div class="outline-preview">{{ detail.outline || '（本章暂无大纲，可直接写，建议之后回向导补齐）' }}</div>
            </el-collapse-item>
          </el-collapse>
        </el-card>

        <!-- 三段生成：剧情 / 正文 / 润色 -->
        <el-card v-for="s in STAGES" :key="s.key" shadow="never" style="margin-bottom: 14px">
          <template #header>
            <div style="display: flex; gap: 10px; align-items: center; flex-wrap: wrap">
              <b>{{ s.label }}</b>
              <span style="color: #999; font-size: 12px">{{ s.tip }}</span>
              <span style="flex: 1" />
              <el-checkbox v-model="force[s.key]" size="small">忽略收据重生成</el-checkbox>
              <el-button size="small" type="primary" :loading="busy === s.key" @click="runStage(s.key)">
                {{ text[s.key] ? '重新生成' : `生成${s.label}` }}
              </el-button>
              <el-button
                size="small"
                type="warning"
                plain
                :loading="busy === s.key && refiningStage === s.key"
                :disabled="!text[s.key] || !!busy"
                @click="refineStage(s.key)"
              >
                按要求调整
              </el-button>
              <el-button size="small" :disabled="!text[s.key] || busy === s.key" @click="saveStage(s.key)">
                保存修改
              </el-button>
            </div>
          </template>
          <el-input
            v-model="directive[s.key]"
            size="small"
            :placeholder="`指令（生成用，或配合「按要求调整」改当前文本）：如「加强悬念」「开头放慢」「对话再口语化」`"
            style="margin-bottom: 10px"
            :disabled="busy === s.key"
          />
          <div v-if="thinking[s.key]" class="thinking-pane">
            <div style="font-size: 12px; color: #909399; margin-bottom: 4px">
              思考过程{{ busy === s.key ? '（生成中，实时）' : '' }}
            </div>
            <pre style="margin: 0; white-space: pre-wrap; font-family: inherit; line-height: 1.7">{{ thinking[s.key] }}</pre>
          </div>
          <div v-for="(n, i) in stageNotes[s.key]" :key="i" style="font-size: 12px; color: #e6a23c; margin-bottom: 6px">
            {{ n }}
          </div>
          <el-input
            v-model="text[s.key]"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 16 }"
            :placeholder="`尚未生成${s.label}。可直接粘贴你自己的内容后点「保存修改」——手写和机器生成同等地位。`"
            :ref="setStagePane(s.key)"
          />
          <div v-if="detail!.chapter[s.key]?.updated_at" style="margin-top: 6px; font-size: 12px; color: #999">
            最近更新 {{ detail!.chapter[s.key]!.updated_at.slice(0, 16).replace('T', ' ') }}
          </div>
        </el-card>

        <!-- 审稿 -->
        <el-card shadow="never" style="margin-bottom: 14px">
          <template #header>
            <div style="display: flex; gap: 10px; align-items: center">
              <b>审稿</b>
              <span style="color: #999; font-size: 12px">六项一致性检查：情节逻辑 / 人物目标 / 情感关系 / 物理信息 / 场景过渡 / 章尾钩子</span>
              <span style="flex: 1" />
              <el-button size="small" :loading="busy === 'review'" :disabled="!text.draft && !text.polish" @click="runReview">
                运行审稿
              </el-button>
            </div>
          </template>
          <template v-if="review">
            <div style="margin-bottom: 8px">
              <el-tag :type="review.verdict === 'pass' ? 'success' : 'danger'">
                {{ review.verdict === 'pass' ? '通过' : `发现 ${review.unstable_count} 处不稳` }}
              </el-tag>
              <span style="margin-left: 8px; color: #666; font-size: 13px">{{ review.summary }}</span>
            </div>
            <div v-for="it in review.items" :key="it.name" style="margin-bottom: 6px; font-size: 13px">
              <el-tag :type="it.status === '稳' ? 'success' : 'danger'" size="small" effect="plain">{{ it.name }}：{{ it.status }}</el-tag>
              <span style="margin-left: 8px; color: #666">{{ it.problem }}</span>
            </div>
            <div v-if="review.verdict !== 'pass'" style="margin-top: 8px; font-size: 12px; color: #e6a23c">
              处理方式：改完正文/润色后重新审稿；或到「审稿与标记」页把问题转成下一章必现标记
            </div>
          </template>
          <el-empty v-else description="生成正文后先审稿，通过再定稿" :image-size="60" />
        </el-card>

        <!-- 定稿 -->
        <el-card shadow="never">
          <template #header>
            <div style="display: flex; gap: 10px; align-items: center">
              <b>定稿</b>
              <span style="color: #999; font-size: 12px">定稿后进入保鲜飞轮：抽取实体 → 核验必现标记 → 整本合成采纳</span>
              <span style="flex: 1" />
              <el-button type="success" :loading="busy === 'finalize'" :disabled="!!detail.chapter.finalized_at" @click="doFinalize">
                {{ detail.chapter.finalized_at ? '已定稿' : `定稿第 ${currentNo} 章` }}
              </el-button>
            </div>
          </template>
          <template v-if="lastFinalize">
            <div style="font-size: 13px; line-height: 2">
              <div>正文版本：v{{ lastFinalize.draft_version }}（全 {{ lastFinalize.total_chapters }} 章）</div>
              <div v-if="lastFinalize.entities">
                实体飞轮：
                <template v-if="lastFinalize.entities.error">
                  <el-tag type="warning" size="small">未完成：{{ lastFinalize.entities.error }}</el-tag>
                </template>
                <template v-else>
                  新建 {{ (lastFinalize.entities.created ?? []).length }} ·
                  更新 {{ (lastFinalize.entities.updated ?? []).length }} ·
                  丢弃 {{ lastFinalize.entities.dropped ?? 0 }}
                </template>
              </div>
              <div v-if="lastFinalize.marker_hits.length">
                必现标记核验：
                <el-tag
                  v-for="h in lastFinalize.marker_hits"
                  :key="h.marker.id"
                  :type="h.hit ? 'success' : 'danger'"
                  size="small"
                  style="margin-right: 6px"
                >
                  {{ h.hit ? '已兑现' : '未兑现' }}：{{ h.marker.content.slice(0, 20) }}
                </el-tag>
              </div>
            </div>
          </template>
          <el-empty v-else description="定稿即归档：实体库与全书版本随更新，越写越准" :image-size="60" />
        </el-card>
      </template>
      <el-empty v-else description="从左侧选择一章开始今天的更新" />
    </el-col>
  </el-row>
</template>

<style scoped>
.chapter-item {
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
  font-size: 13px;
}
.chapter-item:hover {
  background: #f5f7fa;
}
.chapter-item.active {
  background: var(--el-color-primary-light-9);
}
.chapter-item.today {
  border-left: 3px solid var(--el-color-primary);
}
.outline-preview {
  white-space: pre-wrap;
  font-size: 13px;
  color: #555;
  line-height: 1.8;
}
.thinking-pane {
  background: #f7f8fa;
  border-radius: 6px;
  padding: 8px 12px;
  margin-bottom: 10px;
  max-height: 200px;
  overflow: auto;
  font-size: 12px;
  color: #909399;
}
</style>
