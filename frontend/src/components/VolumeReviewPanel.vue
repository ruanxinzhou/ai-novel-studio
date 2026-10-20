<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { currentProjectId } from '../state'
import { adaptApi, deskApi, volumesApi } from '../api'

const props = defineProps<{
  volNo: number
  phase: string // *_review
}>()

const volume = ref<any>(null)
const chapters = ref<any[]>([])
const loading = ref(true)
const loadError = ref<string>('')

const stageMeta: Record<string, { label: string; icon: string }> = {
  adapt: { label: '剧本改编', icon: '📖' },
  adapt_empty: { label: '新卷待改', icon: '📕' },
  simulate: { label: '模拟跑团', icon: '🎲' },
  embody: { label: '魂穿设计', icon: '👁' },
  scaffold: { label: '卷内详设', icon: '🏗' },
  rhythm: { label: '节奏骨架', icon: '📈' },
  settle: { label: '杀青归账', icon: '⚖️' },
  interlude: { label: '蓝星间章', icon: '🌌' },
  desk: { label: '日更写作', icon: '✍️' },
  briefing: { label: '章前规划', icon: '📝' },
}

const baseStage = computed(() => (props.phase || '').replace('_review', ''))
const stageInfo = computed(() => stageMeta[baseStage.value] || { label: baseStage.value || '进度', icon: '📘' })

// adapted_module 兼容 dict / 字符串两种格式
const adaptedText = computed(() => {
  const am = (volume.value as any)?.adapted_module
  if (!am) return ''
  if (typeof am === 'string') return am
  if (typeof am === 'object') {
    const premise = am.adapted_premise || ''
    const cast: any[] = Array.isArray(am.cast) ? am.cast
      : (Array.isArray(am.player_cards) ? am.player_cards : [])
    const flavor = am.flavor_full || am.flavor || ''
    const lines: string[] = []
    if (flavor) lines.push(`【风格】${flavor}`)
    if (premise) lines.push(premise)
    if (cast.length) {
      lines.push('\n【剧本 NPC 卡司】')
      for (const c of cast) {
        if (!c || typeof c !== 'object') continue
        const n = c.name || '?'
        const r = c.role ? `（${c.role}` : ''
        const o = c.occupation ? `${r ? '；' : '（'}${c.occupation}）` : (r ? '）' : '')
        lines.push(`- ${n}${r}${o}`)
      }
    }
    return lines.join('\n')
  }
  return String(am)
})

