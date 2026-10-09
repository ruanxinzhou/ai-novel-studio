<script setup lang="ts">
// 创作向导（M5 主路）：六站引导走一次即毕业。
// 每站同构三层：机器只亮红灯（缺什么就提示什么）→ AI 出多样本附理由 →
// 作者三出口（选一个 / 改一个 / 自己写）。所有生成都只是候选，采纳必须作者签收。
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  STATION_ORDER,
  STATION_LABELS,
  deskApi,
  projectsApi,
  runsApi,
  wizardApi,
  type DirectionCard,
  type LayerVersionFull,
  type WizardDetail,
} from '../api'
import { activeTab, currentProjectId } from '../state'

const detail = ref<WizardDetail | null>(null)
const loading = ref(false)
const station = ref('topic')

// ---- 选题站 ----
const ideaForm = ref({ context: '', desire: '', obstacle: '', text: '' })
const savingIdea = ref(false)
const introDraft = ref('')
const introRunning = ref(false)
const introAdopting = ref(false)

// ---- 脑洞站 ----
const seedForm = ref('')
const cards = ref<DirectionCard[]>([])
const cardsRunning = ref(false)
const editVisible = ref(false)
const editText = ref('')
const editSource = ref<'edited' | 'manual'>('edited')
const savingDirection = ref(false)

// ---- 架构站 ----
const nChapters = ref(12)
const outlineDraft = ref('')
const outlineRunning = ref(false)
const outlineAdopting = ref(false)
// 架构站流式状态：思考 / 正文增量 / 已完成章 / 提示
const outlineLiveThinking = ref('')
const outlineLiveText = ref('')
const outlineDone = ref<number[]>([])
const outlineNotices = ref<string[]>([])

const currentIntro = ref<LayerVersionFull | null>(null)
const currentOutline = ref<LayerVersionFull | null>(null)

const stationIndex = computed(() => {
  const s = station.value
  return STATION_ORDER.indexOf(s as (typeof STATION_ORDER)[number])
})

async function refresh() {
  if (!currentProjectId.value) {
    detail.value = null
    return
  }
  loading.value = true
  try {
    const pid = currentProjectId.value
    const d = await wizardApi.detail(pid)
    detail.value = d
    ideaForm.value = { ...d.wizard.idea }
    seedForm.value = d.wizard.seed
    if (d.wizard.n_chapters > 0) nChapters.value = d.wizard.n_chapters
    if (!station.value || d.wizard.skipped.includes(station.value)) {
      station.value = d.current_station ?? 'topic'
    }
    const intro = await projectsApi.layer(pid, 'intro')
    currentIntro.value = intro.version
    const outline = await projectsApi.layer(pid, 'outline')
    currentOutline.value = outline.version
  } catch (e) {
    ElMessage.error(`向导状态加载失败：${(e as Error).message}`)
  } finally {
    loading.value = false
  }
}

onMounted(refresh)
watch(currentProjectId, () => {
  station.value = 'topic'
  refresh()
})

function goStation(s: string) {
  station.value = s
}

function goDesk(chapter?: number) {
  activeTab.value = 'desk'
  if (chapter) deskFocus.value = chapter
}
// 与章台联动的目标章（DeskView 读取后清零）
const deskFocus = (window as unknown as { __deskFocus?: { value: number } }).__deskFocus ?? { value: 0 }
;(window as unknown as { __deskFocus?: { value: number } }).__deskFocus = deskFocus

// ---- 选题 ----
async function saveIdea() {
  savingIdea.value = true
  try {
    await wizardApi.update(currentProjectId.value, { idea: ideaForm.value })
    ElMessage.success('已保存')
    await refresh()
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    savingIdea.value = false
  }
}

async function expandIntro() {
  if (!ideaForm.value.text.trim()) {
    ElMessage.warning('请先写一句话点子')
    return
  }
  introRunning.value = true
  try {
    await wizardApi.update(currentProjectId.value, { idea: ideaForm.value })
    const r = await runsApi.run('intro', {
      x_pairs: [[ideaForm.value.text, '']],
      project: currentProjectId.value,
    })
    introDraft.value = r.y
    ElMessage.success('简介已生成（机器提议，可改可自写）')
  } catch (e) {
    ElMessage.error(`生成失败：${(e as Error).message}`)
  } finally {
    introRunning.value = false
  }
}

