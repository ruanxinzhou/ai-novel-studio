<script setup lang="ts">
/**
 * 角色声音档案编辑器。
 *
 * 角色的习惯动作 / 口癖 / 口音 / 语气存在实体库（voiceprint），随角色走，
 * 不写死在文风卡或 prompt 里。写正文时按本章在场角色注入。
 *
 * 最低配置：习惯动作 ≥3、口癖 ≥1。低于这个数该角色写不活（有 coverage 提示）。
 */
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  voiceprintsApi,
  type Voiceprint,
  type VoiceprintDetail,
  type VoiceprintRow,
  type VoiceprintInput,
} from '../api'

const props = defineProps<{
  pid: string
  /** 预选角色（从实体库点进来时带上） */
  presetId?: string
}>()
const emit = defineEmits<{ (e: 'close'): void }>()

const LIST_KEYS = ['habits', 'catchphrases', 'dialect', 'taboos'] as const
type ListKey = (typeof LIST_KEYS)[number]
const TEXT_KEYS = ['register', 'psychology', 'silence', 'speech_pattern'] as const
type TextKey = (typeof TEXT_KEYS)[number]

const FIELD_LABELS: Record<keyof Voiceprint, string> = {
  habits: '习惯动作',
  catchphrases: '口癖 / 口头禅',
  dialect: '口音',
  register: '语气基调',
  psychology: '心理基调',
  silence: '沉默习惯',
  speech_pattern: '说话方式',
  taboos: '语言禁忌',
}

const PLACEHOLDERS: Record<keyof Voiceprint, string> = {
  habits: '每行一个。写具体可执行的小动作，与身份/经历相关。\n例：摸口袋找烟（烟盒经常是空的）\n手指弹裤缝\n下意识看后视镜',
  catchphrases: '每行一个。角色会脱口而出的短语或单词句。\n例：热的。\n不烫。\n行。',
  dialect: '每行一个。口音、语序、方言词、特殊称呼。\n例：把「知道」说成「晓得的」\n句尾常带「嘞」',
  register: '一句话。整体说话气质。\n例：懒散、短句多、懒得解释',
  psychology: '一句话。他怎么想事情，决定他说什么。\n例：习惯把感受换算成成本和数字',
  silence: '一句话。他不说话的时候在干什么。\n例：沉默三秒就摸口袋，等对方先开口',
  speech_pattern: '一句话。句式习惯。\n例：爱用省略主语的短句，不反问，只陈述',
  taboos: '每行一个。他绝不会说的话/绝不会用的词。\n例：不说「我爱你」\n不用书面语',
}

const rows = ref<VoiceprintRow[]>([])
const loading = ref(false)
const saving = ref(false)
const selectedId = ref<string>('')
const detail = ref<VoiceprintDetail | null>(null)
// 表单里列表字段用「文本域」承载，回车/换行分条，避免作者为每个动作点一次 +
const form = ref<Record<keyof Voiceprint, string>>({
  habits: '', catchphrases: '', dialect: '', register: '',
  psychology: '', silence: '', speech_pattern: '', taboos: '',
})

const unconfigured = computed(() => rows.value.filter((r) => r.coverage !== '完整').length)

function toText(list: string[] | undefined): string {
  return (list ?? []).join('\n')
}

function parseList(raw: string): string[] {
  return raw
    .split('\n')
    .map((x) => x.trim())
    .filter(Boolean)
}

async function loadRows() {
  if (!props.pid) return
  loading.value = true
  try {
    const r = await voiceprintsApi.list(props.pid)
    rows.value = r.items ?? []
    if (!selectedId.value || !rows.value.some((x) => x.id === selectedId.value)) {
      selectedId.value = props.presetId ?? rows.value[0]?.id ?? ''
    }
    if (selectedId.value) await loadDetail(selectedId.value)
  } catch (e: any) {
    ElMessage.error(`加载角色声音档案失败: ${e?.message ?? e}`)
  } finally {
    loading.value = false
  }
}

async function loadDetail(eid: string) {
  if (!props.pid || !eid) {
    detail.value = null
    return
  }
  try {
    const d = await voiceprintsApi.get(props.pid, eid)
    detail.value = d
    const vp = d.voiceprint
    form.value = {
      habits: toText(vp.habits),
      catchphrases: toText(vp.catchphrases),
      dialect: toText(vp.dialect),
      register: vp.register ?? '',
      psychology: vp.psychology ?? '',
      silence: vp.silence ?? '',
      speech_pattern: vp.speech_pattern ?? '',
      taboos: toText(vp.taboos),
    }
  } catch (e: any) {
    ElMessage.error(`读取档案失败: ${e?.message ?? e}`)
  }
}

