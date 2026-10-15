<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChatDotRound, Close } from '@element-plus/icons-vue'
import { currentPhase } from '../state'

const open = ref(false)
const inputText = ref('')

const PHASE_HINTS: Record<string, { hint: string; shortcuts: string[] }> = {
  setup: {
    hint: '开书设定阶段：先和 AI 聊聊核心设定、主角起点与终局吧。',
    shortcuts: ['帮我想一个讣告铁律', '唐迟初始状态该怎么写', '七日后终局怎么收'],
  },
  adapt: {
    hint: '剧本改编阶段：可以让 AI 帮忙改编副本素材，规避版权风险。',
    shortcuts: ['上传 PDF 改编', '帮我梳理副本要素', '风险点检查'],
  },
  adapt_empty: {
    hint: '新卷待启动：先聊聊新副本的核心诡计与场景吧。',
    shortcuts: ['新副本灵感', '帮我列 3 个核心场景', '设定副本规则'],
  },
  simulate: {
    hint: '模拟跑团阶段：AI 可以帮你推演多线剧情走向。',
    shortcuts: ['COC 三版模拟', '主角厄运分支', '关键 NPC 反应'],
  },
  embody: {
    hint: '魂穿设计阶段：把跑团结果落到唐迟第一视角。',
    shortcuts: ['帮我写魂穿体验', 'SAN 值扣减描写', '身体记忆错位'],
  },
  scaffold: {
    hint: '卷内详设阶段：细化本卷的章节节奏与伏笔分布。',
    shortcuts: ['拆 7 日节奏', '伏笔分布表', '章末钩子建议'],
  },
  rhythm: {
    hint: '节奏骨架阶段：梳理本卷的峰谷节奏和三线配比。',
    shortcuts: ['检查三线占比', '高潮位置建议', '节奏饥饿报警'],
  },
  briefing: {
    hint: '章前规划阶段：开写前明确本章任务书。',
    shortcuts: ['生成本章任务书', '必现标记清单', '上章结尾回顾'],
  },
  desk: {
    hint: '日更阶段：大纲 → 正文 → 润色，一步步推进。',
    shortcuts: ['续写大纲', '生成正文', '润色本章'],
  },
  settle: {
    hint: '杀青阶段：做卷末总结与状态结算。',
    shortcuts: ['卷末总结', '污染结算', '主角状态更新'],
  },
  interlude: {
    hint: '蓝星间章：写一段蓝星日常，调节节奏。',
    shortcuts: ['蓝星日常灵感', '羁绊线推进', '伏笔埋入'],
  },
}

const phaseInfo = computed(() => {
  return PHASE_HINTS[currentPhase.value] ?? {
    hint: '我是 AI 聊一谈，随时可以和我讨论剧情、设定或写作问题。',
    shortcuts: ['帮我润色一段', '检查逻辑漏洞', '生成灵感'],
  }
})

function toggle() {
  open.value = !open.value
}

function useShortcut(text: string) {
  inputText.value = text
}

function send() {
  // TODO: Task 后续接入 sessionsApi.send，这里暂不处理
  console.log('[AIChatFloat] send TODO:', inputText.value)
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    // 回车暂不发送，等接好 API 后再放开
    // send()
  }
}
</script>

<template>
  <div class="ai-chat-float">
    <Transition name="fade">
      <div v-if="open" class="ai-panel">
        <div class="ai-panel-header">
          <span>💬 AI 聊一谈</span>
          <el-button :icon="Close" circle size="small" text @click="open = false" />
        </div>
        <div class="ai-panel-body">
          <div class="ai-hint">
            <el-icon><ChatDotRound /></el-icon>
            <span>{{ phaseInfo.hint }}</span>
          </div>
          <div class="ai-shortcuts">
            <el-tag
              v-for="s in phaseInfo.shortcuts"
              :key="s"
              class="sc-tag"
              effect="plain"
              type="primary"
              @click="useShortcut(s)"
            >
              {{ s }}
            </el-tag>
          </div>
          <div class="ai-msg-list">
            <div class="ai-msg ai">
              <div class="ai-msg-bubble">你好！有什么想聊的，随时告诉我。</div>
            </div>
          </div>
        </div>
        <div class="ai-panel-footer">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="3"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            resize="none"
            @keydown="onKeydown"
          />
          <div class="ai-send-row">
            <el-button type="primary" size="small" @click="send" :disabled="!inputText.trim()">发送</el-button>
          </div>
        </div>
      </div>
    </Transition>

    <el-button
      v-if="!open"
      class="ai-fab"
      type="primary"
      :icon="ChatDotRound"
      circle
      size="large"
      @click="toggle"
    />
  </div>
</template>

<style scoped>
.ai-chat-float {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2000;
}
.ai-fab {
  width: 52px;
  height: 52px;
  font-size: 22px;
  box-shadow: 0 4px 16px rgba(64, 158, 255, 0.35);
}
.ai-panel {
  width: 380px;
  height: 480px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-bottom: 12px;
}
.ai-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
  font-weight: 600;
  font-size: 14px;
}
.ai-panel-body {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
  background: #f7f8fa;
}
.ai-hint {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  font-size: 12px;
  color: #606266;
  background: #ecf5ff;
  padding: 8px 10px;
  border-radius: 8px;
  margin-bottom: 10px;
  line-height: 1.5;
}
.ai-shortcuts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.sc-tag {
  cursor: pointer;
  font-size: 12px;
}
.sc-tag:hover {
  background: #ecf5ff;
}
.ai-msg-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ai-msg {
  display: flex;
}
.ai-msg.ai {
  justify-content: flex-start;
}
.ai-msg-bubble {
  max-width: 80%;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.5;
  background: #fff;
  border: 1px solid #ebeef5;
}
.ai-panel-footer {
  border-top: 1px solid #ebeef5;
  padding: 10px 12px;
  background: #fff;
}
.ai-send-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 6px;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(10px) scale(0.95);
}
</style>
