<script setup lang="ts">
// 必现标记管理：【第N章必现】提取/手动添加/状态流转/正文兑现校验
import { onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { markersApi, type MarkerInfo } from '../api'
import { currentProjectId } from '../state'

const markers = ref<MarkerInfo[]>([])
const pendingCount = ref(0)
const addChapter = ref<number>(1)
const addContent = ref('')
const extractText = ref('')
const verifyProse = ref('')
const verifyResults = ref<{ marker: MarkerInfo; hit: boolean }[]>([])
const busy = ref(false)

async function refresh() {
  try {
    const r = await markersApi.list(currentProjectId.value || undefined)
    markers.value = r.markers
    pendingCount.value = r.pending_count
  } catch (e) {
    ElMessage.error(`加载失败：${(e as Error).message}`)
  }
}
onMounted(refresh)
watch(currentProjectId, () => refresh())

async function add() {
  if (!addContent.value.trim()) {
    ElMessage.warning('请填写标记内容')
    return
  }
  busy.value = true
  try {
    await markersApi.add(addChapter.value, addContent.value, currentProjectId.value || undefined)
    addContent.value = ''
    ElMessage.success('已添加')
    await refresh()
  } catch (e) {
    ElMessage.error(`添加失败：${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

async function extract() {
  if (!extractText.value.trim()) {
    ElMessage.warning('请粘贴含【第N章必现】的大纲/剧情文本')
    return
  }
  busy.value = true
  try {
    const r = await markersApi.extract(extractText.value)
    ElMessage.success(`提取到 ${r.added?.length ?? 0} 条新标记`)
    extractText.value = ''
    await refresh()
  } catch (e) {
    ElMessage.error(`提取失败：${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

async function setStatus(m: MarkerInfo, status: string) {
  try {
    await markersApi.setStatus(m.id, status)
    await refresh()
  } catch (e) {
    ElMessage.error(`状态修改失败：${(e as Error).message}`)
  }
}

async function verify() {
  if (!verifyProse.value.trim()) {
    ElMessage.warning('请粘贴待校验的章正文')
    return
  }
  busy.value = true
  try {
    const r = await markersApi.verify(verifyProse.value, undefined, currentProjectId.value || undefined)
    verifyResults.value = r.results
    const hits = r.results.filter((x) => x.hit).length
    ElMessage.info(`校验完成：${hits}/${r.results.length} 条命中`)
  } catch (e) {
    ElMessage.error(`校验失败：${(e as Error).message}`)
  } finally {
    busy.value = false
  }
}

const STATUS_LABEL: Record<string, string> = {
  pending: '待兑现',
  done: '已兑现',
  dropped: '已放弃',
}
const SOURCE_LABEL: Record<string, string> = {
  manual: '手动',
  extracted: '提取',
  review: '审稿',
}
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="10">
      <el-card shadow="never" style="margin-bottom: 16px">
        <template #header>
          手动添加 <el-tag size="small" style="margin-left: 8px">待兑现 {{ pendingCount }}</el-tag>
        </template>
        <el-form inline>
          <el-form-item label="第">
            <el-input-number v-model="addChapter" :min="1" style="width: 100px" />
          </el-form-item>
          <el-form-item label="章必现">
            <el-input v-model="addContent" placeholder="如：主角觉醒金手指" style="width: 240px" @keyup.enter="add" />
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="busy" @click="add">添加</el-button>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never" style="margin-bottom: 16px">
        <template #header>从大纲/剧情批量提取（粘贴含「【第N章必现】…」的文本）</template>
        <el-input v-model="extractText" type="textarea" :rows="4" placeholder="第3章【第3章必现】主角觉醒金手指。&#10;第5章【第5章必现】黑衣人身份揭晓" />
        <el-button type="primary" plain :loading="busy" style="margin-top: 8px" @click="extract">提取并入册</el-button>
      </el-card>

      <el-card shadow="never">
        <template #header>正文兑现校验（子串命中检测）</template>
        <el-input v-model="verifyProse" type="textarea" :rows="4" placeholder="粘贴章正文，检测 pending 标记是否已出现" />
        <el-button :loading="busy" style="margin-top: 8px" @click="verify">校验</el-button>
        <div v-if="verifyResults.length" style="margin-top: 8px">
          <div v-for="r in verifyResults" :key="r.marker.id" style="font-size: 13px; margin-bottom: 4px">
            <el-tag :type="r.hit ? 'success' : 'danger'" size="small">{{ r.hit ? '命中' : '未命中' }}</el-tag>
            第{{ r.marker.target_chapter }}章 · {{ r.marker.content }}
          </div>
        </div>
      </el-card>
    </el-col>

    <el-col :span="14">
      <el-card shadow="never">
        <template #header>标记清单（{{ markers.length }}）</template>
        <el-empty v-if="!markers.length" description="还没有标记；在写作层填章号运行时，待兑现标记会自动注入生成上下文" />
        <el-table v-else :data="markers" size="small">
          <el-table-column prop="target_chapter" label="章" width="60" />
          <el-table-column prop="content" label="必现内容" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="90">
            <template #default="{ row }">
              <el-tag :type="row.status === 'pending' ? 'warning' : row.status === 'done' ? 'success' : 'info'" size="small">
                {{ STATUS_LABEL[row.status as string] ?? row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="source" label="来源" width="70">
            <template #default="{ row }">{{ SOURCE_LABEL[row.source as string] ?? row.source }}</template>
          </el-table-column>
          <el-table-column label="操作" width="150">
            <template #default="{ row }">
              <el-button v-if="row.status !== 'done'" size="small" type="success" plain @click="setStatus(row, 'done')">兑现</el-button>
              <el-button v-if="row.status === 'pending'" size="small" plain @click="setStatus(row, 'dropped')">放弃</el-button>
              <el-button v-if="row.status !== 'pending'" size="small" plain @click="setStatus(row, 'pending')">重开</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>
