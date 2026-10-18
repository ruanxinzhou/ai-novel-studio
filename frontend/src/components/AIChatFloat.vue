<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ChatDotRound, Close, Promotion } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { chatApi, type ChatMessage } from '../api'
import { useChatContext } from '../chatContext'
import {
  currentPhase,
  currentVolumeNo,
  currentChapterNo,
  currentVolumeKind,
  currentIsLastChapter,
  isReviewingVolume,
  currentProjectId,
} from '../state'

// ---- 根据 phase 字符串映射到中文阶段名 ----
const PHASE_NAME: Record<string, string> = {
  setup: '📖 开书设定',
  adapt: '📕 上传 COC 模组并改编',
  adapt_empty: '📕 上传 COC 模组并改编',
  simulate: '🎲 COC 并行跑团',
  embody: '🫀 魂穿入戏',
  scaffold: '🏗️ 副本详设',
  rhythm: '📅 节奏骨架',
  briefing: '📝 章前 5 分钟方向卡',
  desk: '✍️ 章台写作',
  settle: '🧾 杀青归账',
  interlude: '🌌 蓝星间章',
}
function inferPhase(): string {
  if (isReviewingVolume.value) return '📚 旧卷回看'
  return PHASE_NAME[currentPhase.value] || '小说工坊'
}

const PHASE_HINTS: Record<string, string> = {
  '📖 开书设定': '作者刚开书，可能在选背景/世界观/主角基调。',
  '📕 上传 COC 模组并改编':
    '作者刚上传一个 COC 模组 PDF，AI 并行生成 3-8 版差异化改编（基调/背景/角色/事件重写）。这一阶段只处理副本本身的独立故事（原剧本有自己的时间线，可以是几小时/几天/几个月/几代人），不提前注入七天死期。作者在挑一版，可能对某一版的基调、角色能力、改编方向不满意。',
  '🎲 COC 并行跑团': '并行跑团阶段，AI 开多线程跑同模组收集关键 NPC / 事件 / 规则，推演副本原剧本的走向。此阶段不涉及唐迟七天倒计时。',
  '🫀 魂穿入戏':
    '【双轨时间线启动点】作者在选宿主（魂穿哪个角色，可以是 PL 调查员或任意 NPC）、切入点（此刻原剧本在什么事件？睁眼第一秒是什么状态？三幕：假死因红鲱鱼→核心反转→规则利用）、求生姿态。七天死期是从这一步才加在唐迟身上的——他的 T-7→T-0 个人倒计时从睁眼启动；同时原剧本事件继续按自己节奏推进。',
  '🏗️ 副本详设': '作者在做副本详设（基于宿主+切入点+反应的结构化方案，含线索卡/人物关系/伏笔节点），需要兼顾唐迟七天倒计时与原剧本事件并行。',
  '📅 节奏骨架': '作者在唐迟入戏第1-7天（T-6→T-0，这是唐迟个人倒计时，不是原剧本的 Day1-Day7）上排节拍/事件/气氛曲线/章节分配，同时标注原剧本此刻事件（双轨时间线并行）。',
  '📝 章前 5 分钟方向卡': '作者在敲定下一章的方向卡（前章锚点/本章目标/必现标记/情绪曲线），day 指唐迟入戏第几天。',
  '✍️ 章台写作': '作者在某章的 plot→draft→polish→review→finalize 流程中，要注意九项审稿、白描锁、长线伏笔，以及唐迟倒计时/原剧本事件的双轨清晰度。',
  '🧾 杀青归账': '作者在整卷定稿/归账，检查伏笔兑现、实体归档、审稿结果、T-0代价是否落地。',
  '🌌 蓝星间章': '作者在写蓝星现实线的间章，交代唐迟身体/精神状态变化、长期能力沉淀。',
  '📚 旧卷回看': '作者在回看已定稿的旧卷，可能在检查伏笔或想反定稿修改。',
}

const getPageCtx = useChatContext()

const open = ref(false)
const input = ref('')
const sending = ref(false)
const messages = ref<ChatMessage[]>([])
const scrollRef = ref<HTMLElement | null>(null)

// 初次打开时给欢迎语（不是 AI 回复，只显示一次）
onMounted(() => {
  messages.value = [{ role: 'assistant', content: '你好！我是这本书的 AI 写作搭子，看到你现在的页面和作品进度。要改什么、吐槽什么、要灵感，直接说就好。' }]
})

function toggle() { open.value = !open.value }

function _buildSnapshot() {
  const phaseFromState = inferPhase()
  const page = getPageCtx?.() ?? null
  const phase = page?.phase || phaseFromState
  const system_hint = page?.system_hint || PHASE_HINTS[phaseFromState] || ''
  const progress = {
    project_id: currentProjectId.value || null,
    volume_no: currentVolumeNo.value,
    chapter_no: currentChapterNo.value ?? null,
    volume_kind: currentVolumeKind.value,
    is_last_chapter: currentIsLastChapter.value,
    is_reviewing: isReviewingVolume.value,
  }
  const context = page?.context || null
  return { phase, system_hint, progress, context }
}

