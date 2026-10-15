<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SushiCards from '../components/SushiCards.vue'
import {
  interludeApi,
  type InterludeOption,
  type InterludeZhangshu,
  type InterludePlan,
  type InterludeStep,
} from '../api'
import { currentProjectId } from '../state'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

// ---- 步骤（0 违和点 / 1 后遗症 / 2 下卷钩子 / 3 章数） ----
const step = ref(0)
const stepLabels = ['违和点', '后遗症', '下卷钩子', '章数']
const stepKeys: InterludeStep[] = ['weihedian', 'houchiyizheng', 'xiajuangouzi', 'zhangshu']
const activeStep = computed(() => step.value)

// ---- 基础数据 ----
const loading = ref(false)
const plan = ref<InterludePlan>({})
const volume = ref<any>(null)
const saving = ref(false)
const finalizing = ref(false)
const finalized = ref(false)
const isMock = ref(true)

// ---- 前三步：SushiCards ensemble ----
interface CardItem extends InterludeOption {
  _title: string
  _subtitle: string
  _content: string
  _badge: string
}

const whOptions = ref<InterludeOption[]>([])
const whPickedId = ref<string | null>(null)
const whLoading = ref(false)

const hyOptions = ref<InterludeOption[]>([])
const hyPickedId = ref<string | null>(null)
const hyLoading = ref(false)

const xjOptions = ref<InterludeOption[]>([])
const xjPickedId = ref<string | null>(null)
const xjLoading = ref(false)

// ---- 第四步：章数 ----
const zhangshu = ref<InterludeZhangshu | null>(null)
const zhangshuLoading = ref(false)
const chapterCount = ref(2)
const wordsPerChapter = ref(3500)
const zhangshuReason = ref('')

const pid = computed(() => currentProjectId.value || '')

// ============================================================
// 数据加载
// ============================================================

