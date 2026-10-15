<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { currentProjectId } from '../state'

const emit = defineEmits<{
  (e: 'done'): void
}>()

interface CardItem {
  key: string
  title: string
  filled: boolean
}

const cards: CardItem[] = [
  { key: 'premise', title: '核心设定（含讣告五铁律）', filled: false },
  { key: 'tangchi', title: '唐迟起始状态', filled: false },
  { key: 'endgame', title: '长线伏笔与终局', filled: false },
  { key: 'cost_debts', title: '代价与欠账', filled: false },
  { key: 'remaining_pages', title: '剩余页码（全书规模）', filled: false },
  { key: 'seven_day_pulse', title: '七日脉冲（节奏规律）', filled: false },
  { key: 'finale_notes', title: '终局注记', filled: false },
  { key: 'craft', title: '写作宪法', filled: false },
]

const activeNames = ref<string[]>(['premise'])
const loading = ref(true)
const filledCount = ref(0)

async function checkCards() {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  let filled = 0
  try {
    await Promise.all(
      cards.map(async (c) => {
        try {
          const r = await fetch(`/api/projects/${pid}/book/${c.key}`)
          if (!r.ok) return
          const d = await r.json()
          const data = d.data ?? {}
          c.filled = Object.keys(data).length > 0
          if (c.filled) filled++
        } catch {
          c.filled = false
        }
      }),
    )
  } finally {
    filledCount.value = filled
    loading.value = false
  }
}

function onDone() {
  emit('done')
}

onMounted(() => {
  checkCards()
})

defineExpose({ refresh: checkCards })
</script>

<template>
  <div class="book-setup">
    <div class="bs-header">
      <h2>开书设定</h2>
      <p class="bs-sub">
        完成下面 8 张书卡即可进入第一卷。
        <el-tag size="small" :type="filledCount === 8 ? 'success' : 'warning'">
          {{ filledCount }}/8 已填
        </el-tag>
      </p>
    </div>

    <el-skeleton v-if="loading" :rows="8" animated />

    <el-collapse v-else v-model="activeNames" class="bs-collapse">
      <el-collapse-item
        v-for="c in cards"
        :key="c.key"
        :name="c.key"
      >
        <template #title>
          <div class="card-title-row">
            <span>{{ c.title }}</span>
            <el-tag
              v-if="c.filled"
              size="small"
              type="success"
              effect="plain"
            >✓ 已填入</el-tag>
            <el-tag
              v-else
              size="small"
              type="danger"
              effect="plain"
            >未填入</el-tag>
          </div>
        </template>
        <div class="card-body">
          <el-empty description="编辑界面开发中（Task 13）" :image-size="60">
            <template #description>
              <span style="color:#909399; font-size:13px">
                编辑界面开发中（Task 13），请先通过 API 或后续编辑界面填入此卡内容。
              </span>
            </template>
          </el-empty>
        </div>
      </el-collapse-item>
    </el-collapse>

    <div class="bs-footer">
      <p class="bs-hint" v-if="filledCount < 8">
        注：即使卡片未填完也可点击按钮尝试进入——后端会根据真实卡状态自动判断是否满足开卷条件。
      </p>
      <el-button type="primary" size="large" @click="onDone" :loading="loading">
        完成开书设定，开始第一卷
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.book-setup {
  max-width: 820px;
  margin: 0 auto;
}
.bs-header {
  margin-bottom: 16px;
}
.bs-header h2 {
  margin: 0 0 4px;
  font-size: 22px;
  color: #303133;
}
.bs-sub {
  margin: 0;
  color: #909399;
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.bs-collapse {
  background: #fff;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}
.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding-right: 16px;
}
.card-body {
  padding: 8px 0;
}
.bs-footer {
  margin-top: 24px;
  text-align: center;
}
.bs-hint {
  font-size: 12px;
  color: #909399;
  margin: 0 0 12px;
}
</style>
