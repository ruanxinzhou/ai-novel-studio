<script setup lang="ts">
// 蓝星开篇引导页：phase = mainline-waiting 时显示
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { volumesApi } from '../api'
import { currentProjectId } from '../state'

const emit = defineEmits<{
  (e: 'done'): void
}>()

const loading = ref(false)
const skipping = ref(false)

async function createMainline() {
  const pid = currentProjectId.value
  if (!pid) return
  loading.value = true
  try {
    await volumesApi.create(pid, { kind: 'mainline' })
    ElMessage.success('蓝星开篇卷已创建')
    emit('done')
  } catch (e: any) {
    ElMessage.error(`创建失败：${e?.message || e}`)
  } finally {
    loading.value = false
  }
}

async function skipToDungeon() {
  const pid = currentProjectId.value
  if (!pid) return
  skipping.value = true
  try {
    // 次要按钮：直接建一个副本卷
    await volumesApi.create(pid, { kind: 'dungeon' })
    ElMessage.success('已创建副本卷')
    emit('done')
  } catch (e: any) {
    ElMessage.error(`创建失败：${e?.message || e}`)
  } finally {
    skipping.value = false
  }
}
</script>

<template>
  <div class="ml-landing">
    <div class="ml-stars" aria-hidden="true"></div>
    <div class="ml-card">
      <div class="ml-hero">🌍</div>
      <h1 class="ml-title">蓝星开篇</h1>
      <p class="ml-subtitle">在进入第一个副本前，先写两章蓝星日常作为全书的引子</p>

      <div class="ml-outline">
        <div class="ml-chap">
          <div class="ml-chap-no">第一章</div>
          <div class="ml-chap-body">
            <div class="ml-chap-title">蓝星日常 · 收到讣告</div>
            <div class="ml-chap-desc">主角的日常生活与暗藏的异常，神秘讣告送达</div>
          </div>
        </div>
        <div class="ml-divider"></div>
        <div class="ml-chap">
          <div class="ml-chap-no">第二章</div>
          <div class="ml-chap-body">
            <div class="ml-chap-title">讣告应验 · 穿越坠落</div>
            <div class="ml-chap-desc">死亡来临，意识坠入第一个副本</div>
          </div>
        </div>
        <div class="ml-meta">两章共约 9000 字，作为全书基调与主角锚点</div>
      </div>

      <div class="ml-actions">
        <el-button
          type="primary"
          size="large"
          class="ml-cta"
          :loading="loading"
          @click="createMainline"
        >
          创建蓝星开篇卷并开始
        </el-button>
        <el-button
          size="default"
          text
          class="ml-skip"
          :loading="skipping"
          @click="skipToDungeon"
        >
          跳过蓝星，直接建副本
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ml-landing {
  position: relative;
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  background: linear-gradient(135deg, #0b1f3a 0%, #14325f 45%, #1e4a8a 100%);
  overflow: hidden;
  min-height: 0;
}
.ml-stars {
  position: absolute;
  inset: 0;
  background-image:
    radial-gradient(1px 1px at 20% 30%, #ffffff88, transparent),
    radial-gradient(1px 1px at 70% 80%, #ffffff66, transparent),
    radial-gradient(1.5px 1.5px at 40% 60%, #ffffffaa, transparent),
    radial-gradient(1px 1px at 85% 20%, #ffffff77, transparent),
    radial-gradient(1px 1px at 10% 75%, #ffffff55, transparent),
    radial-gradient(1.5px 1.5px at 55% 15%, #ffffff99, transparent);
  pointer-events: none;
}
.ml-card {
  position: relative;
  z-index: 1;
  width: 100%;
  max-width: 560px;
  background: rgba(255, 255, 255, 0.06);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 16px;
  padding: 44px 40px 36px;
  color: #eaf2ff;
  text-align: center;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}
.ml-hero {
  font-size: 64px;
  line-height: 1;
  margin-bottom: 12px;
  filter: drop-shadow(0 4px 12px rgba(64, 158, 255, 0.5));
}
.ml-title {
  font-size: 32px;
  font-weight: 700;
  margin: 0 0 8px;
  letter-spacing: 2px;
}
.ml-subtitle {
  font-size: 14px;
  color: #b8cdf0;
  margin: 0 0 28px;
}

.ml-outline {
  background: rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 20px 22px;
  text-align: left;
  margin-bottom: 28px;
}
.ml-chap {
  display: flex;
  gap: 14px;
  align-items: flex-start;
}
.ml-chap-no {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #409eff, #1d6fd1);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
}
.ml-chap-body {
  flex: 1;
}
.ml-chap-title {
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  margin-bottom: 4px;
}
.ml-chap-desc {
  font-size: 13px;
  color: #b8cdf0;
  line-height: 1.6;
}
.ml-divider {
  height: 1px;
  background: rgba(255, 255, 255, 0.12);
  margin: 14px 0;
}
.ml-meta {
  margin-top: 14px;
  text-align: center;
  font-size: 12px;
  color: #8fb0e0;
  letter-spacing: 1px;
}

.ml-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.ml-cta {
  min-width: 240px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 1px;
  --el-button-bg-color: #409eff;
  --el-button-border-color: #409eff;
}
.ml-skip {
  font-size: 12px;
  color: #8fb0e0;
}
.ml-skip:hover {
  color: #fff;
}
</style>
