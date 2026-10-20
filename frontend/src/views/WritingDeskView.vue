<script setup lang="ts">
// 全局写作台：左栏章节树 + 右栏复用 DeskView（自由编辑模式，不推进 phase）
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Close } from '@element-plus/icons-vue'
import { deskApi } from '../api'
import { currentProjectId } from '../state'
import DeskView from './DeskView.vue'

interface SpineVolume {
  no: number
  title: string
  kind?: 'mainline' | 'dungeon' | 'interlude' | string
  chapters?: SpineChapter[]
}
interface SpineChapter {
  no: number
  title: string
  volume_no?: number
  field_no?: number
  chapter_kind?: string
  day?: number | null
  chars?: number
  finalized?: boolean
  finalized_at?: string | null
}

const props = defineProps<{
  initialChapterNo?: number | null
}>()

const loading = ref(false)
const volumes = ref<SpineVolume[]>([])
const chaptersMap = ref<Record<number, SpineChapter[]>>({}) // volume_no -> chapters
const chaptersByNo = ref<Record<number, SpineChapter>>({}) // chapter_no -> chapter
const selectedNo = ref<number>(0)
const deskKey = ref(0) // 切章时强制刷新 DeskView

onMounted(async () => {
  await loadSpine()
  // 若指定了初始章，加载完后自动选中
  const init = Number(props.initialChapterNo)
  if (init && chaptersByNo.value[init]) {
    selectChapter(init)
  } else if (!selectedNo.value) {
    // 默认选中第一章（若存在）
    const firstVol = volumes.value[0]
    if (firstVol) {
      const firstChs = chaptersMap.value[firstVol.no]
      if (firstChs && firstChs.length) selectChapter(firstChs[0].no)
    }
  }
})

const selectedChapter = computed(() =>
  selectedNo.value ? chaptersByNo.value[selectedNo.value] ?? null : null,
)
const selectedVolume = computed(() => {
  const c = selectedChapter.value
  if (!c) return null
  return volumes.value.find((v) => v.no === c.volume_no) ?? null
})
const crumbTitle = computed(() => {
  const v = selectedVolume.value
  const c = selectedChapter.value
  if (!c) return ''
  const vPart = v ? `第${v.no}卷 ${v.title || ''}` : ''
  return `${vPart} / 第${c.no}章 ${c.title || '（未命名）'}`
})

function volumeIcon(kind?: string): string {
  if (kind === 'mainline') return '🌍'
  if (kind === 'interlude') return '🌌'
  return '📕'
}
function volumeKindLabel(kind?: string): string {
  if (kind === 'mainline') return '蓝星'
  if (kind === 'interlude') return '间章'
  return '副本'
}

function wordStatus(chars: number | null | undefined): { cls: string; tip: string } {
  const n = Number(chars) || 0
  if (!n) return { cls: 'wc-none', tip: '' }
  if (n < 3500) return { cls: 'wc-short', tip: `过短（<3500）` }
  if (n > 5500) return { cls: 'wc-long', tip: `过长（>5500）` }
  if (n >= 4000 && n <= 5000) return { cls: 'wc-ok', tip: '达标（4000-5000）' }
  if (n < 4000) return { cls: 'wc-near-low', tip: '略短（3500-3999）' }
  return { cls: 'wc-near-high', tip: '略长（5001-5500）' }
}

