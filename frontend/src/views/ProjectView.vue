<script setup lang="ts">
// 项目工作台：五层分步导航，逐层回看/编辑/采纳/回滚——一本小说一条主线
import { onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  LAYER_LABELS,
  LAYERS,
  projectsApi,
  vaultApi,
  type LayerVersionFull,
  type ProjectDetail,
} from '../api'
import { currentProjectId } from '../state'

const detail = ref<ProjectDetail | null>(null)
const activeLayer = ref('intro')
const viewingSeq = ref<number | null>(null) // null = 当前版本
const version = ref<LayerVersionFull | null>(null)
const history = ref<{ seq: number; note: string; timestamp: string; y_chars: number }[]>([])
const editing = ref(false)
const editText = ref('')
const adoptNote = ref('')
const busy = ref(false)
const exporting = ref(false)
const lastExportDir = ref('')

async function exportVault() {
  if (!currentProjectId.value) return
  exporting.value = true
  try {
    const r = await vaultApi.export(currentProjectId.value)
    lastExportDir.value = r.out_dir
    const parts = [`文件 ${r.files} 个`]
    if (r.chapters) parts.push(`章节 ${r.chapters} 章`)
    if (r.entities) parts.push(`实体 ${r.entities} 个`)
    ElMessage.success(`已导出 Obsidian Vault：${parts.join('，')}`)
    r.warnings.forEach((w) => ElMessage.warning(w))
  } catch (e) {
    ElMessage.error(`导出失败：${(e as Error).message}`)
  } finally {
    exporting.value = false
  }
}

async function refresh() {
  if (!currentProjectId.value) {
    detail.value = null
    return
  }
  try {
    detail.value = await projectsApi.detail(currentProjectId.value)
    await showLayer(activeLayer.value)
  } catch (e) {
    ElMessage.error(`加载项目失败：${(e as Error).message}`)
  }
}

async function showLayer(layer: string, seq?: number) {
  activeLayer.value = layer
  viewingSeq.value = seq ?? null
  editing.value = false
  if (!currentProjectId.value) return
  try {
    const r = await projectsApi.layer(currentProjectId.value, layer, seq)
    version.value = r.version
    history.value = r.history
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
}

function startEdit() {
  editText.value = version.value?.y ?? ''
  adoptNote.value = ''
  editing.value = true
}

async function adopt() {
  if (!currentProjectId.value || !editText.value.trim()) {
    ElMessage.warning('内容不能为空')
    return
  }
  busy.value = true
  try {
    await projectsApi.adopt(
      currentProjectId.value,
      activeLayer.value,
      editText.value,
      version.value?.x_pairs ?? [],
      adoptNote.value,
    )
    ElMessage.success(`已采纳为 ${LAYER_LABELS[activeLayer.value]} 新版本`)
    editing.value = false
    await refresh()
    await showLayer(activeLayer.value)
  } catch (e) {
    ElMessage.error(`采纳失败：${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

async function rollback(seq: number) {
  try {
    await ElMessageBox.confirm(`把「${LAYER_LABELS[activeLayer.value]}」回滚到 v${seq}？（原样存为新版本，历史保留）`, '回滚确认')
  } catch {
    return
  }
  try {
    await projectsApi.rollback(currentProjectId.value, activeLayer.value, seq)
    ElMessage.success('已回滚')
    await refresh()
  } catch (e) {
    ElMessage.error(`回滚失败：${(e as Error).message}`)
  }
}

onMounted(refresh)
watch(currentProjectId, () => refresh())
</script>

<template>
  <div v-if="!currentProjectId">
    <el-empty description="先在顶部选择或新建一个小说项目" />
  </div>
  <el-row v-else-if="detail" :gutter="16">
    <el-col :span="6">
      <el-card shadow="never">
        <template #header>
          <div>
            <div style="font-weight: 600">{{ detail.title }}</div>
            <div style="font-size: 12px; color: #888; margin-top: 4px">{{ detail.intro || '（无简介）' }}</div>
          </div>
        </template>
        <div
          v-for="l in LAYERS"
          :key="l"
          class="layer-item"
          :class="{ active: l === activeLayer }"
          @click="showLayer(l)"
        >
          <span>{{ LAYER_LABELS[l] }}</span>
          <el-tag v-if="detail.layers[l]?.current" size="small" type="success">
            v{{ detail.layers[l].current!.seq }}
          </el-tag>
          <el-tag v-else size="small" type="info">未采纳</el-tag>
        </div>
        <el-divider />
        <el-button style="width: 100%" :loading="exporting" @click="exportVault">
          导出 Obsidian Vault
        </el-button>
        <div v-if="lastExportDir" style="font-size: 12px; color: #888; margin-top: 8px; word-break: break-all">
          最近导出：{{ lastExportDir }}
          <br />用 Obsidian「打开文件夹作为仓库」即可看到互链图谱。
        </div>
        <el-divider />
        <div style="font-size: 12px; color: #aaa">
          分步流程：简介 → 大纲 → 剧情 → 正文 → 润色。每层生成后在运行台调试，
          满意后「采纳到项目」；本页随时回看与修改任何一层。
        </div>
      </el-card>
    </el-col>

    <el-col :span="18">
      <el-card shadow="never">
        <template #header>
          <div style="display: flex; justify-content: space-between; align-items: center">
            <span>
              {{ LAYER_LABELS[activeLayer] }}
              <el-tag v-if="version" size="small" style="margin-left: 8px">
                v{{ version.seq }}{{ viewingSeq ? '（历史）' : '（当前）' }}
              </el-tag>
              <span v-if="version?.note" style="color: #888; font-size: 12px; margin-left: 8px">{{ version.note }}</span>
            </span>
            <el-button size="small" type="primary" plain @click="startEdit">编辑并采纳新版本</el-button>
          </div>
        </template>

        <template v-if="editing">
          <el-input v-model="editText" type="textarea" :rows="16" />
          <el-input v-model="adoptNote" placeholder="版本备注（如：调整了第3章节奏）" style="margin-top: 8px" />
          <div style="margin-top: 8px">
            <el-button type="primary" :loading="busy" @click="adopt">采纳为新版本</el-button>
            <el-button @click="editing = false">取消</el-button>
          </div>
        </template>
        <template v-else-if="version">
          <pre style="white-space: pre-wrap; font-family: inherit; line-height: 1.9; max-height: 480px; overflow: auto">{{ version.y }}</pre>
          <el-collapse v-if="version.x_pairs.length" style="margin-top: 8px">
            <el-collapse-item title="输入 x 块">
              <div v-for="(p, i) in version.x_pairs" :key="i" style="font-size: 13px; color: #666; margin-bottom: 4px">
                {{ p[0] }}
              </div>
            </el-collapse-item>
          </el-collapse>
        </template>
        <el-empty v-else description="该层还没有采纳版本；去「运行台」生成后点「采纳到项目」" />

        <template v-if="history.length">
          <el-divider>版本历史（{{ history.length }}）</el-divider>
          <el-table :data="history" size="small" @row-click="(row: any) => showLayer(activeLayer, row.seq)">
            <el-table-column prop="seq" label="#" width="60" />
            <el-table-column prop="note" label="备注" show-overflow-tooltip />
            <el-table-column prop="timestamp" label="时间" width="160" />
            <el-table-column prop="y_chars" label="字数" width="80" />
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" plain :disabled="row.seq === version?.seq" @click.stop="rollback(row.seq)">
                  回滚
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </template>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.layer-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
}
.layer-item:hover {
  background: #f5f7fa;
}
.layer-item.active {
  background: #ecf5ff;
}
</style>
