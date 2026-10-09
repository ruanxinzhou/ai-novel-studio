<script setup lang="ts">
// 运行台：任务制高级工作台——选任务 → 填要点 → 生成 → 三出口（采纳/改后采纳/复制）
// 面向想越过向导直接控制单层的作者；日常写作请走创作向导与今日章台
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  LAYER_LABELS,
  LAYERS,
  corpusApi,
  markersApi,
  projectsApi,
  refineApi,
  reviewApi,
  runsApi,
  skillsApi,
  type ChapterReview,
  type Excerpt,
  type LayerRunResult,
  type LayerVersionFull,
  type SkillModule,
} from '../api'
import { activeTab, currentProjectId } from '../state'

// ---------- 任务模式：给作者的目标语言，映射到底层 layer ----------
interface TaskCard {
  key: 'intro' | 'outline' | 'chapter' | 'advanced'
  layer: string
  title: string
  desc: string
  inputHint: string
  example: string
}

const TASKS: TaskCard[] = [
  {
    key: 'intro',
    layer: 'intro',
    title: '故事简介',
    desc: '把你的点子要点，扩成一段能立住的故事简介',
    inputHint: '每行写一个点子要点：题材背景、主角设定、金手指、核心冲突……',
    example: '例：克苏鲁无限流，主角是普通人，被古神选中穿梭各宇宙做单元剧冒险',
  },
  {
    key: 'outline',
    layer: 'outline',
    title: '全书大纲',
    desc: '根据简介和要点，排出分章大纲（每行一个「第N章」）',
    inputHint: '每行一个「第N章」+ 这一章发生什么。前几章是黄金三章，建议写细。',
    example: '例：第1章 主角加班夜归，捡到会低语的青铜镜，被拉入第一场试炼',
  },
  {
    key: 'chapter',
    layer: 'chapter',
    title: '章节正文',
    desc: '根据大纲写第 N 章正文，自动带上该章待兑现的必现情节标记',
    inputHint: '每行写本章的一个要点：开场、冲突、转折、结尾钩子……',
    example: '例：主角在旧书店发现前一位持有者的笔记，镜面开始渗血',
  },
  {
    key: 'advanced',
    layer: 'draft',
    title: '自由运行',
    desc: '选任意运行目标，配写作模块、检索范文——全部能力都在这里',
    inputHint: '每行一个输入块，格式依所选目标而定',
    example: '给熟悉系统的作者：剧情/正文/润色/审稿/杂务层、模块与范文组合',
  },
]

const task = ref<TaskCard>(TASKS[0])
const layer = computed(() => task.value.layer)
const isAdvanced = computed(() => task.value.key === 'advanced')

// ---------- 项目当前层内容：微调与续写的基础 ----------
const projectOutline = ref<LayerVersionFull | null>(null)
const projectIntro = ref<LayerVersionFull | null>(null)
const loadingLayers = ref(false)

async function loadProjectLayers() {
  if (!currentProjectId.value) {
    projectOutline.value = null
    projectIntro.value = null
    return
  }
  loadingLayers.value = true
  try {
    const [o, i] = await Promise.all([
      projectsApi.layer(currentProjectId.value, 'outline'),
      projectsApi.layer(currentProjectId.value, 'intro'),
    ])
    projectOutline.value = o.version
    projectIntro.value = i.version
  } catch {
    // 静默失败：提示条不出现，点载入按钮时单独报错
  } finally {
    loadingLayers.value = false
  }
}

const outlineMaxChapter = computed(() => {
  let max = 0
  for (const m of (projectOutline.value?.y ?? '').matchAll(/^第\s*(\d+)\s*章/gm)) {
    max = Math.max(max, Number(m[1]))
  }
  return max
})

async function loadOutlineToX() {
  if (!projectOutline.value) return
  if (xLines.value.trim()) {
    try {
      await ElMessageBox.confirm('要点框已有内容，载入会覆盖——继续？', '覆盖确认', { type: 'warning' })
    } catch {
      return
    }
  }
  xLines.value = projectOutline.value.y
  ElMessage.success('已载入当前大纲——直接改，或用「继续调整」按新要求迭代后采纳')
}