async function loadSpine() {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  try {
    const data = await deskApi.spine(pid)
    // 后端返回字段：volumes[*]{volume_no, volume_kind, title, field_no, start_chapter_no, stage_completed}
    //                 chapters[*]{no, title, field_no, volume_no, chapter_kind, day, is_interlude, finalized_at, payoff, word_count}
    // 这里归一化成前端内部使用的 {no, kind, finalized} 字段，避免模板里到处写两套名字
    const rawVols = (data.volumes ?? []) as any[]
    volumes.value = rawVols.map((v: any) => ({
      no: v.volume_no ?? v.no ?? 0,
      title: v.title ?? '',
      kind: v.volume_kind ?? v.kind ?? 'dungeon',
      field_no: v.field_no,
      start_chapter_no: v.start_chapter_no,
      stage_completed: v.stage_completed,
    }))
    const allChapters = (data.chapters ?? []) as any[]
    const byVol: Record<number, SpineChapter[]> = {}
    const byNo: Record<number, SpineChapter> = {}
    for (const raw of allChapters) {
      const c: SpineChapter = {
        no: raw.no,
        title: raw.title ?? '',
        volume_no: raw.volume_no,
        field_no: raw.field_no,
        chapter_kind: raw.chapter_kind,
        day: raw.day,
        chars: raw.word_count ?? raw.chars ?? 0,
        finalized: !!raw.finalized_at,
        finalized_at: raw.finalized_at,
      }
      const vn = c.volume_no ?? c.field_no ?? 0
      if (vn <= 0) continue
      if (!byVol[vn]) byVol[vn] = []
      byVol[vn].push(c)
      byNo[c.no] = c
    }
    // 章按 no 升序
    for (const k of Object.keys(byVol)) {
      byVol[Number(k)].sort((a, b) => a.no - b.no)
    }
    chaptersMap.value = byVol
    chaptersByNo.value = byNo
  } catch (e: any) {
    ElMessage.error(`加载章节树失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

function selectChapter(no: number) {
  selectedNo.value = no
  deskKey.value += 1
}

function closeChapter() {
  selectedNo.value = 0
}

async function handleInsertAfter(afterNo: number, volumeNo: number) {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const { value: title } = await ElMessageBox.prompt('新章节标题（可留空）', '在后面插入新章', {
      confirmButtonText: '插入',
      cancelButtonText: '取消',
      inputValue: '',
      inputPlaceholder: '例如：蓝星清晨',
    })
    const resp = await deskApi.insertChapter(pid, {
      after_no: afterNo,
      title: title || '',
      field_no: volumeNo,
    })
    ElMessage.success(`已插入第 ${resp.no} 章`)
    await loadSpine()
    selectChapter(resp.no)
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(`插入失败：${e?.message || e}`)
  }
}

async function handleInsertAtVolumeTail(volumeNo: number) {
  // 卷尾插入：找该卷最后一章 no 作为 after_no；卷无章则 after_no = 0 由后端按卷定位
  const list = chaptersMap.value[volumeNo] ?? []
  const afterNo = list.length ? list[list.length - 1].no : 0
  // 若 after_no=0，后端会按 field_no 插到该卷最前；卷尾我们期望插入到末尾，因此需要传 last no
  return handleInsertAfter(afterNo, volumeNo)
}

async function handleRename(c: SpineChapter) {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const { value } = await ElMessageBox.prompt('修改章标题', '重命名', {
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValue: c.title || '',
    })
    await deskApi.patchChapter(pid, c.no, { title: value })
    ElMessage.success('已更新')
    await loadSpine()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(`重命名失败：${e?.message || e}`)
  }
}

async function handleDelete(c: SpineChapter) {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    await ElMessageBox.confirm(
      `确定删除第 ${c.no} 章「${c.title || '（未命名）'}」？此操作不可恢复。`,
      '删除章节',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
    await deskApi.deleteChapter(pid, c.no)
    ElMessage.success('已删除')
    if (selectedNo.value === c.no) selectedNo.value = 0
    await loadSpine()
  } catch (e: any) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(`删除失败：${e?.message || e}`)
  }
}

// DeskView 事件：中台模式下不推进 phase
function onNextChapter(no: number) {
  // 在本视图内切换到下一章（按 no 顺序找）
  if (chaptersByNo.value[no]) {
    selectChapter(no)
  } else {
    // 下一章尚未生成/不存在，刷新书脊再试
    loadSpine().then(() => {
      if (chaptersByNo.value[no]) selectChapter(no)
      else ElMessage.info('下一章暂不存在')
    })
  }
}
function onReopenBrief() {
  ElMessage.info('全局写作台不进入章前规划，直接在右侧编辑即可')
}
function onToSettle() {
  ElMessage.info('全局写作台不推进到杀青阶段')
}

</script>

<template>
  <div class="writing-desk">
    <!-- 左栏：章节树 -->
    <aside class="wd-sidebar">
      <div class="wd-side-header">
        <span class="wd-side-title">✎ 全局写作台</span>
        <el-button
          :icon="Refresh"
          size="small"
          text
          :loading="loading"
          @click="loadSpine"
          title="刷新"
        />
      </div>

      <div v-if="loading && !volumes.length" class="wd-empty">加载中…</div>
      <div v-else-if="!volumes.length" class="wd-empty">暂无章节</div>

      <div v-else class="wd-tree">
        <div v-for="v in volumes" :key="v.no" class="wd-volume">
          <div class="wd-vol-head">
            <span class="wd-vol-icon">{{ volumeIcon(v.kind) }}</span>
            <span class="wd-vol-title">第{{ v.no }}卷 {{ v.title || '（未命名）' }}</span>
            <el-tag size="small" type="info" class="wd-vol-kind">{{ volumeKindLabel(v.kind) }}</el-tag>
            <el-button
              class="wd-vol-add"
              type="primary"
              link
              size="small"
              @click="handleInsertAtVolumeTail(v.no)"
            >
              +新章
            </el-button>
          </div>
          <div v-if="chaptersMap[v.no]?.length" class="wd-chapters">
            <div
              v-for="c in chaptersMap[v.no]"
              :key="c.no"
              class="wd-chapter"
              :class="{ active: c.no === selectedNo }"
              @click="selectChapter(c.no)"
            >
              <span class="wc-dot" :class="{ finalized: c.finalized || c.finalized_at }"></span>
              <span class="wc-no">{{ c.no }}.</span>
              <span class="wc-t">{{ c.title || '（未命名）' }}</span>
              <span class="wc-tag" :class="wordStatus(c.chars).cls" :title="wordStatus(c.chars).tip">
                {{ c.chars ? c.chars + '字' : '—' }}
              </span>
              <span class="wc-actions" @click.stop>
                <el-button link size="small" @click="handleInsertAfter(c.no, v.no)" title="在后面插入新章">+</el-button>
                <el-button link size="small" @click="handleRename(c)" title="重命名">✎</el-button>
                <el-button link size="small" type="danger" @click="handleDelete(c)" title="删除">✕</el-button>
              </span>
            </div>
          </div>
          <div v-else class="wd-vol-empty">（暂无章节）</div>
        </div>
      </div>
    </aside>

    <!-- 右栏：章编辑器 / 空态 -->
    <section class="wd-main">
      <template v-if="selectedChapter">
        <div class="wd-crumb">
          <span class="wd-crumb-text">{{ crumbTitle }}</span>
          <el-button :icon="Close" size="small" text @click="closeChapter" title="关闭">关闭</el-button>
        </div>
        <div class="wd-desk-wrap">
          <DeskView
            :key="deskKey"
            :chapter-no="selectedNo"
            :free-standing="true"
            @reopen-brief="onReopenBrief"
            @next-chapter="onNextChapter"
            @to-settle="onToSettle"
          />
        </div>
      </template>
      <div v-else class="wd-empty-main">
        <div class="wd-empty-hint">✎</div>
        <div class="wd-empty-text">从左侧选择章节开始编辑</div>
        <div class="wd-empty-sub">全局写作台为自由编辑模式，不自动推进阶段</div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.writing-desk {
  display: flex;
  height: 100%;
  min-height: 0;
  background: #fff;
}

/* ---- 左栏 ---- */
.wd-sidebar {
  width: 300px;
  flex-shrink: 0;
  background: #fafafa;
  border-right: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.wd-side-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 8px;
  border-bottom: 1px solid #ebeef5;
}
.wd-side-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}
.wd-empty {
  padding: 24px 12px;
  color: #909399;
  font-size: 13px;
  text-align: center;
}
.wd-tree {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0 12px;
}

.wd-volume {
  margin-bottom: 4px;
}
.wd-vol-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  padding: 8px 10px;
  color: #303133;
}
.wd-vol-icon {
  font-size: 14px;
}
.wd-vol-title {
  flex: 1;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wd-vol-kind {
  flex-shrink: 0;
}
.wd-vol-add {
  flex-shrink: 0;
  font-size: 12px;
}
.wd-vol-empty {
  padding: 4px 10px 8px 28px;
  font-size: 12px;
  color: #c0c4cc;
}

.wd-chapters {
  padding-bottom: 4px;
}
.wd-chapter {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 10px;
  font-size: 13px;
  color: #606266;
  cursor: pointer;
  border-left: 2px solid transparent;
}
.wd-chapter:hover {
  background: #f0f7ff;
}
.wd-chapter.active {
  background: #ecf5ff;
  border-left-color: #409eff;
  color: #409eff;
}
.wc-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #dcdfe6;
  flex-shrink: 0;
}
.wc-dot.finalized {
  background: #67c23a;
}
.wc-no {
  color: #909399;
  flex-shrink: 0;
  min-width: 28px;
}
.wc-t {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wc-tag {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
  flex-shrink: 0;
}
.wc-none { color: #c0c4cc; }
.wc-short { color: #b88230; background: #fdf6ec; }
.wc-near-low { color: #b88230; }
.wc-ok { color: #67c23a; background: #f0f9eb; }
.wc-near-high { color: #b88230; }
.wc-long { color: #ff8c00; background: #fff3e6; }

.wc-actions {
  display: none;
  gap: 2px;
  flex-shrink: 0;
  margin-left: 4px;
}
.wd-chapter:hover .wc-actions {
  display: inline-flex;
}
.wc-actions .el-button {
  padding: 2px 4px;
  font-size: 12px;
}

/* ---- 右栏 ---- */
.wd-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.wd-crumb {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  border-bottom: 1px solid #ebeef5;
  background: #fafcff;
}
.wd-crumb-text {
  font-size: 13px;
  color: #606266;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wd-desk-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.wd-empty-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #909399;
  gap: 8px;
}
.wd-empty-hint {
  font-size: 56px;
  opacity: 0.3;
}
.wd-empty-text {
  font-size: 15px;
  color: #606266;
}
.wd-empty-sub {
  font-size: 12px;
  color: #c0c4cc;
}
</style>