async function adoptIntro() {
  if (!introDraft.value.trim()) return
  introAdopting.value = true
  try {
    await projectsApi.adopt(
      currentProjectId.value, 'intro', introDraft.value,
      [[ideaForm.value.text, '']], '创作向导·选题站',
    )
    ElMessage.success('简介已采纳，选题站完成')
    introDraft.value = ''
    await refresh()
  } catch (e) {
    ElMessage.error(`采纳失败：${(e as Error).message}`)
  } finally {
    introAdopting.value = false
  }
}

// ---- 脑洞 ----
async function generateCards() {
  cardsRunning.value = true
  try {
    await wizardApi.update(currentProjectId.value, { seed: seedForm.value })
    const r = await wizardApi.brainstorm(currentProjectId.value)
    cards.value = r.cards
    ElMessage.success(`已生成 ${r.cards.length} 个方向（先问后给：挑一个或自己写）`)
  } catch (e) {
    ElMessage.error(`方向卡生成失败：${(e as Error).message}`)
  } finally {
    cardsRunning.value = false
  }
}

function cardText(c: DirectionCard): string {
  return `【${c.direction}】\n${c.premise}\n金手指/核心冲突：${c.hook}`
}

async function pickCard(c: DirectionCard) {
  await saveDirection(cardText(c), 'picked')
}

function openEditCard(c: DirectionCard | null) {
  editText.value = c ? cardText(c) : ''
  editSource.value = c ? 'edited' : 'manual'
  editVisible.value = true
}

async function saveDirection(text: string, source: string) {
  savingDirection.value = true
  try {
    await wizardApi.update(currentProjectId.value, { direction: text, direction_source: source })
    editVisible.value = false
    ElMessage.success('方向已签收')
    await refresh()
  } catch (e) {
    ElMessage.error(`签收失败：${(e as Error).message}`)
  } finally {
    savingDirection.value = false
  }
}

async function saveDirectionFromDialog() {
  if (!editText.value.trim()) {
    ElMessage.warning('方向不能为空')
    return
  }
  await saveDirection(editText.value, editSource.value)
}

async function toggleSkip(s: string) {
  try {
    const d = await wizardApi.skip(currentProjectId.value, s)
    detail.value = d
    ElMessage.success(d.wizard.skipped.includes(s) ? `已跳过「${STATION_LABELS[s]}」，可随时回来补` : '已取消跳过')
  } catch (e) {
    ElMessage.error(`操作失败：${(e as Error).message}`)
  }
}

// ---- 架构 ----
const chaptersSaving = ref(false)
async function saveChapters() {
  chaptersSaving.value = true
  try {
    await wizardApi.update(currentProjectId.value, { n_chapters: nChapters.value })
    ElMessage.success('章数已保存')
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    chaptersSaving.value = false
  }
}

async function generateOutline() {
  if (nChapters.value < 1) {
    ElMessage.warning('请先设定章数')
    return
  }
  outlineRunning.value = true
  outlineLiveThinking.value = ''
  outlineLiveText.value = ''
  outlineDone.value = []
  outlineNotices.value = []
  try {
    await wizardApi.update(currentProjectId.value, { n_chapters: nChapters.value })
    const materials: { source_id: string; family: string; content: string; title: string }[] = []
    if (currentIntro.value) {
      materials.push({ source_id: 'intro', family: 'finalized', content: currentIntro.value.y, title: '故事简介' })
    }
    const d = detail.value
    if (d?.wizard.direction) {
      materials.push({ source_id: 'wizard:direction', family: 'reference', content: d.wizard.direction, title: '故事方向（作者已签收）' })
    }
    // 流式生成：每章一块，逐章实时上屏（大纲慢在章数×单次调用，实时可见不焦虑）
    const r = await runsApi.stream('outline', {
      x_pairs: Array.from({ length: nChapters.value }, (_, i) => [`第${i + 1}章\n`, ''] as [string, string]),
      materials,
      x_chunk_length: 40,
      user_directive: `共 ${nChapters.value} 章。`,
      project: currentProjectId.value,
    }, {
      onThinking: (t) => {
        outlineLiveThinking.value += t
      },
      onDelta: (t) => {
        outlineLiveText.value += t
      },
      onBlockDone: (i) => {
        outlineDone.value.push(i)
      },
      onNotice: (n) => {
        outlineNotices.value.push(n)
      },
    })
    outlineDraft.value = r.y
    ElMessage.success('大纲已生成（机器提议，可改可自写）')
  } catch (e) {
    ElMessage.error(`生成失败：${(e as Error).message}`)
  } finally {
    outlineRunning.value = false
  }
}

