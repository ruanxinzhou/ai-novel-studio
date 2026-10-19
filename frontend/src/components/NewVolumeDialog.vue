<script setup lang="ts">
// 新建卷对话框：副本(dungeon)/间章(interlude)/（高级：mainline 开篇卷）
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { volumesApi } from '../api'
import { currentProjectId } from '../state'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'created', volumeNo: number): void
}>()

type Kind = 'dungeon' | 'interlude' | 'mainline'
const kind = ref<Kind>('dungeon')
const title = ref('')
const showAdvanced = ref(false)
const submitting = ref(false)

watch(
  () => props.modelValue,
  (v) => {
    if (v) {
      kind.value = 'dungeon'
      title.value = ''
      showAdvanced.value = false
      submitting.value = false
    }
  },
)

function close() {
  emit('update:modelValue', false)
}

const kindOptions: { value: Kind; icon: string; label: string; desc: string }[] = [
  { value: 'dungeon', icon: '📕', label: '副本卷', desc: '进入无限流副本' },
  { value: 'interlude', icon: '🌌', label: '蓝星间章', desc: '副本之间回蓝星过渡' },
]

async function onSubmit() {
  const pid = currentProjectId.value
  if (!pid) {
    ElMessage.error('未选择项目')
    return
  }
  submitting.value = true
  try {
    const resp = await volumesApi.create(pid, { kind: kind.value, title: title.value.trim() || undefined })
    ElMessage.success('卷已创建')
    emit('created', resp.volume_no)
    close()
  } catch (e: any) {
    ElMessage.error(`创建失败：${e?.message || e}`)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="新建卷"
    width="480px"
    :close-on-click-modal="false"
    @update:model-value="(v: boolean) => emit('update:modelValue', v)"
  >
    <div class="nvd-body">
      <div class="nvd-label">卷类型</div>
      <el-radio-group v-model="kind" class="nvd-kind-group">
        <el-radio
          v-for="opt in kindOptions"
          :key="opt.value"
          :value="opt.value"
          class="nvd-kind-item"
          border
        >
          <div class="nvd-kind-row">
            <span class="nvd-kind-icon">{{ opt.icon }}</span>
            <div class="nvd-kind-text">
              <div class="nvd-kind-label">{{ opt.label }}</div>
              <div class="nvd-kind-desc">{{ opt.desc }}</div>
            </div>
          </div>
        </el-radio>
      </el-radio-group>

      <div v-if="showAdvanced" class="nvd-advanced">
        <el-radio v-model="kind" value="mainline" border class="nvd-kind-item">
          <div class="nvd-kind-row">
            <span class="nvd-kind-icon">🌍</span>
            <div class="nvd-kind-text">
              <div class="nvd-kind-label">蓝星开篇卷</div>
              <div class="nvd-kind-desc">全书引子（一般在向导阶段自动创建）</div>
            </div>
          </div>
        </el-radio>
      </div>

      <el-button
        link
        type="primary"
        class="nvd-advanced-toggle"
        @click="showAdvanced = !showAdvanced"
      >
        {{ showAdvanced ? '收起高级选项' : '高级选项：这是开篇卷' }}
      </el-button>

      <div class="nvd-label" style="margin-top: 16px">标题（可选）</div>
      <el-input
        v-model="title"
        placeholder="例如：第一副本 · 午夜博物馆"
        maxlength="60"
        show-word-limit
        @keyup.enter="onSubmit"
      />
    </div>

    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="onSubmit">确定</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.nvd-body {
  padding: 4px 0;
}
.nvd-label {
  font-size: 13px;
  color: #606266;
  margin-bottom: 8px;
  font-weight: 500;
}
.nvd-kind-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
}
.nvd-kind-item {
  margin-right: 0 !important;
  width: 100%;
  padding: 10px 14px;
  height: auto;
  line-height: 1.4;
}
.nvd-kind-item :deep(.el-radio__label) {
  width: 100%;
  padding-left: 10px;
}
.nvd-kind-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.nvd-kind-icon {
  font-size: 22px;
  line-height: 1;
}
.nvd-kind-text {
  flex: 1;
}
.nvd-kind-label {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.nvd-kind-desc {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.nvd-advanced {
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.nvd-advanced-toggle {
  margin-top: 10px;
  font-size: 12px;
  padding: 0;
}
</style>
