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
  habits: '动作倾向',
  catchphrases: '口头习惯',
  dialect: '用语习惯',
  register: '语气基调',
  psychology: '心理基调',
  silence: '沉默习惯',
  speech_pattern: '说话方式',
  taboos: '语言禁忌',
}

const PLACEHOLDERS: Record<keyof Voiceprint, string> = {
  habits: '每行一条「倾向」，不是具体动作。写手要按这个方向自己发明每次不同的动作。\n例：手部常有下意识的小动作\n手边总要摆着点什么可把玩的东西\n习惯把身边的事按时间或数量记下来',
  catchphrases: '每行一条「口头习惯」。要么写类别，要么只留一个极有辨识度的词。\n例：爱用一两个字的判断词收尾\n紧张时会甩一句整活的抽象语录',
  dialect: '每行一条用语习惯：口音、语序、方言词、特殊称呼、词从哪来。\n例：把「知道」说成「晓得的」\n句尾常带「嘞」\n地球的词偶尔滑出来，顿半拍，自己换算成本地听得懂的说法',
  register: '一句话。整体说话气质。\n例：懒散、短句多、懒得解释',
  psychology: '一句话。他怎么想事情，决定他说什么。\n例：习惯把感受换算成成本和数字',
  silence: '一句话。他不说话的时候在干什么。\n例：沉默三秒就摸口袋，等对方先开口',
  speech_pattern: '一句话。句式习惯。\n例：爱用省略主语的短句，不反问，只陈述\n喜欢抬杠，被怼了也要接一句',
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

// ---- AI 生成草稿（预览确认制，不直接覆盖作者内容）----
const generating = ref(false)
const showPreview = ref(false)
/** 草稿内容（可编辑后采用） */
const draftForm = ref<Record<keyof Voiceprint, string>>({ ...form.value })
/** 哪些字段将被采用。作者写过的字段默认不勾，避免一次生成冲掉精心调过的语气。 */
const adopted = ref<Record<keyof Voiceprint, boolean>>({} as Record<keyof Voiceprint, boolean>)

/** 草稿是**为哪个角色**生成的。
 *
 * ## 为什么必须记住这个
 *
 * 之前「采用草稿」保存的是 `selectedId.value`——也就是**保存那一刻**
 * 左侧列表里选中的角色。但草稿是**生成那一刻**那个角色的。
 *
 * 这两个可以不一致：预览弹窗是 `append-to-body` 的独立浮层，作者
 * 在弹窗打开期间仍能点左侧角色列表切换；一切换，`selectedId` 就变了，
 * 于是「陆骁的草稿」被写进了主角的档案——主角的声音档案被整个覆盖，
 * 而陆骁的还是空的。
 *
 * 所以草稿必须绑定生成时的角色 id，采用时写回**那个**角色，
 * 与作者在弹窗期间点没点列表无关。
 */
const draftOwnerId = ref<string>('')

/** 该字段现有档案是否非空（非空=作者写过，默认不采用草稿） */
function hasAuthorContent(k: keyof Voiceprint): boolean {
  return (form.value[k] || '').trim().length > 0
}

/** 切换选中的角色。草稿预览打开时拒绝切换——
 *  草稿是为上一个角色生成的，切了会让「谁的草稿」和「存给谁」对不上。 */
function selectRole(id: string) {
  if (showPreview.value) {
    ElMessage.info('草稿预览中，先采用或关闭预览再切换角色')
    return
  }
  selectedId.value = id
}

async function generateDraft() {
  if (!props.pid || !selectedId.value) return
  generating.value = true
  try {
    const r = await voiceprintsApi.draft(props.pid, selectedId.value)
    if (!r.ok || !r.draft) {
      ElMessage.error(r.error || '生成失败，可手动填写')
      return
    }
    const d = r.draft
    // 草稿归属锁定为生成时的角色
    draftOwnerId.value = selectedId.value
    draftForm.value = {
      habits: toText(d.habits), catchphrases: toText(d.catchphrases),
      dialect: toText(d.dialect), register: d.register ?? '',
      psychology: d.psychology ?? '', silence: d.silence ?? '',
      speech_pattern: d.speech_pattern ?? '', taboos: toText(d.taboos),
    }
    // 默认勾选：草稿有内容 且（现有档案为空 或 该字段确实有变化）
    const next = {} as Record<keyof Voiceprint, boolean>
    for (const k of Object.keys(FIELD_LABELS) as (keyof Voiceprint)[]) {
      const hasContent = (draftForm.value[k] || '').trim().length > 0
      next[k] = hasContent && (!hasAuthorContent(k) || r.changed.includes(k))
    }
    adopted.value = next
    showPreview.value = true
  } catch (e: any) {
    ElMessage.error(`生成失败：${e?.message ?? e}`)
  } finally {
    generating.value = false
  }
}

async function applyDraft() {
  if (!props.pid) return
  // 写回**草稿的归属角色**，不是当前选中的角色
  const owner = draftOwnerId.value || selectedId.value
  if (!owner) return
  const picked = (Object.keys(FIELD_LABELS) as (keyof Voiceprint)[]).filter((k) => adopted.value[k])
  if (!picked.length) {
    ElMessage.warning('至少勾一个字段再采用')
    return
  }
  saving.value = true
  try {
    // 只提交勾选的字段：后端 set_voiceprint 对未提交字段保持原值，
    // 所以作者没勾的字段绝不会被草稿覆盖。
    const body: VoiceprintInput = {}
    for (const k of picked) {
      if (LIST_KEYS.includes(k as ListKey)) body[k] = parseList(draftForm.value[k])
      else body[k] = draftForm.value[k].trim()
    }
    const r = await voiceprintsApi.save(props.pid, owner, body)
    ElMessage.success(`已采用 ${picked.length} 个字段（${r.coverage}）`)
    showPreview.value = false
    // 切回草稿归属角色，让作者看到它落到位了
    if (selectedId.value !== owner) {
      selectedId.value = owner
    } else {
      await loadDetail(owner)
    }
    await loadRows()
  } catch (e: any) {
    ElMessage.error(`采用失败：${e?.message ?? e}`)
  } finally {
    saving.value = false
  }
}

watch(() => props.pid, loadRows, { immediate: true })
watch(selectedId, (v) => {
  // 草稿预览期间不刷新表单：草稿是为上一个角色生成的，
  // 换了角色会把「这是谁的档案」搞混。
  if (!v) return
  if (showPreview.value) return
  loadDetail(v)
})
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
          @click="selectRole(r.id)"
          :style="{
            padding: '8px 10px',
            // 草稿预览打开时禁掉切换：弹窗是 append-to-body 的独立浮层，
            // 作者点得到底下的列表。切了就分不清「这是谁的草稿」，
            // 也容易把 A 的草稿看成 B 的。
            cursor: showPreview ? 'not-allowed' : 'pointer',
            opacity: showPreview ? 0.5 : 1,
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
          最低配置：动作倾向 ≥ {{ detail.min_habits }} 条、口头习惯 ≥ {{ detail.min_catchphrases }} 条。
          <br />
          写的是<b>倾向</b>（方向），不是具体动作——写手会按倾向自己发明每次不同的动作，
          写死了具体动作会原样进正文，读起来像硬要说一下。
        </div>

        <el-alert
          v-if="detail.channel_balance?.narrow"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 12px"
        >
          <template #title>动作倾向全挤在一个通道，正文会写成复读</template>
          <div style="font-size: 12px; line-height: 1.7">
            现在只有「{{ detail.channel_balance.dominant || '单一通道' }}」一条通道。
            正文层要「两句对白之间插一个小动作」，写手没别的选择，只能反复用同一类身体描写。
            <div style="margin-top: 4px; color: #a8823c">
              建议补到 4-6 条、分布在不同通道，例如：视线（会先扫环境）/ 呼吸（紧张时压着气）/
              姿势（重心偏在脚后跟）/ 声线（压低半度）/ 脚步（比看上去轻）。
            </div>
          </div>
        </el-alert>

        <el-alert
          v-if="detail.over_specified?.length"
          type="warning"
          :closable="false"
          show-icon
          style="margin-bottom: 12px"
        >
          <template #title>这几条写得过死，正文里会原样复现</template>
          <div style="font-size: 12px; line-height: 1.7">
            <div v-for="(h, i) in detail.over_specified" :key="i">· {{ h }}</div>
            <div style="margin-top: 4px; color: #a8823c">
              建议抽象成方向，例如「手部常有下意识的小动作」「手边总要摆着点什么可把玩的东西」。
            </div>
          </div>
        </el-alert>

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

          <div style="display: flex; gap: 8px; align-items: center">
            <el-button type="primary" :loading="saving" @click="save">保存档案</el-button>
            <el-tooltip
              content="按这个角色在实体库里的档案（身份/年龄/外貌/性格/动机/秘密/死状…）推导出他的说话方式，生成草稿供你预览采用"
              placement="top"
            >
              <el-button :loading="generating" @click="generateDraft">✨ AI 生成草稿</el-button>
            </el-tooltip>
            <div style="flex: 1" />
            <el-button @click="emit('close')">完成</el-button>
          </div>
        </el-form>
      </template>
    </div>
  </div>

  <!-- AI 草稿预览：逐字段勾选 + 可编辑，改完再采用 -->
  <el-dialog
    v-model="showPreview"
    :title="`✨ ${detail?.name ?? ''} 的声音档案草稿`"
    width="720px"
    top="6vh"
    append-to-body
  >
    <el-alert type="info" :closable="false" style="margin-bottom: 12px">
      <template #title>
        这是 AI 依据实体库档案推导的<b>草稿</b>，还没写进档案库。
        逐字段确认：勾选的才会覆盖，不勾的保持你原来的内容。草稿可以直接改。
      </template>
    </el-alert>

    <div style="display: flex; flex-direction: column; gap: 10px; max-height: 56vh; overflow: auto; padding-right: 4px">
      <div
        v-for="k in (Object.keys(FIELD_LABELS) as (keyof Voiceprint)[])"
        :key="k"
        style="border: 1px solid #ebeef5; border-radius: 4px; padding: 8px 10px"
      >
        <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px">
          <el-checkbox v-model="adopted[k]" :disabled="!(draftForm[k] || '').trim()" />
          <b style="font-size: 13px">{{ FIELD_LABELS[k] }}</b>
          <el-tag v-if="hasAuthorContent(k)" size="small" type="warning" effect="plain">
            你已填过{{ adopted[k] ? ' · 将覆盖' : ' · 保持原值' }}
          </el-tag>
        </div>
        <el-input
          v-model="draftForm[k]"
          type="textarea"
          :rows="LIST_KEYS.includes(k as ListKey) ? 3 : 2"
          :placeholder="PLACEHOLDERS[k]"
        />
      </div>
    </div>

    <template #footer>
      <div style="display: flex; justify-content: space-between; align-items: center">
        <span style="font-size: 12px; color: #909399">
          已勾选 {{ Object.values(adopted).filter(Boolean).length }} / 8 个字段
        </span>
        <div>
          <el-button @click="showPreview = false">取消</el-button>
          <el-button type="primary" :loading="saving" @click="applyDraft">采用勾选字段</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>