<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { currentProjectId } from '../state'
import { adaptApi, deskApi } from '../api'

const props = defineProps<{
  volNo: number
  phase: string // *_review
}>()

const volume = ref<any>(null)
const chapters = ref<any[]>([])
const loading = ref(true)

const stageMeta: Record<string, { label: string; icon: string }> = {
  adapt: { label: '剧本改编', icon: '📖' },
  simulate: { label: '模拟跑团', icon: '🎲' },
  embody: { label: '魂穿设计', icon: '👁' },
  scaffold: { label: '卷内详设', icon: '🏗' },
  rhythm: { label: '节奏骨架', icon: '📈' },
  settle: { label: '杀青归账', icon: '⚖️' },
  interlude: { label: '蓝星间章', icon: '🌌' },
  desk: { label: '日更写作', icon: '✍️' },
}

const baseStage = computed(() => props.phase.replace('_review', ''))
const stageInfo = computed(() => stageMeta[baseStage.value] || { label: baseStage.value, icon: '📘' })

async function load() {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  try {
    const [volRes, deskRes] = await Promise.all([
      adaptApi.getVolume(pid, props.volNo).catch(() => null),
      deskApi.detail(pid).catch(() => ({ chapters: [] })),
    ])
    volume.value = volRes?.volume || null
    // 过滤该卷的章
    const all = (deskRes as any)?.chapters || []
    const target = all.filter((c: any) => c.field_no === props.volNo || (volume.value && c.field_no === volume.value.field_no))
    chapters.value = target.sort((a: any, b: any) => a.no - b.no)
  } finally {
    loading.value = false
  }
}

onMounted(load)

function stageCompleted(key: string) {
  return !!(volume.value?.stage_completed && volume.value.stage_completed[key])
}
</script>

<template>
  <div v-loading="loading" class="vol-review">
    <div v-if="volume" class="vr-head">
      <div class="vr-icon">{{ volume.volume_kind === 'interlude' ? '🌌' : '📕' }}</div>
      <div class="vr-title-wrap">
        <h2>第{{ volume.volume_no }}卷 {{ volume.title || (volume.volume_kind === 'interlude' ? '蓝星间章' : '副本') }}</h2>
        <div class="vr-meta">
          <el-tag size="small" effect="plain">{{ stageInfo.icon }} {{ stageInfo.label }}</el-tag>
          <el-tag size="small" type="info" effect="plain" v-if="volume.contamination_after != null">污染 {{ volume.contamination_after }}</el-tag>
          <el-tag size="small" type="success" effect="plain" v-else-if="volume.protagonist_snapshot?.contamination_level != null">起始污染 {{ volume.protagonist_snapshot.contamination_level }}</el-tag>
        </div>
      </div>
    </div>

    <el-card v-if="volume.adapted_module" class="vr-card" shadow="never">
      <template #header><span>📖 核心文本</span></template>
      <div class="vr-text">{{ volume.adapted_module }}</div>
    </el-card>

    <el-card class="vr-card" shadow="never">
      <template #header><span>🪜 阶段完成度</span></template>
      <el-steps :active="(volume.stage_completed ? Object.values(volume.stage_completed).filter(Boolean).length : 0)" finish-status="success" simple>
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
      <div v-if="volume.rhythm_blueprint.pacing_ratio" style="display:flex; gap:12px; font-size:12px; color:#606266; margin-bottom:8px">
        <span v-for="(v,k) in volume.rhythm_blueprint.pacing_ratio" :key="k">{{k}} {{Math.round(v*100)}}%</span>
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
          <template #default="{row}">{{ row.word_count || (row.draft ? row.draft.length : 0) }}</template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="volume.settle" class="vr-card" shadow="never">
      <template #header><span>⚖️ 杀青归账</span></template>
      <div v-if="volume.settle.cost_summary" class="vr-text" style="margin-bottom:8px">{{ volume.settle.cost_summary }}</div>
      <el-tag size="small" type="danger" effect="plain" v-if="volume.settle.contamination_after != null">
        污染度 {{ volume.settle.contamination_delta ?? 0 > 0 ? '+' : '' }}{{ volume.settle.contamination_delta }} → {{ volume.settle.contamination_after }}
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
  </div>
</template>

<style scoped>
.vol-review {
  max-width: 880px;
  margin: 0 auto;
  padding: 24px;
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
.vr-meta { display: flex; gap: 8px; }
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
</style>
