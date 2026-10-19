<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import PrevStepPanel from '../components/PrevStepPanel.vue'
import { rhythmApi } from '../api'
import type { RhythmBlueprint, DensityMap, RhythmBeat } from '../api'
import { currentProjectId } from '../state'
import { provideChatContext } from '../chatContext'

const props = defineProps<{
  volNo: number
}>()

const emit = defineEmits<{
  (e: 'done'): void
}>()

// ---- state ----
const blueprint = ref<RhythmBlueprint | null>(null)
const densityMap = ref<DensityMap | null>(null)
const volume = ref<any>(null)
const loading = ref(false)
const finalizing = ref(false)
const isMock = ref(false)
const rhythmDirective = ref('')

// 注册节奏骨架聊天上下文
provideChatContext(() => {
  const bp = blueprint.value as any
  const dm = densityMap.value as any
  return {
    phase: '📅 节奏骨架',
    system_hint:
      '作者在唐迟入戏第1-7天（T-6→T-0，这是唐迟个人七天求生倒计时，不是原剧本的Day1-Day7）上排事件、气氛曲线、密度、必现标记。'
      + '【双轨时间线】每条 day_beat 要同时标注「原剧本此刻正在哪个事件」，让作者清楚原剧本事件流和唐迟倒计时并行。'
      + '回答时注意三折死因节奏：'
      + '入戏前3天假死因红鲱鱼误导→第4-5天核心反转（真死因揭穿）→第6-7天（T-1→T-0）规则利用破局。'
      + '气氛曲线要缓慢攀升、必现标记（白胎会/缝胎客/墨胆珠/湿红线/七格帖/订书钉）分布要均匀。',
    context: {
      project_id: currentProjectId.value,
      volume_no: props.volNo,
      user_directive: rhythmDirective.value || null,
      is_mock: isMock.value,
      blueprint: bp ? {
        title: bp.title || null,
        premise: (bp.premise || '').slice(0, 800),
        day_beats: Array.isArray(bp.day_beats) ? bp.day_beats.map((d: any) => ({
          day: d.day || d.day_no,
          title: d.title,
          mood: d.mood,
          summary: (d.summary || d.events_text || '').slice(0, 400),
          beats: Array.isArray(d.beats) ? d.beats.slice(0, 8).map((b: any) => ({
            label: b.label || b.name, description: (b.description || '').slice(0, 200),
          })) : [],
        })) : [],
        pacing: bp.pacing || null,
        suggested_chapters: bp.suggested_chapters || null,
      } : null,
      density_map: dm ? {
        total_words: dm.total_words,
        chapter_count: dm.chapter_count,
        days: Array.isArray(dm.days) ? dm.days.map((d: any) => ({
          day: d.day || d.day_no, chapters: d.chapters, words: d.words,
          tension: d.tension, density: d.density,
        })) : [],
      } : null,
    },
  }
})

// ---- Blueprint edit dialog ----
const editVisible = ref(false)
const editNotes = ref('')
const editSuggestedChaptersMin = ref<number>(18)
const editSuggestedChaptersMax = ref<number>(24)
const editSuggestedLength = ref<number>(200000)
const editPacingSetup = ref(0.2)
const editPacingBuild = ref(0.4)
const editPacingClimax = ref(0.25)
const editPacingCooldown = ref(0.15)

function openEdit() {
  if (!blueprint.value) return
  const bp = blueprint.value
  editNotes.value = bp.notes || ''
  const range = bp.suggested_chapter_range || [18, 24]
  editSuggestedChaptersMin.value = range[0] ?? 18
  editSuggestedChaptersMax.value = range[1] ?? 24
  editSuggestedLength.value = bp.suggested_length || 200000
  const pr = (bp.pacing_ratio as Record<string, number>) || {}
  editPacingSetup.value = Number(pr.setup) || 0.2
  editPacingBuild.value = Number(pr.build) || 0.4
  editPacingClimax.value = Number(pr.climax) || 0.25
  editPacingCooldown.value = Number(pr.cooldown) || 0.15
  editVisible.value = true
}