async function save() {
  if (!props.pid || !selectedId.value) return
  saving.value = true
  try {
    const body: VoiceprintInput = {}
    for (const k of LIST_KEYS) body[k] = parseList(form.value[k])
    for (const k of TEXT_KEYS) body[k] = form.value[k].trim()
    const r = await voiceprintsApi.save(props.pid, selectedId.value, body)
    ElMessage.success(`已保存（${r.coverage}）`)
    await loadRows()
  } catch (e: any) {
    ElMessage.error(`保存失败: ${e?.message ?? e}`)
  } finally {
    saving.value = false
  }
}

const habitCount = computed(() => parseList(form.value.habits).length)
const catchCount = computed(() => parseList(form.value.catchphrases).length)

watch(() => props.pid, loadRows, { immediate: true })
watch(selectedId, (v) => v && loadDetail(v))
</script>

<template>
  <div style="display: flex; gap: 12px; height: 100%; min-height: 420px">
    <!-- 角色列表 -->
    <div style="width: 190px; display: flex; flex-direction: column; border: 1px solid #ebeef5; border-radius: 4px; overflow: hidden">
      <div style="padding: 6px 10px; font-size: 12px; color: #909399; background: #fafafa; border-bottom: 1px solid #ebeef5">
        角色 · {{ rows.length }}
        <span v-if="unconfigured" style="color: #e6a23c">（{{ unconfigured }} 待补）</span>
      </div>
      <div style="flex: 1; overflow: auto">
        <div
          v-for="r in rows"
          :key="r.id"
          @click="selectedId = r.id"
          :style="{
            padding: '8px 10px',
            cursor: 'pointer',
            borderBottom: '1px solid #f5f7fa',
            background: selectedId === r.id ? '#ecf5ff' : '#fff',
          }"
        >
          <div style="display: flex; gap: 4px; align-items: center">
            <b style="font-size: 13px">{{ r.name }}</b>
            <el-tag v-if="r.is_protagonist" size="small" type="danger" effect="plain">主角</el-tag>
          </div>
          <div style="font-size: 11px; margin-top: 2px" :style="{ color: r.coverage === '完整' ? '#67c23a' : '#e6a23c' }">
            {{ r.coverage }}
          </div>
        </div>
        <el-empty v-if="!loading && rows.length === 0" description="暂无角色" :image-size="60" />
      </div>
    </div>

    <!-- 档案表单 -->
    <div style="flex: 1; border: 1px solid #ebeef5; border-radius: 4px; padding: 12px; overflow: auto">
      <el-empty v-if="!detail" description="选择左侧角色编辑其声音档案" :image-size="70" />
      <template v-else>
        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px">
          <h3 style="margin: 0">{{ detail.name }}</h3>
          <el-tag v-if="detail.is_protagonist" size="small" type="danger">主角</el-tag>
          <el-tag size="small" :type="detail.coverage === '完整' ? 'success' : 'warning'">
            {{ detail.coverage }}
          </el-tag>
        </div>
        <div style="font-size: 12px; color: #909399; line-height: 1.6; margin-bottom: 12px">
          这里是这个角色的辨识度来源。写正文时按本章在场角色自动注入，优先级高于文风卡和通用去 AI 味规则。
          最低配置：习惯动作 ≥ {{ detail.min_habits }} 个、口癖 ≥ {{ detail.min_catchphrases }} 个。
        </div>

        <el-form label-position="top" size="small">
          <el-form-item v-for="k in (Object.keys(FIELD_LABELS) as (keyof Voiceprint)[])" :key="k">
            <template #label>
              <span style="font-weight: 600">{{ FIELD_LABELS[k] }}</span>
              <span
                v-if="k === 'habits'"
                :style="{ color: habitCount >= detail!.min_habits ? '#67c23a' : '#e6a23c', marginLeft: '6px' }"
              >{{ habitCount }}/{{ detail!.min_habits }}</span>
              <span
                v-else-if="k === 'catchphrases'"
                :style="{ color: catchCount >= detail!.min_catchphrases ? '#67c23a' : '#e6a23c', marginLeft: '6px' }"
              >{{ catchCount }}/{{ detail!.min_catchphrases }}</span>
            </template>
            <el-input
              v-model="form[k]"
              type="textarea"
              :rows="LIST_KEYS.includes(k as ListKey) ? 3 : 2"
              :placeholder="PLACEHOLDERS[k]"
            />
          </el-form-item>

          <div style="display: flex; gap: 8px">
            <el-button type="primary" :loading="saving" @click="save">保存档案</el-button>
            <el-button @click="emit('close')">完成</el-button>
          </div>
        </el-form>
      </template>
    </div>
  </div>
</template>