async function loadIntroToX() {
  if (!projectIntro.value) return
  if (xLines.value.trim()) {
    try {
      await ElMessageBox.confirm('要点框已有内容，载入会覆盖——继续？', '覆盖确认', { type: 'warning' })
    } catch {
      return
    }
  }
  xLines.value = projectIntro.value.y
  ElMessage.success('已载入当前简介——直接改，或用「继续调整」按新要求迭代后采纳')
}

// ---------- 大纲续写模式：已有 N 章，从第 N+1 章接着排 ----------
const outlineMode = ref<'fresh' | 'continue'>('fresh')
const contStart = ref<number>(1)
const contCount = ref<number>(30)
const contVolume = ref('')
const isContinueRun = computed(() => task.value.key === 'outline' && outlineMode.value === 'continue')

watch(outlineMaxChapter, (v) => {
  // 载入大纲/切项目后，起点默认落在已有最后一章的下一章
  if (v > 0 && contStart.value <= v) contStart.value = v + 1
})

// 拼接：旧大纲 + 新卷名（可选）+ 新章，采纳后大纲层整体入新版本
function joinOutline(newY: string) {
  const parts = [projectOutline.value!.y.trimEnd()]
  const vol = contVolume.value.trim()
  if (vol) parts.push(vol)
  parts.push(newY.trimStart())
  return parts.join('\n\n')
}

const userDirective = ref('')
const xLines = ref('') // 每行一个要点
const xChunkLength = ref<number | null>(null)
const chapterNo = ref<number | null>(null)
const showAdvancedOpts = ref<string[]>([])
const running = ref(false)
const result = ref<LayerRunResult | null>(null)
const adopted = ref(false)
const editableY = ref('') // 结果可改——作者改后采纳是一级公民
const adoptNote = ref('')
const adopting = ref(false)

// ---------- 实时流式状态 ----------
const liveThinking = ref('')
const liveText = ref('')
const liveIndex = ref(0)
const doneBlocks = ref<number[]>([])
const resumedBlocks = ref<number[]>([])
const notices = ref<string[]>([])
const livePane = ref<HTMLElement | null>(null)

watch(liveText, () => {
  const el = livePane.value
  if (el) el.scrollTop = el.scrollHeight
})

function resetLive() {
  liveThinking.value = ''
  liveText.value = ''
  liveIndex.value = 0
  doneBlocks.value = []
  resumedBlocks.value = []
  notices.value = []
}

const hasFailed = computed(() => !!result.value && result.value.journal.failed > 0)

// ---------- 模块与范文（高级选项，折叠收纳） ----------
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

// 与章台联动：章台「续写/补新章」入口（DeskView 置位后本页清零）
const pipelineContinue =
  (window as unknown as { __pipelineContinue?: { value: boolean } }).__pipelineContinue ?? { value: false }
;(window as unknown as { __pipelineContinue?: { value: boolean } }).__pipelineContinue = pipelineContinue

function enterContinueMode() {
  const t = TASKS.find((t) => t.key === 'outline')!
  task.value = t
  result.value = null
  adopted.value = false
  syncDefaults()
  if (projectOutline.value) {
    outlineMode.value = 'continue'
    contStart.value = outlineMaxChapter.value + 1
  } else {
    outlineMode.value = 'fresh'
    ElMessage.info('当前项目还没有大纲——先从头生成一次，之后就能在这里续写')
  }
}