function onEditSave() {
  if (!blueprint.value) return
  // Normalize pacing ratio to sum ~1
  const total = editPacingSetup.value + editPacingBuild.value + editPacingClimax.value + editPacingCooldown.value
  const norm = (v: number) => total > 0 ? Math.round((v / total) * 1000) / 1000 : 0.25
  blueprint.value = {
    ...blueprint.value,
    notes: editNotes.value,
    suggested_chapter_range: [editSuggestedChaptersMin.value, editSuggestedChaptersMax.value],
    suggested_length: editSuggestedLength.value,
    pacing_ratio: {
      setup: norm(editPacingSetup.value),
      build: norm(editPacingBuild.value),
      climax: norm(editPacingClimax.value),
      cooldown: norm(editPacingCooldown.value),
    },
  }
  // 密度图/key_beats 无法本地重构，保持不变；展示已生效
  editVisible.value = false
  ElMessage.success('节奏蓝图已本地更新（采纳后将随阶段标记落盘）')
}

const days = [1, 2, 3, 4, 5, 6, 7]

// ---- 数据加载 ----
async function load() {
  const pid = currentProjectId.value
  if (!pid) return
  try {
    const r = await rhythmApi.get(pid, props.volNo)
    blueprint.value = r.blueprint || null
    densityMap.value = r.density_map || null
    volume.value = r.volume || null
  } catch (e: any) {
    if (e?.status !== 404) {
      // ignore other errors silently
    }
  }
}

onMounted(load)
watch(() => props.volNo, load)

// ---- AI 推演 ----
async function onAnalyze() {
  const pid = currentProjectId.value
  if (!pid) {
    ElMessage.error('请先选择项目')
    return
  }
  loading.value = true
  isMock.value = false
  try {
    const r = await rhythmApi.analyze(pid, props.volNo, {
      rhythm_directive: rhythmDirective.value.trim(),
    })
    blueprint.value = r.blueprint
    densityMap.value = r.density_map
    isMock.value = !!r.is_mock
    if (r.is_mock) {
      ElMessage({ message: '已使用示例节奏数据（mock 副本）', type: 'info' })
    } else {
      ElMessage.success('节奏推演完成')
    }
  } catch (e: any) {
    const msg = e?.message || String(e)
    if (msg.includes('422') || msg.includes('锁定')) {
      ElMessage.error('请先完成副本详设 6 卡锁定后再推演节奏')
    } else {
      ElMessage.error(`节奏推演失败：${msg}`)
    }
  } finally {
    loading.value = false
  }
}

// ---- 采纳 ----
async function onFinalize() {
  const pid = currentProjectId.value
  if (!pid || !blueprint.value) return
  finalizing.value = true
  try {
    await rhythmApi.finalize(pid, props.volNo)
    ElMessage.success('节奏方案已采纳，进入章前规划')
    emit('done')
  } catch (e: any) {
    ElMessage.error(`采纳失败：${e?.message || e}`)
  } finally {
    finalizing.value = false
  }
}

// ---- 衍生计算 ----
const maxDensity = computed(() => {
  if (!densityMap.value) return 1
  let m = 1
  for (const d of days) {
    const row = densityMap.value[d]
    if (row) m = Math.max(m, row.total)
  }
  return m
})

const beatByDay = computed<Record<number, RhythmBeat>>(() => {
  const map: Record<number, RhythmBeat> = {}
  if (!blueprint.value) return map
  for (const b of blueprint.value.key_beats_progress || []) {
    map[b.day] = b
  }
  return map
})

function beatHeight(i: number) {
  const b = beatByDay.value[i]
  const v = b ? b.intensity : 0
  return `${(v / 7) * 100}%`
}

function densityH(i: number, key: 'death' | 'climax' | 'clue') {
  if (!densityMap.value) return '0%'
  const row = densityMap.value[i]
  if (!row) return '0%'
  const v = row[key] || 0
  return `${(v / maxDensity.value) * 100}%`
}

function totalAt(i: number) {
  if (!densityMap.value) return 0
  return densityMap.value[i]?.total || 0
}

function densityPct(i: number, key: 'death' | 'climax' | 'clue') {
  if (!densityMap.value) return 0
  const row = densityMap.value[i]
  if (!row || !row.total) return 0
  return Math.round((row[key] / row.total) * 100)
}