async function load() {
  const pid = currentProjectId.value
  const vn = props.volNo
  if (!pid || !vn) {
    volume.value = null
    chapters.value = []
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  volume.value = null
  chapters.value = []
  try {
    const [volRes, deskRes] = await Promise.all([
      adaptApi.getVolume(pid, vn).catch((e: any) => {
        loadError.value = e?.message || '读取卷信息失败'
        return null
      }),
      deskApi.detail(pid).catch(() => ({ chapters: [] })),
    ])
    const vol = volRes?.volume || null
    volume.value = vol
    if (!vol) {
      chapters.value = []
      return
    }
    // 过滤该卷的章：主线/间章按 chapter_kind，dungeon 按 field_no
    const all: any[] = (deskRes as any)?.chapters || []
    let matched: any[] = []
    if (vol.volume_kind === 'mainline') {
      matched = all.filter((c: any) => c.chapter_kind === 'mainline')
    } else if (vol.volume_kind === 'interlude') {
      matched = all.filter((c: any) => c.is_interlude || c.chapter_kind === 'interlude')
    } else {
      matched = all.filter((c: any) => c.field_no === vn || c.field_no === vol.field_no)
    }
    if (matched.length > 0) {
      chapters.value = matched.slice().sort((a: any, b: any) => a.no - b.no)
    } else {
      // 老数据兼容：chapter 没有 field_no 时，按卷顺序近似分组
      try {
        const volsResp = await volumesApi.list(pid).catch(() => ({ volumes: [], total: 0 }))
        const vols: any[] = Array.isArray((volsResp as any)?.volumes) ? (volsResp as any).volumes : []
        const sortedVols = vols.slice().sort((a: any, b: any) => a.volume_no - b.volume_no)
        const chsAll = all.slice().sort((a: any, b: any) => a.no - b.no)
        const idx = sortedVols.findIndex((v: any) => v.volume_no === vn)
        if (idx >= 0 && sortedVols.length > 0 && chsAll.length > 0) {
          const per = Math.max(1, Math.ceil(chsAll.length / sortedVols.length))
          chapters.value = chsAll.slice(idx * per, (idx + 1) * per)
        } else {
          chapters.value = []
        }
      } catch {
        chapters.value = []
      }
    }
  } catch (e: any) {
    loadError.value = e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(
  () => [props.volNo, props.phase],
  () => {
    load()
  },
)

function stageCompleted(key: string) {
  return !!(volume.value?.stage_completed && volume.value.stage_completed[key])
}

function deltaSign(delta: any): string {
  const n = Number(delta)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? '+' : ''
}

function wordClass(chars: number | null | undefined): string {
  const n = Number(chars) || 0
  if (!n) return 'vr-wc-none'
  if (n < 3500) return 'vr-wc-short'
  if (n > 5500) return 'vr-wc-long'
  if (n >= 4000 && n <= 5000) return 'vr-wc-ok'
  return 'vr-wc-near'
}

function wordTip(chars: number | null | undefined): string {
  const n = Number(chars) || 0
  if (!n) return ''
  if (n < 3500) return '过短（<3500）'
  if (n > 5500) return '过长（>5500）'
  if (n >= 4000 && n <= 5000) return '达标（4000-5000）'
  if (n < 4000) return '略短（3500-3999）'
  return '略长（5001-5500）'
}
</script>

<template>
  <div v-loading="loading" class="vol-review">
    <!-- 错误态 -->
    <div v-if="loadError && !volume" class="vr-error">
      <el-alert type="error" :closable="false" :title="loadError" show-icon />
      <div style="text-align:center; margin-top:16px">
        <el-button @click="load">重试</el-button>
      </div>
    </div>

    <!-- 加载中占位（避免渲染 null 崩溃） -->
    <div v-else-if="!volume" class="vr-empty">
      <el-icon class="is-loading" />
      <span>加载卷信息中…</span>
    </div>

    <!-- 数据态 -->
    <template v-else>
      <div class="vr-head">
        <div class="vr-icon">{{ volume.volume_kind === 'interlude' ? '🌌' : '📕' }}</div>
        <div class="vr-title-wrap">
          <h2>第{{ volume.volume_no }}卷 {{ volume.title || (volume.volume_kind === 'interlude' ? '蓝星间章' : '副本') }}</h2>
          <div class="vr-meta">
            <el-tag size="small" effect="plain">{{ stageInfo.icon }} {{ stageInfo.label }}</el-tag>
            <el-tag v-if="volume.settle?.contamination_after != null" size="small" type="info" effect="plain">
              污染 {{ volume.settle.contamination_after }}
            </el-tag>
            <el-tag v-else-if="volume.protagonist_snapshot?.contamination_level != null" size="small" type="success" effect="plain">
              起始污染 {{ volume.protagonist_snapshot.contamination_level }}
            </el-tag>
          </div>
        </div>
      </div>

      <el-card v-if="adaptedText" class="vr-card" shadow="never">
        <template #header><span>📖 核心文本</span></template>
        <div class="vr-text" style="white-space:pre-wrap">{{ adaptedText }}</div>
      </el-card>

      <el-card class="vr-card" shadow="never">
        <template #header><span>🪜 阶段完成度</span></template>
        <el-steps
          :active="(volume.stage_completed ? Object.values(volume.stage_completed).filter(Boolean).length : 0)"
          finish-status="success"
          simple
        >
          <el-step title="改编" />
          <el-step title="跑团" />
          <el-step title="魂穿" />
          <el-step title="详设" />
          <el-step title="节奏" />
        </el-steps>
      </el-card>

      <el-card v-if="volume.rhythm_blueprint" class="vr-card" shadow="never">
        <template #header><span>📈 节奏方案</span></template>
        <div v-if="volume.rhythm_blueprint.suggested_chapter_range" style="margin-bottom:8px; font-size:13px; color:#909399">
          建议章节：{{ volume.rhythm_blueprint.suggested_chapter_range?.[0] }}-{{ volume.rhythm_blueprint.suggested_chapter_range?.[1] }} 章
          · 预计字数 {{ volume.rhythm_blueprint.suggested_length }}
        </div>
        <div
          v-if="volume.rhythm_blueprint.pacing_ratio"
          style="display:flex; gap:12px; font-size:12px; color:#606266; margin-bottom:8px; flex-wrap:wrap"
        >
          <span v-for="(v,k) in volume.rhythm_blueprint.pacing_ratio" :key="k">{{ k }} {{ Math.round(v*100) }}%</span>
        </div>
        <div v-if="volume.rhythm_blueprint.notes" class="vr-text">{{ volume.rhythm_blueprint.notes }}</div>
      </el-card>

      <el-card v-if="chapters.length" class="vr-card" shadow="never">
        <template #header><span>✍️ 章节列表（{{ chapters.length }} 章）</span></template>
        <el-table :data="chapters" size="small" stripe>
          <el-table-column prop="no" label="#" width="56" />
          <el-table-column prop="title" label="标题" />
          <el-table-column label="状态" width="100">
            <template #default="{row}">
              <el-tag v-if="row.finalized_at" size="small" type="success" effect="plain">已完结</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">草稿</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="字数" width="100">
            <template #default="{row}">
              <span
                class="vr-wc-tag"
                :class="wordClass(row.word_count || (row.draft ? row.draft.length : 0))"
                :title="wordTip(row.word_count || (row.draft ? row.draft.length : 0))"
              >{{ row.word_count || (row.draft ? row.draft.length : 0) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="volume.settle" class="vr-card" shadow="never">
        <template #header><span>⚖️ 杀青归账</span></template>
        <div v-if="volume.settle.cost_summary" class="vr-text" style="margin-bottom:8px">{{ volume.settle.cost_summary }}</div>
        <el-tag v-if="volume.settle.contamination_after != null" size="small" type="danger" effect="plain">
          污染度 {{ deltaSign(volume.settle.contamination_delta) }}{{ volume.settle.contamination_delta ?? 0 }} → {{ volume.settle.contamination_after }}
        </el-tag>
        <div v-if="volume.settle.blue_star_hook" style="margin-top:8px">
          <div style="font-size:12px; color:#909399; margin-bottom:4px">蓝星钩子</div>
          <div class="vr-text">{{ volume.settle.blue_star_hook.title }}：{{ volume.settle.blue_star_hook.content }}</div>
        </div>
      </el-card>

      <el-card v-if="volume.interlude_plan && volume.interlude_plan.finalized_at" class="vr-card" shadow="never">
        <template #header><span>🌌 间章计划</span></template>
        <div class="vr-kv"><b>违和点：</b><span>{{ volume.interlude_plan.weihedian?.content }}</span></div>
        <div class="vr-kv"><b>后遗症：</b><span>{{ volume.interlude_plan.houchiyizheng?.content }}</span></div>
        <div class="vr-kv"><b>下卷钩子：</b><span>{{ volume.interlude_plan.xiajuangouzi?.content }}</span></div>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.vol-review {
  max-width: 880px;
  margin: 0 auto;
  padding: 24px;
}
.vr-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 200px;
  color: #909399;
  font-size: 14px;
}
.vr-error {
  max-width: 520px;
  margin: 40px auto 0;
}
.vr-head {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}
.vr-icon { font-size: 42px; }
.vr-title-wrap h2 {
  margin: 0 0 6px;
  font-size: 22px;
  color: #303133;
}
.vr-meta { display: flex; gap: 8px; flex-wrap: wrap; }
.vr-card { margin-bottom: 14px; border-radius: 8px; }
.vr-text {
  font-size: 13px;
  color: #606266;
  line-height: 1.65;
  white-space: pre-wrap;
  background: #fafafa;
  padding: 10px 12px;
  border-radius: 4px;
}
.vr-kv {
  font-size: 13px;
  color: #606266;
  line-height: 1.7;
}
.vr-kv b { color: #303133; margin-right: 4px; }
.vr-wc-tag {
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
}
.vr-wc-none { color: #c0c4cc; }
.vr-wc-short { color: #b88230; background: #fdf6ec; }   /* 过短 <3500 黄 */
.vr-wc-near  { color: #b88230; }                      /* 临界 3500-3999 / 5001-5500 */
.vr-wc-ok    { color: #67c23a; background: #f0f9eb; } /* 达标 4000-5000 绿 */
.vr-wc-long  { color: #ff8c00; background: #fff3e6; } /* 过长 >5500 橙 */
</style>