onMounted(async () => {
  try {
    const [mods, types] = await Promise.all([skillsApi.list(), corpusApi.types()])
    modules.value = mods.modules
    allExcerptTypes.value = types.types
    syncDefaults()
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
  await loadProjectLayers()
  if (pipelineContinue.value) {
    pipelineContinue.value = false
    enterContinueMode()
  }
})

watch(currentProjectId, () => {
  outlineMode.value = 'fresh'
  contVolume.value = ''
  loadProjectLayers()
})

watch(activeTab, (t) => {
  if (t === 'pipeline' && pipelineContinue.value) {
    pipelineContinue.value = false
    enterContinueMode()
  }
})

function syncDefaults() {
  selectedModules.value = [...defaultsForLayer.value]
}

function pickTask(t: TaskCard) {
  task.value = t
  result.value = null
  adopted.value = false
  syncDefaults()
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

async function doRun() {
  // 续写分支：x 用新章号骨架，已有大纲作为定稿材料注入，结果与旧大纲拼接
  let pairs: [string, string][]
  let extraMaterials: { source_id: string; family: string; content: string; title: string }[] | undefined
  let directive: string | undefined
  let chunkLength: number | undefined

  if (isContinueRun.value) {
    if (!projectOutline.value) {
      ElMessage.warning('当前项目还没有大纲——先「从头生成」一次，之后才能续写')
      return
    }
    if (!contStart.value || contStart.value < 1 || !contCount.value || contCount.value < 1) {
      ElMessage.warning('请填续写的起始章号和新增章数')
      return
    }
    const end = contStart.value + contCount.value - 1
    pairs = Array.from({ length: contCount.value }, (_, i) => [`第${contStart.value + i}章\n`, ''] as [string, string])
    extraMaterials = [{
      source_id: 'outline:current',
      family: 'finalized',
      content: projectOutline.value.y,
      title: `已有大纲（前 ${outlineMaxChapter.value} 章，续写衔接）`,
    }]
    directive =
      `已有大纲 ${outlineMaxChapter.value} 章见材料。本次从第 ${contStart.value} 章续写到第 ${end} 章，共 ${contCount.value} 章` +
      (contVolume.value.trim() ? `，新卷首行写「${contVolume.value.trim()}」` : '') +
      '。衔接已有剧情、伏笔与文风，不得改动已有章节。'
    chunkLength = 40
  } else {
    pairs = buildXPairs()
    if (!pairs.length) {
      ElMessage.warning('请至少输入一行要点')
      return
    }
    if (task.value.key === 'chapter' && !chapterNo.value) {
      ElMessage.warning('写正文请先填章号——会自动带上该章待兑现的必现情节标记')
      return
    }
  }

  running.value = true
  result.value = null
  adopted.value = false
  resetLive()
  try {
    // 流式运行：思考与正文实时上屏；已完成块自动命中收据秒回，失败块只补失败
    const r = await runsApi.stream(layer.value, {
      ...body(),
      x_pairs: pairs,
      materials: extraMaterials,
      user_directive: directive ?? (userDirective.value || undefined),
      x_chunk_length: chunkLength ?? (xChunkLength.value || undefined),
    }, {
      onThinking: (text, index) => {
        liveIndex.value = index
        liveThinking.value += text
      },
      onDelta: (text, index) => {
        liveIndex.value = index
        liveText.value += text
      },
      onResumed: (index) => {
        resumedBlocks.value.push(index)
      },
      onBlockDone: (index) => {
        doneBlocks.value.push(index)
      },
      onNotice: (text) => {
        notices.value.push(text)
      },
    })
    result.value = r
    editableY.value = isContinueRun.value ? joinOutline(r.y) : r.y
    ElMessage.success(
      `完成：${r.journal.completed}/${r.journal.total} 段` +
        (r.resumed_indices.length ? `，复用旧结果 ${r.resumed_indices.length} 段` : '') +
        (isContinueRun.value ? '，已与旧大纲拼接——确认后采纳' : ''),
    )
  } catch (e) {
    ElMessage.error(`运行失败：${(e as Error).message}`)
  } finally {
    running.value = false
  }
}

// ---------- 审稿 ----------
const reviewing = ref(false)
const review = ref<ChapterReview | null>(null)

async function doReview() {
  if (!editableY.value) {
    ElMessage.warning('先运行生成内容再审稿')
    return
  }
  reviewing.value = true
  review.value = null
  try {
    review.value = await reviewApi.chapter(xLines.value, editableY.value)
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

// ---------- 结果三出口 ----------
const NEXT_STEPS: Record<string, { text: string; tab: string; label: string }> = {
  intro: { text: '简介已入库。下一步：去创作向导的选题站把它采进向导，继续脑洞站', tab: 'wizard', label: '去创作向导' },
  outline: { text: '大纲已入库。章节已就绪——可以去今日章台开写第 1 章', tab: 'desk', label: '去今日章台' },
  chapter: { text: '正文已入库。下一步：去今日章台走审稿和定稿', tab: 'desk', label: '去今日章台' },
}

async function adoptToProject() {
  if (!result.value || !editableY.value.trim()) return
  if (!currentProjectId.value) {
    ElMessage.warning('先在顶部选择或新建小说项目')
    return
  }
  adopting.value = true
  try {
    await projectsApi.adopt(
      currentProjectId.value,
      layer.value,
      editableY.value,
      result.value.pairs,
      adoptNote.value || `运行 ${result.value.run_id.slice(0, 8)}`,
    )
    adopted.value = true
    ElMessage.success(`已采纳到项目「${LAYER_LABELS[layer.value]}」层，新版本入库`)
  } catch (e) {
    ElMessage.error(`采纳失败：${(e as Error).message}`)
  } finally {
    adopting.value = false
  }
}

async function copyY() {
  try {
    await navigator.clipboard.writeText(editableY.value)
    ElMessage.success('已复制全文')
  } catch {
    ElMessage.error('复制失败，请手动选择文本复制')
  }
}

// ---------- 迭代润色：在当前成文基础上按新要求继续调整 ----------
const refineReq = ref('')
const refining = ref(false)

async function doRefine() {
  if (!editableY.value.trim()) {
    ElMessage.warning('没有可调整的内容，先运行生成')
    return
  }
  if (!refineReq.value.trim()) {
    ElMessage.warning('写一句调整要求，例：开头节奏放慢、对话再口语化、加强结尾悬念')
    return
  }
  refining.value = true
  resetLive()
  try {
    const r = await refineApi.stream(editableY.value, refineReq.value.trim(), {
      onThinking: (t) => {
        liveThinking.value += t
      },
      onDelta: (t) => {
        liveText.value += t
      },
      onNotice: (n) => {
        notices.value.push(n)
      },
    })
    editableY.value = r.y
    ElMessage.success(`已按你的要求调整（${r.y.length} 字），不满意可继续调整或手动改`)
  } catch (e) {
    ElMessage.error(`调整失败：${(e as Error).message}`)
  } finally {
    refining.value = false
  }
}

const nextStep = computed(() => NEXT_STEPS[layer.value] ?? null)
</script>

<template>
  <div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 14px">
      这里是自由组合的高级工作台。<b>日常写作请走「创作向导」和「今日章台」</b>——
      同样的内容重复生成不会重复花钱（同输入自动复用上一次结果）。
    </el-alert>

    <el-row :gutter="16">
      <el-col :span="9">
        <!-- 第一步：选任务 -->
        <el-card shadow="never" style="margin-bottom: 14px">
          <template #header>第一步 · 我要做什么</template>
          <div
            v-for="t in TASKS"
            :key="t.key"
            class="task-card"
            :class="{ active: task.key === t.key }"
            @click="pickTask(t)"
          >
            <div class="task-title">
              {{ t.title }}
              <el-tag v-if="t.key === 'advanced'" size="small" type="info">老手</el-tag>
            </div>
            <div class="task-desc">{{ t.desc }}</div>
          </div>
        </el-card>

        <!-- 第二步：填输入 -->
        <el-card shadow="never">
          <template #header>第二步 · 给我原料</template>
          <el-form label-width="86px" label-position="left">
            <!-- 项目已有内容：一键载入微调 / 续写 -->
            <el-form-item v-if="task.key === 'outline' && projectOutline" label="当前大纲">
              <div style="width: 100%">
                <div style="font-size: 12px; color: #666; margin-bottom: 6px">
                  项目已有大纲 v{{ projectOutline.seq }}（{{ outlineMaxChapter }} 章）
                </div>
                <el-button size="small" plain @click="loadOutlineToX">载入微调</el-button>
                <el-button size="small" type="primary" plain @click="outlineMode = 'continue'; contStart = outlineMaxChapter + 1">
                  续写新章
                </el-button>
                <div style="font-size: 12px; color: #999; margin-top: 4px">
                  载入后可直接修改，或生成后用「继续调整」按新要求迭代
                </div>
              </div>
            </el-form-item>
            <el-form-item v-if="task.key === 'intro' && projectIntro" label="当前简介">
              <div style="width: 100%">
                <div style="font-size: 12px; color: #666; margin-bottom: 6px">项目已有简介 v{{ projectIntro.seq }}（{{ projectIntro.y.length }} 字）</div>
                <el-button size="small" plain @click="loadIntroToX">载入微调</el-button>
              </div>
            </el-form-item>

            <el-form-item v-if="task.key === 'outline'" label="生成方式">
              <el-radio-group v-model="outlineMode">
                <el-radio-button value="fresh">从头生成</el-radio-button>
                <el-radio-button value="continue">续写新章</el-radio-button>
              </el-radio-group>
            </el-form-item>

            <template v-if="isContinueRun">
              <el-form-item label="续写范围">
                <div style="width: 100%; display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
                  <span style="font-size: 13px">从第</span>
                  <el-input-number v-model="contStart" :min="1" style="width: 110px" />
                  <span style="font-size: 13px">章续写</span>
                  <el-input-number v-model="contCount" :min="1" :max="300" style="width: 110px" />
                  <span style="font-size: 13px">章（到第 {{ contStart + contCount - 1 }} 章）</span>
                </div>
                <div style="font-size: 12px; color: #888; margin-top: 4px">
                  已有 {{ outlineMaxChapter }} 章自动作为上下文注入，只生成新章，结果与旧大纲拼接后一并采纳
                </div>
              </el-form-item>
              <el-form-item label="新卷名">
                <el-input v-model="contVolume" placeholder="可空。如：第二卷 风起云涌（会写在新章开头）" />
              </el-form-item>
            </template>

            <el-form-item v-if="!isContinueRun" :label="isAdvanced ? '输入块' : '要点'">
              <div style="width: 100%">
                <el-input
                  v-model="xLines"
                  type="textarea"
                  :rows="8"
                  :placeholder="task.inputHint"
                />
                <div style="font-size: 12px; color: #999; margin-top: 4px">{{ task.example }}</div>
                <div v-if="!isAdvanced" style="font-size: 12px; color: #b0b0b0; margin-top: 2px">
                  相邻要点会按长度合并成更少的生成段（省时省钱）；想一行一段，去高级选项把「每段字数上限」调小
                </div>
              </div>
            </el-form-item>

            <el-form-item v-if="task.key === 'chapter' || isAdvanced" label="章号">
              <el-input-number v-model="chapterNo" :min="1" placeholder="选填" style="width: 100%" />
              <div style="font-size: 12px; color: #888">写正文时填写：自动注入该章待兑现的必现情节标记</div>
            </el-form-item>

            <el-form-item label="附加要求">
              <el-input v-model="userDirective" type="textarea" :rows="2" placeholder="例：多写对话、结尾留悬念（可空）" />
            </el-form-item>

            <el-form-item v-if="isAdvanced" label="运行目标">
              <el-select v-model="task.layer" @change="syncDefaults">
                <el-option v-for="l in LAYERS" :key="l" :value="l" :label="LAYER_LABELS[l]" />
              </el-select>
            </el-form-item>

            <el-collapse v-model="showAdvancedOpts">
              <el-collapse-item title="高级选项：每段字数上限 / 写作模块 / 范文注入" name="adv">
                <el-form-item label="字数上限">
                  <el-input-number v-model="xChunkLength" :min="0" :step="500" placeholder="默认按运行目标规格" style="width: 100%" />
                </el-form-item>
                <el-form-item label="写作模块">
                  <el-select v-model="selectedModules" multiple clearable style="width: 100%" placeholder="空 = 用默认集">
                    <el-option v-for="m in modules" :key="m.name" :value="m.name" :label="`${m.name} — ${m.title}`" />
                  </el-select>
                </el-form-item>
                <el-divider style="margin: 12px 0">范文注入（参考资料）</el-divider>
                <el-form inline>
                  <el-form-item>
                    <el-select v-model="corpusType" clearable filterable placeholder="类型" style="width: 150px">
                      <el-option v-for="t in allExcerptTypes" :key="t.type" :value="t.type" :label="`${t.type} (${t.count})`" />
                    </el-select>
                  </el-form-item>
                  <el-form-item>
                    <el-input v-model="corpusTag" placeholder="标签" style="width: 110px" clearable />
                  </el-form-item>
                  <el-form-item>
                    <el-input v-model="corpusKeyword" placeholder="关键词" style="width: 110px" clearable />
                  </el-form-item>
                  <el-form-item>
                    <el-button @click="previewCorpus">检索</el-button>
                  </el-form-item>
                </el-form>
                <div v-if="corpusHits.length" style="max-height: 200px; overflow: auto">
                  <el-checkbox-group v-model="injectedExcerpts">
                    <div v-for="h in corpusHits" :key="h.excerpt_id" style="margin-bottom: 6px">
                      <el-checkbox :value="h">
                        <span style="font-size: 12px">[{{ h.excerpt_type }}]《{{ h.title }}》</span>
                      </el-checkbox>
                      <div style="font-size: 12px; color: #888; margin-left: 24px; white-space: pre-wrap">{{ h.text.slice(0, 80) }}…</div>
                    </div>
                  </el-checkbox-group>
                </div>
              </el-collapse-item>
            </el-collapse>
          </el-form>

          <el-button type="primary" :loading="running" style="width: 100%; margin-top: 12px" @click="doRun">
            {{ running ? '生成中…' : hasFailed ? '继续生成（只补失败部分）' : isContinueRun ? `续写第 ${contStart}–${contStart + contCount - 1} 章` : '开始生成' }}
          </el-button>
          <div style="font-size: 12px; color: #999; text-align: center; margin-top: 6px">
            中断没关系：同输入重跑只补缺的部分，已写好的不重复花钱
          </div>
          <div v-if="result?.markers_injected?.length" style="margin-top: 10px">
            <el-alert type="warning" :closable="false" show-icon>
              <template #title>本章必现情节已注入（{{ result.markers_injected.length }} 条）</template>
              <div v-for="m in result.markers_injected" :key="m.id" style="font-size: 12px">
                · {{ m.content }}
              </div>
            </el-alert>
          </div>
        </el-card>
      </el-col>

      <el-col :span="15">
        <el-card v-if="running || liveText || liveThinking" shadow="never" style="margin-bottom: 14px">
          <template #header>
            <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
              <span>实时生成</span>
              <el-tag v-if="running" size="small" type="primary">第 {{ liveIndex + 1 }} 段生成中…</el-tag>
              <el-tag v-for="i in doneBlocks" :key="`d${i}`" size="small" type="success">第 {{ i + 1 }} 段完成</el-tag>
              <el-tag v-for="i in resumedBlocks" :key="`r${i}`" size="small" type="info">第 {{ i + 1 }} 段复用旧结果</el-tag>
              <span v-for="(n, i) in notices" :key="i" style="font-size: 12px; color: #e6a23c">{{ n }}</span>
            </div>
          </template>
          <el-collapse v-if="liveThinking" style="margin-bottom: 10px">
            <el-collapse-item title="思考过程" name="think">
              <pre class="thinking-pane">{{ liveThinking }}</pre>
            </el-collapse-item>
          </el-collapse>
          <div ref="livePane" class="live-pane">
            <pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.9; margin: 0">{{ liveText }}<span v-if="running" class="typing-cursor">▍</span></pre>
          </div>
        </el-card>

        <el-card v-if="result" shadow="never">
          <template #header>
            <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap">
              <span>结果</span>
              <el-tag size="small">{{ result.journal.completed }}/{{ result.journal.total }} 段</el-tag>
              <el-tag v-if="result.journal.failed" type="danger" size="small">失败 {{ result.journal.failed }}</el-tag>
              <el-tag v-if="result.resumed_indices.length" type="success" size="small">
                复用旧结果 {{ result.resumed_indices.length }} 段
              </el-tag>
              <el-tag v-for="s in result.skills_injected" :key="s" type="warning" size="small">{{ s }}</el-tag>
            </div>
          </template>

          <!-- 三出口：采纳 / 改后采纳 / 复制 -->
          <div v-if="adopted && nextStep" style="margin-bottom: 12px">
            <el-alert type="success" :closable="false" show-icon>
              <template #title>{{ nextStep.text }}</template>
              <el-button size="small" type="primary" plain @click="activeTab = nextStep.tab">{{ nextStep.label }}</el-button>
            </el-alert>
          </div>
          <div style="margin-bottom: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
            <el-input
              v-model="adoptNote"
              placeholder="版本备注（可空，如：调整了第3章节奏）"
              size="small"
              style="width: 240px"
            />
            <el-button type="success" :loading="adopting" @click="adoptToProject">采纳到项目</el-button>
            <el-button @click="copyY">复制全文</el-button>
            <span v-if="!currentProjectId" style="font-size: 12px; color: #c00">
              未选项目——顶部新建/选择后才能入库
            </span>
            <span v-else style="font-size: 12px; color: #999">下面内容可以直接改，改完再点采纳，存的是你的版本</span>
          </div>

          <!-- 迭代润色：在当前成文上按新要求继续调整 -->
          <div style="display: flex; gap: 8px; margin-bottom: 12px">
            <el-input
              v-model="refineReq"
              size="small"
              placeholder="在此基础上继续调整：例「开头节奏放慢」「对话再口语化」「加强结尾悬念」——只改涉及部分，其余保留"
              @keyup.enter="doRefine"
            />
            <el-button type="warning" plain :loading="refining" @click="doRefine">继续调整</el-button>
          </div>

          <el-tabs>
            <el-tab-pane label="成文（可改）">
              <el-input
                v-model="editableY"
                type="textarea"
                :autosize="{ minRows: 10, maxRows: 26 }"
                style="margin-bottom: 4px"
              />
            </el-tab-pane>
            <el-tab-pane :label="`分段 (${result.pairs.length})`">
              <el-collapse>
                <el-collapse-item v-for="(p, i) in result.pairs" :key="i" :title="`第 ${i + 1} 段（${p[1].length} 字）`">
                  <div style="color: #888; font-size: 12px; margin-bottom: 6px">要点：{{ p[0].slice(0, 120) }}…</div>
                  <pre style="white-space: pre-wrap; font-family: inherit">{{ p[1] }}</pre>
                </el-collapse-item>
              </el-collapse>
            </el-tab-pane>
            <el-tab-pane label="生成明细">
              <el-table :data="result.journal.receipts" size="small">
                <el-table-column prop="index" label="#" width="50" />
                <el-table-column prop="status" label="状态" width="90">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'completed' ? 'success' : 'danger'" size="small">
                      {{ row.status === 'completed' ? '完成' : '失败' }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="completion_status" label="完成态" width="110" />
                <el-table-column prop="attempts" label="尝试" width="60" />
                <el-table-column prop="x_hash" label="指纹" width="130" />
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
              <el-empty v-else description="点「六项一致性审稿」逐项检查当前成文（不稳 ≥2 项不能交稿）" />
            </el-tab-pane>
          </el-tabs>
        </el-card>
        <el-empty v-else-if="!running" description="左边选一个任务，填几行要点，点「开始生成」——结果出来后可直接修改再采纳" />
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.task-card {
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: border-color 0.2s, background 0.2s;
}
.task-card:hover {
  border-color: var(--el-color-primary-light-5);
}
.task-card.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.task-title {
  font-weight: 600;
  margin-bottom: 4px;
  display: flex;
  gap: 8px;
  align-items: center;
}
.task-desc {
  font-size: 12px;
  color: #888;
}
.thinking-pane {
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 12px;
  color: #909399;
  background: #f7f8fa;
  border-radius: 6px;
  padding: 10px 12px;
  max-height: 240px;
  overflow: auto;
  line-height: 1.7;
  margin: 0;
}
.live-pane {
  max-height: 420px;
  overflow: auto;
}
.typing-cursor {
  color: var(--el-color-primary);
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
</style>