function beatTypeTag(t: string): { type: any; label: string } {
  switch (t) {
    case 'hook': return { type: '', label: 'hook 钩子' }
    case 'sell': return { type: 'success', label: 'sell 售卖' }
    case 'engine': return { type: 'info', label: 'engine 推进' }
    case 'climax': return { type: 'danger', label: 'climax 高潮' }
    case 'cooldown': return { type: 'info', label: 'cooldown 冷却' }
    default: return { type: '', label: t }
  }
}

function warningTypeTag(t: string): { type: any; label: string } {
  switch (t) {
    case 'dearth': return { type: 'warning', label: '真空' }
    case 'cluster': return { type: 'danger', label: '过载' }
    case 'front_load': return { type: 'warning', label: '前重' }
    case 'back_load': return { type: 'warning', label: '后重' }
    default: return { type: 'info', label: t }
  }
}

function pacingLabel(k: string): string {
  switch (k) {
    case 'setup': return 'setup 铺垫'
    case 'build': return 'build 升温'
    case 'climax': return 'climax 高潮'
    case 'cooldown': return 'cooldown 收束'
    default: return k
  }
}

const chapterColumns = [
  { prop: 'chapter', label: '章', width: 60 },
  { prop: 'day', label: '日', width: 60 },
  { prop: 'focus', label: '核心焦点' },
  { prop: 'words', label: '字数', width: 90 },
  { prop: 'beat_type', label: '节拍类型', width: 130 },
]
</script>

