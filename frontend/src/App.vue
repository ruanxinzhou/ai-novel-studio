<script setup lang="ts">
// M0 占位页：验证前后端连通。M1 起替换为四层创作流工作台。
import { onMounted, ref } from 'vue'

const health = ref('...')
const providers = ref<Record<string, unknown>>({})

onMounted(async () => {
  health.value = await (await fetch('/health')).json()
  providers.value = await (await fetch('/providers')).json()
})
</script>

<template>
  <main style="font-family: system-ui; max-width: 720px; margin: 60px auto; line-height: 1.8">
    <h1>Novel Studio</h1>
    <p>后端健康状态：<code>{{ JSON.stringify(health) }}</code></p>
    <h3>通道状态</h3>
    <pre>{{ JSON.stringify(providers, null, 2) }}</pre>
    <p style="color: #888">M1 将在此呈现：大纲 | 章节 | 剧情 | 正文 四层创作流 + Prompt 工坊</p>
  </main>
</template>