async function adoptOutline() {
  if (!outlineDraft.value.trim()) return
  outlineAdopting.value = true
  try {
    await projectsApi.adopt(
      currentProjectId.value, 'outline', outlineDraft.value, [],
      `创作向导·架构站（${nChapters.value} 章）`,
    )
    ElMessage.success('大纲已采纳，架构站完成')
    outlineDraft.value = ''
    await refresh()
  } catch (e) {
    ElMessage.error(`采纳失败：${(e as Error).message}`)
  } finally {
    outlineAdopting.value = false
  }
}

async function loadFirstChapterStatus() {
  if (!currentProjectId.value) return null
  try {
    return await deskApi.detail(currentProjectId.value)
  } catch {
    return null
  }
}
const firstDesk = ref<{ finalized: number; total: number } | null>(null)
watch(station, async (s) => {
  if (['plotplan', 'writing', 'polish'].includes(s)) {
    firstDesk.value = await loadFirstChapterStatus()
  }
}, { immediate: true })
</script>

<template>
  <div>
    <el-card v-if="!currentProjectId" shadow="never">
      <el-empty description="先在「项目管理」新建或进入一本书，向导会带你六步开书">
        <el-button type="primary" @click="activeTab = 'projects'">去项目管理</el-button>
      </el-empty>
    </el-card>

    <template v-else>
      <!-- 六站步条 -->
      <el-card shadow="never" style="margin-bottom: 12px" v-loading="loading">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
          <span style="font-weight: 600">创作向导</span>
          <el-tag v-if="detail?.graduated" type="success">已毕业 · 进入连载期</el-tag>
          <el-tag v-else-if="detail?.current_station" type="primary">
            当前：{{ STATION_LABELS[detail!.current_station!] }}
          </el-tag>
        </div>
        <el-steps :active="stationIndex" align-center finish-status="success">
          <el-step
            v-for="s in detail?.stations ?? []"
            :key="s.station"
            :title="s.label"
            :status="s.done ? 'success' : s.skipped ? 'wait' : (s.station === station ? 'process' : 'wait')"
            :class="{ clickable: true, skipped: s.skipped }"
            style="cursor: pointer"
            @click="goStation(s.station)"
          />
        </el-steps>
        <div v-if="detail && !detail.gates_ok" style="margin-top: 8px">
          <el-alert type="warning" :closable="false" show-icon
            title="开工判据未齐：情境、欲望、阻碍三格还有空——不着急，什么时候齐了什么时候开工" />
        </div>
      </el-card>

      <!-- 选题站 -->
      <el-card v-show="station === 'topic'" shadow="never">
        <template #header><b>① 选题：把一句话点子立住</b></template>
        <el-alert v-if="!detail?.gates_ok" type="info" :closable="false" style="margin-bottom: 10px"
          title="三格判据：什么世界（情境）· 主角要什么（欲望）· 什么拦着他（阻碍）。三格齐了才开工，不设期限。" />
        <el-form label-width="80px" label-position="left">
          <el-form-item label="情境">
            <el-input v-model="ideaForm.context" placeholder="什么世界/背景？如：修仙世界，灵根决定一切" />
          </el-form-item>
          <el-form-item label="欲望">
            <el-input v-model="ideaForm.desire" placeholder="主角要什么？如：一个凡人想逆天改命求长生" />
          </el-form-item>
          <el-form-item label="阻碍">
            <el-input v-model="ideaForm.obstacle" placeholder="什么拦着他？如：寿元大限与强敌环伺" />
          </el-form-item>
          <el-form-item label="一句话点子">
            <el-input v-model="ideaForm.text" type="textarea" :rows="2"
              placeholder="一句话说清这本书（可空，之后在创作导航的简介层生成）" />
          </el-form-item>
        </el-form>
        <div style="display: flex; gap: 8px; margin-bottom: 12px">
          <el-button :loading="savingIdea" @click="saveIdea">保存三格与点子</el-button>
          <el-button type="primary" :loading="introRunning" @click="expandIntro">AI 扩写为简介</el-button>
          <el-button plain @click="toggleSkip('topic')">跳过本站</el-button>
        </div>
        <template v-if="introDraft">
          <div style="margin-bottom: 6px; color: #909399; font-size: 13px">机器提议（候选，可改可自写）：</div>
          <el-input v-model="introDraft" type="textarea" :rows="6" />
          <div style="margin-top: 8px">
            <el-button type="success" :loading="introAdopting" @click="adoptIntro">采纳这份简介</el-button>
          </div>
        </template>
        <el-alert v-else-if="currentIntro" type="success" :closable="false"
          :title="`选题站已完成（简介 v${currentIntro.seq} 已采纳）。可回到本站重新生成再采纳覆盖。`" />
      </el-card>

      <!-- 脑洞站 -->
      <el-card v-show="station === 'brainstorm'" shadow="never">
        <template #header><b>② 脑洞：五个方向里挑一个，或自己写</b></template>
        <el-input v-model="seedForm" type="textarea" :rows="2" style="margin-bottom: 8px"
          placeholder="你的种子：想写什么类型？主角什么设定？想要的爽点？（先问后给，AI 按你的种子出方向）" />
        <div style="display: flex; gap: 8px; margin-bottom: 12px">
          <el-button type="primary" :loading="cardsRunning" @click="generateCards">给我 5 个方向</el-button>
          <el-button plain @click="openEditCard(null)">自己写一个方向</el-button>
          <el-button plain @click="toggleSkip('brainstorm')">跳过本站</el-button>
        </div>
        <el-row v-if="cards.length" :gutter="10">
          <el-col v-for="(c, i) in cards" :key="i" :xs="24" :sm="12" :md="8" style="margin-bottom: 10px">
            <el-card shadow="hover" :body-style="{ padding: '12px' }">
              <b>{{ c.direction }}</b>
              <div style="font-size: 12px; color: #888; margin: 6px 0">{{ c.reason }}</div>
              <div style="font-size: 13px; margin-bottom: 6px">钩子：{{ c.hook }}</div>
              <div style="font-size: 13px; color: #555; min-height: 48px">{{ c.premise }}</div>
              <div style="display: flex; gap: 6px">
                <el-button size="small" type="primary" @click="pickCard(c)">用这个</el-button>
                <el-button size="small" plain @click="openEditCard(c)">改一改</el-button>
              </div>
            </el-card>
          </el-col>
        </el-row>
        <el-alert v-if="detail?.wizard.direction" type="success" :closable="false" style="margin-top: 8px"
          :title="`方向已签收（${detail.wizard.direction_source === 'manual' ? '自写' : detail.wizard.direction_source === 'edited' ? '改写' : '选用'}）：${detail.wizard.direction.slice(0, 50)}`" />
      </el-card>

      <!-- 架构站 -->
      <el-card v-show="station === 'architecture'" shadow="never">
        <template #header><b>③ 故事架构：全书大纲</b></template>
        <div style="display: flex; gap: 10px; align-items: center; margin-bottom: 10px; flex-wrap: wrap">
          <span>计划章数：</span>
          <el-input-number v-model="nChapters" :min="1" :max="2000" />
          <el-button :loading="chaptersSaving" @click="saveChapters">保存</el-button>
          <el-button type="primary" :loading="outlineRunning" @click="generateOutline">生成全书大纲</el-button>
          <el-button plain @click="toggleSkip('architecture')">跳过本站</el-button>
          <span style="color: #aaa; font-size: 12px">简介与已签收方向会自动带入；长篇可先只立 20-50 章的主线，后续加卷。</span>
        </div>
        <div v-if="outlineRunning || outlineLiveText || outlineLiveThinking" class="outline-live">
          <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px">
            <b style="font-size: 13px">实时生成</b>
            <el-tag size="small" type="primary">第 {{ (outlineDone.at(-1) ?? 0) + 1 }} 章生成中…</el-tag>
            <el-tag v-for="i in outlineDone" :key="`od${i}`" size="small" type="success">第 {{ i + 1 }} 章完成</el-tag>
            <span v-for="(n, i) in outlineNotices" :key="i" style="font-size: 12px; color: #e6a23c">{{ n }}</span>
            <span style="font-size: 12px; color: #999">章数越多越久，每章一次调用；中断重跑只补缺的章</span>
          </div>
          <el-collapse v-if="outlineLiveThinking" style="margin-bottom: 8px">
            <el-collapse-item title="思考过程" name="think">
              <pre class="outline-thinking">{{ outlineLiveThinking }}</pre>
            </el-collapse-item>
          </el-collapse>
          <pre v-if="outlineLiveText" style="margin: 0; white-space: pre-wrap; font-family: inherit; line-height: 1.8; font-size: 13px">{{ outlineLiveText }}</pre>
        </div>
        <template v-if="outlineDraft">
          <div style="margin-bottom: 6px; color: #909399; font-size: 13px">机器提议（每段以「第N章」开头，可改）：</div>
          <el-input v-model="outlineDraft" type="textarea" :rows="14" />
          <div style="margin-top: 8px">
            <el-button type="success" :loading="outlineAdopting" @click="adoptOutline">采纳这份大纲</el-button>
          </div>
        </template>
        <el-alert v-else-if="currentOutline" type="success" :closable="false"
          :title="`架构站已完成（大纲 v${currentOutline.seq} 已采纳）。可重新生成再采纳覆盖。`" />
        <el-alert v-else type="info" :closable="false" title="还没有大纲。设定章数后点「生成全书大纲」。" />
      </el-card>

      <!-- 剧情 / 成稿 / 润色：首章微循环入口 -->
      <el-card v-show="['plotplan', 'writing', 'polish'].includes(station)" shadow="never">
        <template #header><b>{{ STATION_LABELS[station] }}：在今日章台完成第一章</b></template>
        <el-alert type="info" :closable="false" style="margin-bottom: 10px"
          title="从这一站起进入单章微循环：剧情 → 成稿 → 审稿 → 润色 → 定稿。第一章走通，向导即毕业；之后天天在章台循环。" />
        <div v-if="firstDesk" style="margin-bottom: 12px; color: #666; font-size: 13px">
          章台进度：{{ firstDesk.finalized }} / {{ firstDesk.total }} 章已定稿
        </div>
        <el-button type="primary" size="large" @click="goDesk(1)">去今日章台 · 做第 1 章</el-button>
      </el-card>

      <!-- 毕业面板 -->
      <el-card v-show="detail?.graduated" shadow="never">
        <el-result icon="success" title="开书完成，向导毕业" sub-title="开书期结束，进入连载期：日更在「今日章台」循环">
          <template #extra>
            <el-button type="primary" size="large" @click="goDesk()">进入今日章台</el-button>
          </template>
        </el-result>
      </el-card>
    </template>

    <!-- 方向卡改写/自写 -->
    <el-dialog v-model="editVisible" :title="editSource === 'edited' ? '改写这个方向' : '自己写一个方向'" width="520">
      <el-input v-model="editText" type="textarea" :rows="6"
        placeholder="方向名 + 梗概 + 金手指/核心冲突（自己写的和选的一样是一级公民）" />
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingDirection" @click="saveDirectionFromDialog">签收这个方向</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
:deep(.el-step.skipped .el-step__title) {
  color: #e6a23c;
}
.clickable :deep(.el-step__title) {
  cursor: pointer;
}
.outline-live {
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  max-height: 380px;
  overflow: auto;
  background: #fafcff;
}
.outline-thinking {
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 12px;
  color: #909399;
  background: #f7f8fa;
  border-radius: 6px;
  padding: 10px 12px;
  max-height: 200px;
  overflow: auto;
  line-height: 1.7;
  margin: 0;
}
</style>