<template>
  <div class="rhythm-view">
    <!-- 顶部标题 -->
    <div class="rhythm-header">
      <div class="rhythm-title">
        <h2>节奏骨架 · 双轨时间线</h2>
        <p class="sub">按唐迟入戏第1-7天（T-6→T-0）推演整卷节奏，同时标注原剧本事件并行</p>
      </div>
      <div class="rhythm-actions">
        <el-input
          v-model="rhythmDirective"
          class="rhythm-directive"
          placeholder="节奏指令：如「Day3 必须有一场小高潮」「希望 Day5 拉满」"
          clearable
        />
        <el-button
          v-if="blueprint"
          size="default"
          title="编辑蓝图节奏注记 / 章节建议 / 四段占比"
          @click="openEdit"
        >✏️ 编辑蓝图</el-button>
        <el-button
          type="primary"
          :loading="loading"
          :disabled="finalizing"
          @click="onAnalyze"
        >
          {{ loading ? 'AI 正在推演节奏…' : (blueprint ? '重新推演节奏' : 'AI 推演节奏') }}
        </el-button>
      </div>
    </div>

    <!-- 上一步产物：副本详设6卡 -->
    <PrevStepPanel
      step-label="节奏骨架"
      prev-step-label="副本详设（6 卡）"
      :sections="[
        {
          title: '核心诡计',
          body: volume?.scaffold_cards?.core_twist?.text || volume?.scaffold_cards?.core_twist || '',
          bodyType: 'text'
        },
        {
          title: '唐迟第一秒 / 穿入开局',
          body: volume?.scaffold_cards?.tangchi_start?.text || volume?.scaffold_cards?.tangchi_start || '',
          bodyType: 'text'
        },
        {
          title: '死亡节点',
          body: Array.isArray(volume?.death_nodes) ? volume.death_nodes.join('\n') : (volume?.scaffold_cards?.death_nodes?.text || ''),
          bodyType: 'text'
        },
        {
          title: '代价上限',
          body: volume?.scaffold_cards?.cost_cap?.text || volume?.scaffold_cards?.cost_cap || '',
          bodyType: 'text'
        }
      ]"
    />

    <!-- 空态 -->
    <div v-if="!blueprint" class="empty-state">
      <el-empty description="点击右上角「AI 推演节奏」生成蓝图，可在按钮左侧填写节奏指令">
        <template #image>
          <div class="empty-ico">⚰</div>
        </template>
      </el-empty>
    </div>

    <template v-else>
      <!-- mock 提示 -->
      <el-alert
        v-if="isMock"
        title="当前为示例副本数据（mock），节点分布为虚构，仅供 UI 演示"
        type="info"
        :closable="false"
        show-icon
        style="margin-bottom: 16px;"
      />

      <!-- 核心可视化：7 天强度曲线 -->
      <div class="panel">
        <div class="panel-head">
          <h3>七日节奏强度曲线</h3>
          <span class="panel-sub">intensity 1(平缓) → 7(癫狂)</span>
        </div>
        <div class="rhythm-curve">
          <div
            v-for="d in days"
            :key="d"
            class="rhythm-day-col"
          >
            <div class="rhythm-label">
              <div class="rhythm-label-txt">{{ beatByDay[d]?.label || '—' }}</div>
              <div class="rhythm-label-int">强度 {{ beatByDay[d]?.intensity || 0 }}/7</div>
            </div>
            <div class="rhythm-bar-wrap">
              <div class="rhythm-bar" :style="{ height: beatHeight(d) }">
                <div class="rhythm-bar-inner"></div>
                <div class="rhythm-nodes">
                  <span
                    v-for="(_, idx) in Array(densityMap?.[d]?.death || 0).fill(0)"
                    :key="'d'+idx"
                    class="rn rn-death" title="死亡节点"
                  >💀</span>
                  <span
                    v-for="(_, idx) in Array(densityMap?.[d]?.climax || 0).fill(0)"
                    :key="'c'+idx"
                    class="rn rn-climax" title="高潮节点"
                  >★</span>
                  <span
                    v-for="(_, idx) in Array(densityMap?.[d]?.clue || 0).fill(0)"
                    :key="'l'+idx"
                    class="rn rn-clue" title="线索节点"
                  >●</span>
                </div>
              </div>
            </div>
            <div class="rhythm-day-num">D{{ d }}</div>
          </div>
        </div>
      </div>

      <!-- 密度条 + 四段占比 -->
      <div class="two-col">
        <div class="panel panel-grow">
          <div class="panel-head">
            <h3>七日节点密度</h3>
            <span class="panel-sub">
              <span class="legend lg-death"></span>死亡
              <span class="legend lg-climax"></span>高潮
              <span class="legend lg-clue"></span>线索
            </span>
          </div>
          <div class="density-row">
            <div v-for="d in days" :key="d" class="density-col">
              <div class="density-total">{{ totalAt(d) }}</div>
              <div class="density-stack">
                <div class="dseg seg-death" :style="{ height: densityH(d, 'death') }" :title="`死亡 ${densityMap?.[d]?.death||0} (${densityPct(d,'death')}%)`"></div>
                <div class="dseg seg-climax" :style="{ height: densityH(d, 'climax') }" :title="`高潮 ${densityMap?.[d]?.climax||0} (${densityPct(d,'climax')}%)`"></div>
                <div class="dseg seg-clue" :style="{ height: densityH(d, 'clue') }" :title="`线索 ${densityMap?.[d]?.clue||0} (${densityPct(d,'clue')}%)`"></div>
              </div>
              <div class="density-day">D{{ d }}</div>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="panel-head">
            <h3>节奏四段占比</h3>
          </div>
          <div class="pacing-grid">
            <div
              v-for="(v, k) in (blueprint.pacing_ratio || {})"
              :key="String(k)"
              class="pacing-card"
            >
              <div class="pacing-label">{{ pacingLabel(String(k)) }}</div>
              <div class="pacing-value">{{ Math.round((Number(v) || 0) * 100) }}%</div>
              <div class="pacing-bar">
                <div class="pacing-bar-inner" :class="'pk-'+String(k)" :style="{ width: (Number(v)||0)*100 + '%' }"></div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 告警 -->
      <div class="panel">
        <div class="panel-head">
          <h3>节奏告警</h3>
        </div>
        <div v-if="(blueprint.warnings || []).length === 0">
          <el-result icon="success" title="节奏分布均衡" sub-title="七日密度与强度曲线无明显风险点" />
        </div>
        <div v-else class="warn-list">
          <el-alert
            v-for="(w, i) in blueprint.warnings"
            :key="i"
            :type="warningTypeTag(w.type).type as any"
            :closable="false"
            show-icon
            class="warn-item"
          >
            <template #title>
              <el-tag size="small" :type="warningTypeTag(w.type).type as any" effect="dark" style="margin-right: 8px;">
                {{ warningTypeTag(w.type).label }}
              </el-tag>
              <span v-if="w.day">第 {{ w.day }} 天 · </span>{{ w.msg }}
            </template>
          </el-alert>
        </div>
      </div>

      <!-- 章节分配建议 -->
      <div class="panel">
        <div class="panel-head">
          <h3>
            建议章节分配（{{ blueprint.suggested_chapter_range?.[0] }}~{{ blueprint.suggested_chapter_range?.[1] }}章
            / 约 {{ (blueprint.suggested_length || 0).toLocaleString() }} 字）
          </h3>
        </div>
        <el-table
          :data="blueprint.chapter_plan || []"
          stripe
          size="small"
          class="chapter-table"
        >
          <el-table-column v-for="col in chapterColumns" :key="col.prop" :prop="col.prop" :label="col.label" :width="col.width">
            <template #default="{ row }" v-if="col.prop === 'beat_type'">
              <el-tag size="small" :type="beatTypeTag(row.beat_type).type as any">
                {{ beatTypeTag(row.beat_type).label }}
              </el-tag>
            </template>
            <template #default="{ row }" v-else-if="col.prop === 'words'">
              {{ (row.words || 0).toLocaleString() }}
            </template>
          </el-table-column>
        </el-table>
        <div v-if="blueprint.notes" class="notes">
          <div class="notes-label">节奏注记</div>
          <p>{{ blueprint.notes }}</p>
        </div>
      </div>

      <!-- 底部按钮 -->
      <div class="bottom-actions">
        <el-button
          type="primary"
          size="large"
          :loading="finalizing"
          :disabled="!blueprint || loading"
          @click="onFinalize"
        >
          采纳此节奏方案，进入章前规划
        </el-button>
      </div>
    </template>

    <!-- Edit Blueprint Dialog -->
    <el-dialog
      v-model="editVisible"
      title="✏️ 编辑节奏蓝图（本地）"
      width="600px"
      :close-on-click-modal="false"
    >
      <p class="edit-hint">仅本地修改展示用的节奏注记 / 章节建议 / 四段占比；节点分布（曲线/密度/告警/章节表）不重构，需重新推演以更新。</p>
      <div class="edit-row">
        <label>节奏注记</label>
        <el-input v-model="editNotes" type="textarea" :rows="4" placeholder="对节奏方案的补充说明/注意事项" />
      </div>
      <div class="edit-row edit-grid">
        <div>
          <label>建议章数（起）</label>
          <el-input-number v-model="editSuggestedChaptersMin" :min="6" :max="60" />
        </div>
        <div>
          <label>建议章数（止）</label>
          <el-input-number v-model="editSuggestedChaptersMax" :min="6" :max="60" />
        </div>
        <div>
          <label>建议字数</label>
          <el-input-number v-model="editSuggestedLength" :min="20000" :max="800000" :step="10000" />
        </div>
      </div>
      <div class="edit-row">
        <label>四段占比（会自动归一化）</label>
        <div class="edit-grid">
          <div>
            <span class="pk-chip pk-setup">setup</span>
            <el-input-number v-model="editPacingSetup" :min="0" :max="1" :step="0.05" />
          </div>
          <div>
            <span class="pk-chip pk-build">build</span>
            <el-input-number v-model="editPacingBuild" :min="0" :max="1" :step="0.05" />
          </div>
          <div>
            <span class="pk-chip pk-climax">climax</span>
            <el-input-number v-model="editPacingClimax" :min="0" :max="1" :step="0.05" />
          </div>
          <div>
            <span class="pk-chip pk-cooldown">cooldown</span>
            <el-input-number v-model="editPacingCooldown" :min="0" :max="1" :step="0.05" />
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" @click="onEditSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.rhythm-view {
  padding: 20px 28px 40px;
  color: #e6e0d4;
  min-height: 100%;
  max-width: 1080px;
  margin: 0 auto;
}