async function loadPlan() {
  if (!pid.value) return
  loading.value = true
  try {
    const res = await interludeApi.get(pid.value, props.volNo)
    volume.value = res.volume
    plan.value = res.plan || {}
    // 恢复已保存的选中
    if (plan.value.weihedian?.id) {
      whOptions.value = [plan.value.weihedian as InterludeOption]
      whPickedId.value = plan.value.weihedian.id
    }
    if (plan.value.houchiyizheng?.id) {
      hyOptions.value = [plan.value.houchiyizheng as InterludeOption]
      hyPickedId.value = plan.value.houchiyizheng.id
    }
    if (plan.value.xiajuangouzi?.id) {
      xjOptions.value = [plan.value.xiajuangouzi as InterludeOption]
      xjPickedId.value = plan.value.xiajuangouzi.id
    }
    if (plan.value.zhangshu) {
      zhangshu.value = plan.value.zhangshu
      chapterCount.value = plan.value.zhangshu.suggested_chapters ?? 2
      wordsPerChapter.value = plan.value.zhangshu.words_per_chapter ?? 3500
      zhangshuReason.value = plan.value.zhangshu.reason ?? ''
    }
    // 跳到第一个未完成的步骤
    const done = plan.value.steps_done ?? 0
    if (done < 4 && !plan.value.finalized_at) {
      step.value = Math.min(done, 3)
    } else if (plan.value.finalized_at) {
      finalized.value = true
      step.value = 3
    }
  } catch (e: any) {
    ElMessage.error(`加载间章规划失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

onMounted(loadPlan)

// ============================================================
// SushiCards 工具：构造卡片
// ============================================================

function _buildCards(list: InterludeOption[], badge: string): CardItem[] {
  return list.map((o, i) => ({
    ...o,
    id: o.id || `opt-${i}-${Date.now()}`,
    _title: o.title,
    _subtitle: (o as any).how_to_plant || (o as any).summary || '候选',
    _content: o.content,
    _badge: badge,
  }))
}

const whCards = computed(() => _buildCards(whOptions.value, '违和'))
const hyCards = computed(() => _buildCards(hyOptions.value, '后遗'))
const xjCards = computed(() => _buildCards(xjOptions.value, '钩子'))

const pickedWh = computed(() => whOptions.value.find((o) => o.id === whPickedId.value) || null)
const pickedHy = computed(() => hyOptions.value.find((o) => o.id === hyPickedId.value) || null)
const pickedXj = computed(() => xjOptions.value.find((o) => o.id === xjPickedId.value) || null)

// ============================================================
// AI 建议加载（前三步）
// ============================================================

async function loadSuggestions(
  stepKey: InterludeStep,
  directive?: string | null,
  baseIndex?: number | null,
) {
  if (!pid.value) return
  const selected = collectSelected()
  if (stepKey === 'weihedian') whLoading.value = true
  else if (stepKey === 'houchiyizheng') hyLoading.value = true
  else if (stepKey === 'xiajuangouzi') xjLoading.value = true
  else zhangshuLoading.value = true
  try {
    const res = await interludeApi.aiSuggest(pid.value, props.volNo, stepKey, {
      directive: directive || null,
      base_index: baseIndex ?? null,
      ...selected,
    }, selected as any)
    isMock.value = !!res.is_mock
    if (stepKey === 'zhangshu') {
      if (res.zhangshu) {
        zhangshu.value = res.zhangshu
      } else {
        zhangshu.value = {
          suggested_chapters: (res as any).suggested_chapters ?? 2,
          words_per_chapter: (res as any).words_per_chapter ?? 3500,
          reason: (res as any).reason ?? '',
        }
      }
      chapterCount.value = zhangshu.value.suggested_chapters
      wordsPerChapter.value = zhangshu.value.words_per_chapter
      zhangshuReason.value = zhangshu.value.reason
    } else {
      const opts: InterludeOption[] = res.options || (res as any).items || []
      const withIds = opts.map((o, i) => ({
        ...o,
        id: o.id || `${stepKey}-${i}-${Date.now()}`,
      }))
      if (stepKey === 'weihedian') {
        whOptions.value = withIds
        if (!whPickedId.value && withIds.length > 0) whPickedId.value = withIds[0].id
      } else if (stepKey === 'houchiyizheng') {
        hyOptions.value = withIds
        if (!hyPickedId.value && withIds.length > 0) hyPickedId.value = withIds[0].id
      } else {
        xjOptions.value = withIds
        if (!xjPickedId.value && withIds.length > 0) xjPickedId.value = withIds[0].id
      }
    }
  } catch (e: any) {
    ElMessage.error(`AI 建议失败：${e?.message || e}`)
  } finally {
    whLoading.value = false
    hyLoading.value = false
    xjLoading.value = false
    zhangshuLoading.value = false
  }
}

function collectSelected(): Record<string, any> {
  const out: Record<string, any> = {}
  if (pickedWh.value) out.weihedian = pickedWh.value
  if (pickedHy.value) out.houchiyizheng = pickedHy.value
  if (pickedXj.value) out.xiajuangouzi = pickedXj.value
  if (zhangshu.value) out.zhangshu = zhangshu.value
  return out
}

// ============================================================
// SushiCards 事件：违和点
// ============================================================

function onWhPick(id: string) { whPickedId.value = id }
function onWhRerun(idx: number | null, directive: string) { loadSuggestions('weihedian', directive, idx) }
async function onWhCustom() {
  try {
    const { value: title } = await ElMessageBox.prompt('请输入自拟违和点的标题（10字内）', '自拟违和点', {
      inputPlaceholder: '例：电梯多停了一层',
      confirmButtonText: '下一步', cancelButtonText: '取消',
    })
    const { value: content } = await ElMessageBox.prompt('具体描述（80-120字，克苏鲁阴冷基调）', '自拟违和点', {
      inputType: 'textarea',
      inputPlaceholder: '例：唐迟加班到深夜回家，电梯在他没按的 13 楼停了一下，门开了，外面没有人……',
      confirmButtonText: '加入', cancelButtonText: '取消',
    })
    const { value: how } = await ElMessageBox.prompt('如何在间章中埋设？', '自拟违和点', {
      inputPlaceholder: '例：作为第一晚回家的微恐，只点到为止，不解释。',
      confirmButtonText: '加入', cancelButtonText: '取消',
    })
    const card: InterludeOption = {
      id: `wh-custom-${Date.now()}`, title: title || '自拟违和点',
      content: content || '', how_to_plant: how || '',
    }
    whOptions.value = [card, ...whOptions.value]
    whPickedId.value = card.id
    ElMessage.success('已加入自拟违和点')
  } catch { /* cancel */ }
}
async function onWhEdit(idx: number) {
  const o = whOptions.value[idx]; if (!o) return
  try {
    const { value: content } = await ElMessageBox.prompt('微调违和点描述', '编辑违和点', {
      inputType: 'textarea', inputValue: o.content || '',
      confirmButtonText: '保存', cancelButtonText: '取消',
    })
    whOptions.value[idx] = { ...o, content }
  } catch { /* cancel */ }
}

// ============================================================
// SushiCards 事件：后遗症
// ============================================================

function onHyPick(id: string) { hyPickedId.value = id }
function onHyRerun(idx: number | null, directive: string) { loadSuggestions('houchiyizheng', directive, idx) }
async function onHyCustom() {
  try {
    const { value: title } = await ElMessageBox.prompt('请输入自拟后遗症标题（10字内）', '自拟后遗症', {
      inputPlaceholder: '例：怕海',
      confirmButtonText: '下一步', cancelButtonText: '取消',
    })
    const { value: content } = await ElMessageBox.prompt('具体描述（80-120字，要有代价感）', '自拟后遗症', {
      inputType: 'textarea',
      inputPlaceholder: '例：唐迟发现自己路过水产市场会不自觉屏住呼吸，听见冲马桶的水声会出冷汗……',
      confirmButtonText: '加入', cancelButtonText: '取消',
    })
    const { value: how } = await ElMessageBox.prompt('如何在间章中展现？', '自拟后遗症', {
      inputPlaceholder: '例：3次递进展现，从轻到重。',
      confirmButtonText: '加入', cancelButtonText: '取消',
    })
    const card: InterludeOption = {
      id: `hy-custom-${Date.now()}`, title: title || '自拟后遗症',
      content: content || '', how_to_plant: how || '',
    }
    hyOptions.value = [card, ...hyOptions.value]
    hyPickedId.value = card.id
    ElMessage.success('已加入自拟后遗症')
  } catch { /* cancel */ }
}
async function onHyEdit(idx: number) {
  const o = hyOptions.value[idx]; if (!o) return
  try {
    const { value: content } = await ElMessageBox.prompt('微调后遗症描述', '编辑后遗症', {
      inputType: 'textarea', inputValue: o.content || '',
      confirmButtonText: '保存', cancelButtonText: '取消',
    })
    hyOptions.value[idx] = { ...o, content }
  } catch { /* cancel */ }
}

// ============================================================
// SushiCards 事件：下卷钩子
// ============================================================

function onXjPick(id: string) { xjPickedId.value = id }
function onXjRerun(idx: number | null, directive: string) { loadSuggestions('xiajuangouzi', directive, idx) }
async function onXjCustom() {
  try {
    const { value: title } = await ElMessageBox.prompt('请输入自拟下卷钩子标题（10字内）', '自拟下卷钩子', {
      inputPlaceholder: '例：门缝下的湿纸条',
      confirmButtonText: '下一步', cancelButtonText: '取消',
    })
    const { value: content } = await ElMessageBox.prompt('钩子具体表现（80-120字，让人脊背发凉）', '自拟下卷钩子', {
      inputType: 'textarea',
      inputPlaceholder: '例：唐迟凌晨回宿舍发现门缝下塞着一张湿纸条，纸条上是他自己的字迹……',
      confirmButtonText: '加入', cancelButtonText: '取消',
    })
    const { value: how } = await ElMessageBox.prompt('如何衔接下一卷？', '自拟下卷钩子', {
      inputPlaceholder: '例：章尾纸条落款日期是明天，当晚他被拉入副本。',
      confirmButtonText: '加入', cancelButtonText: '取消',
    })
    const card: InterludeOption = {
      id: `xj-custom-${Date.now()}`, title: title || '自拟下卷钩子',
      content: content || '', how_to_plant: how || '',
    }
    xjOptions.value = [card, ...xjOptions.value]
    xjPickedId.value = card.id
    ElMessage.success('已加入自拟下卷钩子')
  } catch { /* cancel */ }
}
async function onXjEdit(idx: number) {
  const o = xjOptions.value[idx]; if (!o) return
  try {
    const { value: content } = await ElMessageBox.prompt('微调钩子描述', '编辑下卷钩子', {
      inputType: 'textarea', inputValue: o.content || '',
      confirmButtonText: '保存', cancelButtonText: '取消',
    })
    xjOptions.value[idx] = { ...o, content }
  } catch { /* cancel */ }
}

// ============================================================
// 步骤导航：save-plan 自动保存
// ============================================================

async function saveStep(stepKey: InterludeStep, picked: InterludeOption | InterludeZhangshu) {
  if (!pid.value) return
  saving.value = true
  try {
    const res = await interludeApi.savePlan(pid.value, props.volNo, stepKey, picked)
    plan.value = res.plan
    ElMessage.success(`已保存：${stepLabelOf(stepKey)}`)
  } catch (e: any) {
    ElMessage.error(`保存失败：${e?.message || e}`)
    throw e
  } finally {
    saving.value = false
  }
}

function stepLabelOf(k: InterludeStep): string {
  const i = stepKeys.indexOf(k)
  return i >= 0 ? stepLabels[i] : k
}

async function goNext() {
  if (step.value === 0) {
    if (!pickedWh.value) { ElMessage.warning('请先选中一个违和点'); return }
    try {
      await saveStep('weihedian', pickedWh.value)
      step.value = 1
      if (hyOptions.value.length === 0) await loadSuggestions('houchiyizheng')
    } catch { /* err already shown */ }
    return
  }
  if (step.value === 1) {
    if (!pickedHy.value) { ElMessage.warning('请先选中一个后遗症'); return }
    try {
      await saveStep('houchiyizheng', pickedHy.value)
      step.value = 2
      if (xjOptions.value.length === 0) await loadSuggestions('xiajuangouzi')
    } catch { /* err */ }
    return
  }
  if (step.value === 2) {
    if (!pickedXj.value) { ElMessage.warning('请先选中一个下卷钩子'); return }
    try {
      await saveStep('xiajuangouzi', pickedXj.value)
      step.value = 3
      if (!zhangshu.value) await loadSuggestions('zhangshu')
    } catch { /* err */ }
    return
  }
  if (step.value === 3) {
    if (!zhangshu.value) { ElMessage.warning('请先生成章数建议'); return }
    const zs: InterludeZhangshu = {
      suggested_chapters: chapterCount.value,
      words_per_chapter: wordsPerChapter.value,
      reason: zhangshuReason.value || '作者手动调整',
    }
    try {
      await saveStep('zhangshu', zs)
      await doFinalize()
    } catch { /* err */ }
    return
  }
}

function goBack() {
  if (step.value > 0) step.value -= 1
}

// ============================================================
// Finalize
// ============================================================

async function doFinalize() {
  if (!pid.value) return
  finalizing.value = true
  try {
    const res = await interludeApi.finalize(pid.value, props.volNo)
    finalized.value = true
    ElMessage.success(`间章定稿！已创建 ${res.chapters_created?.length || 0} 章，开始写吧。`)
    // 稍等提示后 emit done
    setTimeout(() => emit('done'), 800)
  } catch (e: any) {
    ElMessage.error(`定稿失败：${e?.message || e}`)
  } finally {
    finalizing.value = false
  }
}
</script>

<template>
  <div class="interlude-view">
    <header class="interlude-header">
      <h2>
        <span class="rune">◐</span>
        蓝星间章 · 短暂的喘息
        <span class="vol-sub" v-if="volume?.title">《{{ volume.title }}》</span>
      </h2>
      <p class="interlude-sub">
        铜钟的余响还在骨头里，唐迟回到了蓝星。这里没有海声、没有血迹、没有死者——
        但总有一些东西，跟着他一起回来了。<br>
        四步定下这短暂的喘息：违和点 → 后遗症 → 下卷钩子 → 章数。
      </p>
      <div class="overview-meta">
        <el-tag size="small" effect="dark">第 {{ props.volNo }} 卷</el-tag>
        <el-tag size="small" type="info" effect="dark">蓝星间章</el-tag>
        <el-tag v-if="isMock" size="small" type="info" effect="plain">Mock 模式（克苏鲁硬编码）</el-tag>
        <el-tag v-if="plan.steps_done !== undefined" size="small" effect="plain">{{ plan.steps_done }}/4 步</el-tag>
      </div>
    </header>

    <el-steps :active="activeStep" finish-status="success" align-center class="interlude-steps">
      <el-step v-for="(lbl, i) in stepLabels" :key="i" :title="lbl" />
    </el-steps>

    <!-- ============================================================ -->
    <!-- Step 0 · 违和点 -->
    <!-- ============================================================ -->
    <div v-if="step === 0" class="interlude-step">
      <h3>Step 1 · 违和点</h3>
      <p class="hint">
        唐迟回到出租屋的第一晚/第一周，他最先察觉到的「不对劲」是什么？
        不是恐怖事件，而是日常里的微妙错位——镜子慢半拍、手机多了陌生号码、手心长出藤壶状的小疤。
      </p>

      <div v-if="whLoading" class="loading-hint">海水的回声正在爬上楼梯……</div>
      <SushiCards
        v-else-if="whOptions.length > 0"
        mode="ensemble"
        :items="whCards"
        :picked-id="whPickedId"
        axis-label="违和点候选"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :allow-custom="true"
        :allow-regenerate="true"
        :is-active="true"
        @pick="onWhPick"
        @rerun="onWhRerun"
        @custom-write="onWhCustom"
        @edit-card="onWhEdit"
      />
      <div v-else class="empty-tip">
        <el-button type="primary" @click="loadSuggestions('weihedian')">生成违和点候选</el-button>
      </div>

      <div class="step-actions">
        <span />
        <span class="step-hint" v-if="pickedWh">当前选中：{{ pickedWh.title }}</span>
        <el-button type="primary" :disabled="!pickedWh || saving" @click="goNext">下一步：后遗症 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 1 · 后遗症 -->
    <!-- ============================================================ -->
    <div v-else-if="step === 1" class="interlude-step">
      <h3>Step 2 · 后遗症</h3>
      <p class="hint">
        副本的代价不会在下一秒消失。梦里听见海声、背后有人叫他名字、影子永远慢半拍——
        这些残留会慢慢渗入唐迟的蓝星日常，像退潮后留在沙滩上的盐渍。
      </p>

      <div v-if="hyLoading" class="loading-hint">枕头下的潮气正在变重……</div>
      <SushiCards
        v-else-if="hyOptions.length > 0"
        mode="ensemble"
        :items="hyCards"
        :picked-id="hyPickedId"
        axis-label="后遗症候选"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :allow-custom="true"
        :allow-regenerate="true"
        :is-active="true"
        @pick="onHyPick"
        @rerun="onHyRerun"
        @custom-write="onHyCustom"
        @edit-card="onHyEdit"
      />
      <div v-else class="empty-tip">
        <el-button type="primary" @click="loadSuggestions('houchiyizheng')">生成后遗症候选</el-button>
      </div>

      <div class="step-actions">
        <el-button size="small" @click="goBack">← 返回违和点</el-button>
        <span class="step-hint" v-if="pickedHy">当前选中：{{ pickedHy.title }}</span>
        <el-button type="primary" :disabled="!pickedHy || saving" @click="goNext">下一步：下卷钩子 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 2 · 下卷钩子 -->
    <!-- ============================================================ -->
    <div v-else-if="step === 2" class="interlude-step">
      <h3>Step 3 · 下卷钩子</h3>
      <p class="hint">
        间章末尾，下一副本的召唤信号以什么方式出现？是一个不该存在的快递、一条和副本里死法一模一样的新闻，
        还是身边人开始说梦话叫出死者的名字？这一击打完，唐迟就没有回头路了。
      </p>

      <div v-if="xjLoading" class="loading-hint">某扇门正在蓝星的某个角落打开……</div>
      <SushiCards
        v-else-if="xjOptions.length > 0"
        mode="ensemble"
        :items="xjCards"
        :picked-id="xjPickedId"
        axis-label="下卷钩子候选"
        item-title-field="_title"
        item-subtitle-field="_subtitle"
        item-content-field="_content"
        item-badge-field="_badge"
        :plate-count="3"
        :allow-custom="true"
        :allow-regenerate="true"
        :is-active="true"
        @pick="onXjPick"
        @rerun="onXjRerun"
        @custom-write="onXjCustom"
        @edit-card="onXjEdit"
      />
      <div v-else class="empty-tip">
        <el-button type="primary" @click="loadSuggestions('xiajuangouzi')">生成下卷钩子候选</el-button>
      </div>

      <div class="step-actions">
        <el-button size="small" @click="goBack">← 返回后遗症</el-button>
        <span class="step-hint" v-if="pickedXj">当前选中：{{ pickedXj.title }}</span>
        <el-button type="primary" :disabled="!pickedXj || saving" @click="goNext">下一步：章数 →</el-button>
      </div>
    </div>

    <!-- ============================================================ -->
    <!-- Step 3 · 章数 -->
    <!-- ============================================================ -->
    <div v-else-if="step === 3" class="interlude-step">
      <h3>Step 4 · 章数</h3>
      <p class="hint">
        这趟短暂的喘息需要几章来写？章数太多会拖慢节奏，太少又铺不够违和感。
        AI 会根据前三步的选择给出建议，你可以拖动滑块调整。
      </p>

      <div v-if="zhangshuLoading" class="loading-hint">正在测算呼吸的长度……</div>
      <div v-else class="zhangshu-panel">
        <div class="slider-block">
          <label>间章章数</label>
          <el-slider v-model="chapterCount" :min="1" :max="3" :step="1" show-stops />
          <div class="slider-val">{{ chapterCount }} 章</div>
        </div>
        <div class="slider-block">
          <label>每章字数</label>
          <el-slider v-model="wordsPerChapter" :min="2500" :max="5000" :step="500" show-stops />
          <div class="slider-val">{{ wordsPerChapter.toLocaleString() }} 字/章</div>
        </div>
        <div class="total-box">
          <span class="total-label">总篇幅</span>
          <span class="total-val">{{ (chapterCount * wordsPerChapter).toLocaleString() }} 字</span>
        </div>

        <el-card shadow="never" class="reason-card">
          <template #header>
            <span class="reason-h">节奏理由</span>
            <el-button size="small" text type="primary" @click="loadSuggestions('zhangshu')" :loading="zhangshuLoading">
              重新生成
            </el-button>
          </template>
          <el-input
            v-model="zhangshuReason"
            type="textarea"
            :rows="4"
            placeholder="AI 给出的节奏理由，也可以自己改……"
          />
        </el-card>
      </div>

      <div v-if="!finalized" class="step-actions">
        <el-button size="small" @click="goBack">← 返回下卷钩子</el-button>
        <span class="step-hint">{{ chapterCount }} 章 × {{ wordsPerChapter }} 字</span>
        <el-button type="primary" size="large" :loading="finalizing || saving" @click="goNext">
          ❆ 定稿 · 进入间章
        </el-button>
      </div>

      <div v-else class="finalized-box">
        <el-result icon="success" title="间章已定稿" sub-title="门开了一条缝，唐迟还没察觉。开始写吧。">
          <template #extra>
            <el-button type="primary" @click="emit('done')">进入写作</el-button>
          </template>
        </el-result>
      </div>
    </div>
  </div>
</template>

<style scoped>
.interlude-view {
  padding: 20px 28px 40px;
  color: #cfd8e3;
  min-height: 100%;
  /* 冷蓝灰调：区别于 settle_routes 的血红/古铜 */
  background: radial-gradient(ellipse at top, #1c2430 0%, #141a23 45%, #0a0e14 100%);
}

.interlude-header {
  margin-bottom: 18px;
  border-bottom: 1px solid rgba(120, 150, 180, 0.2);
  padding-bottom: 14px;
}
.interlude-header h2 {
  margin: 0 0 6px;
  font-size: 22px;
  letter-spacing: 1px;
  color: #dfe6ef;
}
.interlude-header .rune {
  color: #7aa2c1;
  margin-right: 8px;
}
.interlude-header .vol-sub {
  font-size: 15px;
  color: #7aa2c1;
  font-weight: normal;
  margin-left: 6px;
}
.interlude-sub {
  margin: 0 0 10px;
  color: #8b99aa;
  font-size: 13px;
  line-height: 1.7;
}
.overview-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.overview-meta :deep(.el-tag) {
  background: rgba(20, 26, 35, 0.6);
  border-color: rgba(122, 162, 193, 0.3);
  color: #b6c4d3;
}

.interlude-steps {
  margin: 12px 0 24px;
}
.interlude-steps :deep(.el-step__title) {
  color: #94a3b5;
  font-size: 13px;
}
.interlude-steps :deep(.el-step__title.is-process) {
  color: #7aa2c1;
}
.interlude-steps :deep(.el-step__head.is-process) {
  color: #7aa2c1;
  border-color: #7aa2c1;
}
.interlude-steps :deep(.el-step__head.is-finish) {
  color: #577088;
  border-color: #577088;
}
.interlude-steps :deep(.el-step__title.is-finish) {
  color: #577088;
}

.interlude-step {
  background: rgba(20, 26, 35, 0.55);
  border: 1px solid rgba(122, 162, 193, 0.15);
  border-radius: 8px;
  padding: 20px 22px;
}
.interlude-step h3 {
  margin: 0 0 10px;
  color: #7aa2c1;
  font-size: 17px;
  letter-spacing: 1px;
}
.interlude-step .hint {
  color: #8b99aa;
  font-size: 13px;
  line-height: 1.7;
  margin: 0 0 16px;
}
.interlude-step .hint strong {
  color: #dfe6ef;
}

.loading-hint,
.empty-tip {
  text-align: center;
  color: #8b99aa;
  padding: 28px 0;
  font-size: 13px;
}

.step-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
  justify-content: space-between;
}
.step-actions .step-hint {
  color: #7aa2c1;
  font-size: 13px;
  flex: 1;
  text-align: center;
}

/* 章数面板 */
.zhangshu-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.slider-block {
  background: rgba(10, 14, 20, 0.5);
  border: 1px solid rgba(122, 162, 193, 0.15);
  border-radius: 6px;
  padding: 14px 18px;
}
.slider-block label {
  display: block;
  color: #7aa2c1;
  font-size: 13px;
  margin-bottom: 10px;
  letter-spacing: 0.5px;
}
.slider-block :deep(.el-slider__runway) {
  background-color: rgba(122, 162, 193, 0.15);
}
.slider-block :deep(.el-slider__bar) {
  background-color: #7aa2c1;
}
.slider-block :deep(.el-slider__button) {
  border-color: #7aa2c1;
}
.slider-block :deep(.el-slider__stop) {
  background-color: #3d5469;
}
.slider-val {
  text-align: right;
  color: #cfd8e3;
  font-size: 14px;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}

.total-box {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 10px 16px;
  background: rgba(122, 162, 193, 0.08);
  border-left: 3px solid #7aa2c1;
  border-radius: 3px;
}
.total-label {
  color: #7aa2c1;
  font-size: 13px;
}
.total-val {
  color: #dfe6ef;
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.reason-card {
  background: rgba(10, 14, 20, 0.6) !important;
  border: 1px solid rgba(122, 162, 193, 0.2) !important;
}
.reason-card :deep(.el-card__header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(122, 162, 193, 0.12);
}
.reason-h {
  color: #7aa2c1;
  font-size: 14px;
}
.reason-card :deep(.el-card__body) {
  padding: 14px;
}
.reason-card :deep(.el-textarea__inner) {
  background: rgba(10, 14, 20, 0.8);
  color: #cfd8e3;
  border-color: rgba(122, 162, 193, 0.2);
}

.finalized-box {
  margin-top: 18px;
  padding: 20px;
}
.finalized-box :deep(.el-result__title) {
  color: #dfe6ef;
}
.finalized-box :deep(.el-result__subtitle) {
  color: #8b99aa;
}
</style>
