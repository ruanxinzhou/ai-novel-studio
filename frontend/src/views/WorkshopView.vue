<script setup lang="ts">
// Prompt 工坊：双层模板查看/编辑/重置 + 干跑调试（看最终 messages）
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  LAYER_LABELS,
  api,
  type PromptDetail,
  type PromptSummary,
} from '../api'

const summaries = ref<PromptSummary[]>([])
const active = ref('')
const detail = ref<PromptDetail | null>(null)
const editing = ref(false)
const editText = ref('')
const editNote = ref('')
const dryX = ref('')
const dryDirective = ref('')
const dryResult = ref<Awaited<ReturnType<typeof api.promptsDryRun>> | null>(null)
const saving = ref(false)
const drying = ref(false)

// 修改历史服务端分页
const historyPage = ref(1)
const historyPageSize = 10
const historyTotal = ref(0)

onMounted(refresh)

async function refresh() {
  try {
    const r = await api.promptsList()
    summaries.value = r.layers
    if (!active.value && r.layers.length) await select(r.layers[0].layer)
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
}

async function loadDetail(layer: string, page: number) {
  const r = await api.promptsDetail(layer, page, historyPageSize)
  detail.value = r
  historyTotal.value = r.total ?? r.history.length
  historyPage.value = page
}

async function select(layer: string) {
  active.value = layer
  editing.value = false
  dryResult.value = null
  await loadDetail(layer, 1)
}

function historyPageChange(p: number) {
  if (detail.value) loadDetail(detail.value.layer, p).catch((e) => ElMessage.error(`加载失败：${(e as Error).message}`))
}

function startEdit() {
  if (!detail.value) return
  editText.value = detail.value.effective
  editNote.value = ''
  editing.value = true
}

async function save() {
  if (!detail.value) return
  saving.value = true
  try {
    await api.promptsEdit(detail.value.layer, editText.value, editNote.value)
    ElMessage.success('已保存覆盖（历史已留痕）')
    editing.value = false
    await refresh()
    await select(detail.value.layer)
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    saving.value = false
  }
}

async function reset() {
  if (!detail.value) return
  try {
    await api.promptsReset(detail.value.layer)
    ElMessage.success('已重置为默认指令')
    await refresh()
    await select(detail.value.layer)
  } catch (e) {
    ElMessage.error(`重置失败：${(e as Error).message}`)
  }
}

async function dryRun() {
  if (!detail.value) return
  drying.value = true
  try {
    dryResult.value = await api.promptsDryRun(detail.value.layer, dryX.value, dryDirective.value)
  } catch (e) {
    ElMessage.error(`干跑失败：${(e as Error).message}`)
  } finally {
    drying.value = false
  }
}
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="6">
      <el-card shadow="never">
        <template #header>层指令</template>
        <el-menu :default-active="active" @select="select">
          <el-menu-item v-for="s in summaries" :key="s.layer" :index="s.layer">
            <span>{{ LAYER_LABELS[s.layer] ?? s.layer }}</span>
            <el-tag v-if="s.overridden" size="small" type="warning" style="margin-left: 8px">已覆盖</el-tag>
          </el-menu-item>
        </el-menu>
      </el-card>
    </el-col>

    <el-col :span="18">
      <template v-if="detail">
        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>{{ LAYER_LABELS[detail.layer] ?? detail.layer }} 指令</span>
              <div>
                <el-button size="small" @click="startEdit">编辑</el-button>
                <el-button size="small" type="danger" plain :disabled="!detail.overridden" @click="reset">
                  重置默认
                </el-button>
              </div>
            </div>
          </template>
          <template v-if="editing">
            <el-input v-model="editText" type="textarea" :rows="8" />
            <el-input v-model="editNote" placeholder="修改备注（可选）" style="margin-top: 8px" />
            <div style="margin-top: 8px">
              <el-button type="primary" :loading="saving" @click="save">保存</el-button>
              <el-button @click="editing = false">取消</el-button>
            </div>
          </template>
          <pre v-else style="white-space: pre-wrap; font-family: inherit; line-height: 1.8">{{ detail.effective }}</pre>
        </el-card>

        <el-card shadow="never" style="margin-bottom: 16px">
          <template #header>干跑调试（只渲染不调用 LLM）</template>
          <el-input v-model="dryX" type="textarea" :rows="3" placeholder="x 块样例（模拟真实输入）" />
          <el-input v-model="dryDirective" style="margin-top: 8px" placeholder="作者指令样例（可空）" />
          <el-button type="primary" plain :loading="drying" style="margin-top: 8px" @click="dryRun">
            渲染最终 Prompt
          </el-button>
          <template v-if="dryResult">
            <div style="margin: 8px 0">
              <el-tag size="small">估算 ~{{ dryResult.est_prompt_tokens }} tok</el-tag>
              <el-tag size="small" type="info" style="margin-left: 6px">{{ dryResult.prompt_bytes }} 字节</el-tag>
              <el-tag v-for="(w, i) in dryResult.warnings" :key="i" size="small" type="warning" style="margin-left: 6px">
                {{ w }}
              </el-tag>
            </div>
            <el-collapse>
              <el-collapse-item v-for="(m, i) in dryResult.messages" :key="i" :title="`[${m.role}] (${m.content.length} 字)`">
                <pre style="white-space: pre-wrap; font-family: inherit; max-height: 400px; overflow: auto">{{ m.content }}</pre>
              </el-collapse-item>
            </el-collapse>
          </template>
        </el-card>

        <el-card v-if="detail.history.length" shadow="never">
          <template #header>修改历史（{{ historyTotal }}）</template>
          <el-timeline>
            <el-timeline-item
              v-for="(h, i) in [...detail.history].reverse()"
              :key="i"
              :timestamp="h.timestamp"
              :type="h.source === 'user' ? 'warning' : 'info'"
            >
              <b>{{ h.source }}</b>
              <span v-if="h.note" style="color: #888"> — {{ h.note }}</span>
              <pre style="white-space: pre-wrap; font-family: inherit; color: #555; margin: 4px 0 0">{{ h.text.slice(0, 200) }}{{ h.text.length > 200 ? '…' : '' }}</pre>
            </el-timeline-item>
          </el-timeline>
          <el-pagination
            v-if="historyTotal > historyPageSize"
            v-model:current-page="historyPage"
            :page-size="historyPageSize"
            :total="historyTotal"
            layout="prev, pager, next, total"
            size="small"
            style="margin-top: 8px; justify-content: flex-end"
            @current-change="historyPageChange"
          />
        </el-card>
      </template>
    </el-col>
  </el-row>
</template>