.rhythm-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
  gap: 16px;
  flex-wrap: wrap;
}
.rhythm-header h2 {
  margin: 0 0 6px;
  font-size: 22px;
  letter-spacing: 1px;
  color: #d4b48a;
  text-shadow: 0 0 10px rgba(212, 180, 138, 0.25);
}
.rhythm-header .sub {
  margin: 0;
  color: #8e8575;
  font-size: 13px;
}
.rhythm-actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.rhythm-directive {
  width: 380px;
}
.rhythm-directive :deep(.el-input__wrapper) {
  background: rgba(20, 17, 14, 0.7);
  box-shadow: 0 0 0 1px #2a241d inset;
}
.rhythm-directive :deep(.el-input__inner) {
  color: #e6e0d4;
}
.rhythm-actions .el-button--primary {
  background: linear-gradient(135deg, #8a2a2a, #c44848) !important;
  border-color: #c44848 !important;
  box-shadow: 0 0 14px rgba(196, 72, 72, 0.25);
}

.empty-state {
  padding: 60px 20px;
  background: rgba(30, 26, 22, 0.5);
  border: 1px dashed #3a332a;
  border-radius: 10px;
}
.empty-ico {
  font-size: 64px;
  color: #8e8575;
  line-height: 1;
}
.empty-state :deep(.el-empty__description p) {
  color: #8e8575;
}

.panel {
  background: rgba(30, 26, 22, 0.6);
  border: 1px solid #3a332a;
  border-radius: 8px;
  padding: 18px 22px;
  margin-bottom: 18px;
}
.panel-grow { flex: 1; }
.panel-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 14px;
}
.panel-head h3 {
  margin: 0;
  font-size: 16px;
  color: #d4b48a;
  letter-spacing: 0.5px;
}
.panel-sub {
  font-size: 12px;
  color: #8e8575;
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

/* ---- 7 天曲线 ---- */
.rhythm-curve {
  display: flex;
  gap: 14px;
  align-items: stretch;
  overflow-x: auto;
  padding: 8px 4px 4px;
}
.rhythm-day-col {
  flex: 1 1 0;
  min-width: 90px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.rhythm-label {
  min-height: 48px;
  text-align: center;
  margin-bottom: 6px;
}
.rhythm-label-txt {
  font-size: 12.5px;
  color: #c9c0ae;
  line-height: 1.3;
}
.rhythm-label-int {
  font-size: 11px;
  color: #c98e8e;
  margin-top: 2px;
}
.rhythm-bar-wrap {
  width: 100%;
  height: 180px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  background: linear-gradient(to top, rgba(40, 30, 30, 0.5), rgba(20, 17, 14, 0.2));
  border-radius: 4px;
  border: 1px solid #2a241d;
  padding: 6px;
  position: relative;
}
.rhythm-bar {
  width: 70%;
  min-height: 4px;
  border-radius: 4px 4px 2px 2px;
  background: linear-gradient(to top, #409EFF, #F56C6C);
  box-shadow:
    0 0 12px rgba(245, 108, 108, 0.4),
    inset 0 0 8px rgba(255, 255, 255, 0.08);
  position: relative;
  transition: height 0.6s cubic-bezier(.22,.9,.3,1);
}
.rhythm-bar-inner {
  position: absolute;
  inset: 0;
  background:
    repeating-linear-gradient(
      to top,
      rgba(255,255,255,0.06) 0 2px,
      transparent 2px 10px
    );
  border-radius: inherit;
}
.rhythm-nodes {
  position: absolute;
  right: -26px;
  top: 4px;
  bottom: 4px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 13px;
  align-items: center;
  justify-content: flex-end;
}
.rn { line-height: 1; }
.rn-death { color: #e56b6b; }
.rn-climax { color: #e6b450; }
.rn-clue { color: #5a8fd6; }
.rhythm-day-num {
  margin-top: 6px;
  font-size: 12px;
  color: #8e8575;
  letter-spacing: 1px;
}

.two-col {
  display: flex;
  gap: 18px;
  align-items: stretch;
  flex-wrap: wrap;
}

.density-row {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  min-height: 160px;
}
.density-col {
  flex: 1 1 0;
  min-width: 60px;
  display: flex;
  flex-direction: column;
  align-items: center;
}
.density-total {
  font-size: 13px;
  color: #c9c0ae;
  margin-bottom: 4px;
}
.density-stack {
  width: 28px;
  height: 130px;
  background: rgba(20, 17, 14, 0.7);
  border: 1px solid #2a241d;
  border-radius: 3px;
  display: flex;
  flex-direction: column-reverse;
  overflow: hidden;
}
.dseg {
  width: 100%;
  transition: height 0.4s ease;
}
.seg-death { background: #c44848; }
.seg-climax { background: #e6a23c; }
.seg-clue { background: #409EFF; }
.density-day {
  margin-top: 6px;
  font-size: 11px;
  color: #8e8575;
}

.legend {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin: 0 4px 0 8px;
  vertical-align: middle;
}
.lg-death { background: #c44848; margin-left: 0; }
.lg-climax { background: #e6a23c; }
.lg-clue { background: #409EFF; }

.pacing-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  min-width: 280px;
}
.pacing-card {
  background: rgba(20, 17, 14, 0.6);
  border: 1px solid #2a241d;
  border-radius: 6px;
  padding: 12px 14px;
}
.pacing-label {
  font-size: 13px;
  color: #8e8575;
}
.pacing-value {
  font-size: 20px;
  color: #d4b48a;
  margin: 4px 0 8px;
  font-weight: 600;
}
.pacing-bar {
  height: 6px;
  background: #1a1510;
  border-radius: 3px;
  overflow: hidden;
}
.pacing-bar-inner { height: 100%; border-radius: 3px; }
.pk-setup { background: #5a8fd6; }
.pk-build { background: #c4894e; }
.pk-climax { background: #c44848; }
.pk-cooldown { background: #6b7280; }

.warn-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.warn-item { border-radius: 6px; }

.chapter-table {
  background: rgba(20, 17, 14, 0.4) !important;
  border-radius: 6px;
  overflow: hidden;
}
.chapter-table :deep(.el-table) {
  background: transparent !important;
  color: #c9c0ae;
  --el-table-bg-color: transparent;
  --el-table-tr-bg-color: transparent;
  --el-table-header-bg-color: rgba(30, 26, 22, 0.8);
  --el-table-row-hover-bg-color: rgba(196, 137, 78, 0.08);
  --el-table-border-color: #2a241d;
  --el-table-header-text-color: #d4b48a;
  --el-table-text-color: #c9c0ae;
}
.chapter-table :deep(th.el-table__cell) {
  background: rgba(30, 26, 22, 0.8) !important;
}
.chapter-table :deep(.el-table__row) {
  background: transparent !important;
}
.chapter-table :deep(tr.el-table__row.el-table__row--striped) {
  background: rgba(30, 26, 22, 0.35) !important;
}

.notes {
  margin-top: 14px;
  padding: 12px 16px;
  background: rgba(20, 17, 14, 0.5);
  border-left: 3px solid #c4894e;
  border-radius: 4px;
}
.notes-label {
  font-size: 13px;
  color: #d4b48a;
  margin-bottom: 6px;
}
.notes p {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: #c9c0ae;
}

.bottom-actions {
  margin-top: 20px;
  display: flex;
  justify-content: center;
}
.bottom-actions .el-button--primary {
  background: linear-gradient(135deg, #8a2a2a, #c44848) !important;
  border-color: #c44848 !important;
  padding: 12px 30px;
  font-size: 15px;
  box-shadow: 0 0 18px rgba(196, 72, 72, 0.3);
}

.edit-hint {
  margin: 0 0 14px;
  padding: 10px 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
}
.edit-row {
  margin-bottom: 14px;
}
.edit-row label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  color: #606266;
  font-weight: 500;
}
.edit-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
}
.edit-grid > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.pk-chip {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  color: #fff;
  margin-bottom: 4px;
}
.pk-chip.pk-setup { background: #5a8fd6; }
.pk-chip.pk-build { background: #c4894e; }
.pk-chip.pk-climax { background: #c44848; }
.pk-chip.pk-cooldown { background: #6b7280; }

@media (max-width: 768px) {
  .rhythm-header { flex-direction: column; gap: 12px; }
  .rhythm-directive { width: 100%; }
  .two-col { flex-direction: column; }
  .pacing-grid { min-width: 0; }
}
</style>