async function _scrollBottom() {
  await nextTick()
  if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight
}

async function send() {
  const content = input.value.trim()
  if (!content || sending.value) return
  messages.value.push({ role: 'user', content })
  input.value = ''
  sending.value = true
  await _scrollBottom()
  // 先放一条 thinking 占位
  const thinkIdx = messages.value.length
  messages.value.push({ role: 'assistant', content: '思考中…' })
  await _scrollBottom()
  try {
    const { phase, system_hint, progress, context } = _buildSnapshot()
    // 只送最近 30 条，避免爆炸
    const msgs = messages.value.slice(0, -1).slice(-30) // 去掉 thinking 占位
    const resp = await chatApi.send({ messages: msgs, phase, system_hint, progress, context })
    messages.value[thinkIdx] = { role: 'assistant', content: resp.text }
  } catch (e: any) {
    messages.value[thinkIdx] = { role: 'assistant', content: `（调用失败：${e?.message || e}）` }
    ElMessage.error('AI 调用失败')
  } finally {
    sending.value = false
    _scrollBottom()
  }
}

function clearAll() {
  messages.value = [{ role: 'assistant', content: '（已清空对话，我继续盯着当前页面。有新问题直接说。）' }]
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

watch(open, (v) => { if (v) _scrollBottom() })

const placeholder = computed(() => {
  const { phase } = _buildSnapshot()
  return `在【${phase}】阶段有什么想聊的？（Enter 发送，Shift+Enter 换行）`
})
</script>

<template>
  <div class="ai-float-root">
    <Transition name="ai-pop">
      <div v-if="open" class="ai-panel">
        <div class="ai-head">
          <div class="ai-head-title">
            <el-icon><ChatDotRound /></el-icon>
            <span>AI 写作搭子</span>
          </div>
          <div class="ai-head-actions">
            <el-button size="small" text @click="clearAll">清空</el-button>
            <el-button size="small" text :icon="Close" @click="toggle" />
          </div>
        </div>
        <div class="ai-scroll" ref="scrollRef">
          <div
            v-for="(m, i) in messages"
            :key="i"
            :class="['ai-bubble', m.role === 'user' ? 'ai-user' : 'ai-assistant']"
          >{{ m.content }}</div>
        </div>
        <div class="ai-input">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            resize="none"
            :placeholder="placeholder"
            @keydown="onKey"
          />
          <el-button
            class="ai-send"
            type="primary"
            :loading="sending"
            :disabled="!input.trim()"
            @click="send"
          >
            发送
          </el-button>
        </div>
      </div>
    </Transition>

    <button class="ai-fab" :class="{ 'ai-fab--open': open }" @click="toggle" title="AI 写搭">
      <el-icon v-if="!open"><Promotion /></el-icon>
      <el-icon v-else><Close /></el-icon>
    </button>
  </div>
</template>

<style scoped>
.ai-float-root {
  position: fixed;
  right: 22px;
  bottom: 22px;
  z-index: 9998;
}
.ai-fab {
  width: 54px;
  height: 54px;
  border-radius: 50%;
  border: none;
  background: linear-gradient(135deg, #409eff, #6b5bff);
  color: #fff;
  font-size: 22px;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(64,158,255,0.45);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform .2s, box-shadow .2s;
}
.ai-fab:hover { transform: translateY(-2px); box-shadow: 0 10px 24px rgba(64,158,255,0.5); }
.ai-fab--open { background: linear-gradient(135deg,#f56c6c,#e6a23c); }

.ai-panel {
  position: absolute;
  right: 0;
  bottom: 70px;
  width: 440px;
  max-width: calc(100vw - 40px);
  height: 580px;
  max-height: calc(100vh - 100px);
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 14px 40px rgba(0,0,0,0.18);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid #ebeef5;
}
.ai-head {
  padding: 10px 14px;
  border-bottom: 1px solid #f0f2f5;
  background: linear-gradient(90deg, #ecf5ff 0%, #f4ecff 100%);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.ai-head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #303133;
}
.ai-head-actions { display: flex; gap: 4px; }

.ai-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  background: #fafbfc;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ai-bubble {
  max-width: 85%;
  padding: 9px 12px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04);
}
.ai-user {
  align-self: flex-end;
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 3px;
}
.ai-assistant {
  align-self: flex-start;
  background: #fff;
  color: #303133;
  border: 1px solid #ebeef5;
  border-bottom-left-radius: 3px;
}

.ai-input {
  padding: 10px 12px;
  border-top: 1px solid #f0f2f5;
  background: #fff;
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.ai-input :deep(.el-textarea__inner) { font-size: 14px; line-height: 1.55; }
.ai-send { flex-shrink: 0; height: 40px; }

.ai-pop-enter-active, .ai-pop-leave-active { transition: all .2s ease; transform-origin: bottom right; }
.ai-pop-enter-from, .ai-pop-leave-to { opacity: 0; transform: scale(0.85) translateY(10px); }
</style>
