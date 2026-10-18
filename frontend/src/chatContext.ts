import type { InjectionKey, Ref } from 'vue'
import { inject, provide, ref } from 'vue'

/**
 * 每个页面可以 provide 一个 ChatContextProvider：返回 { phase, system_hint, context } 快照。
 * AIChatFloat 会 inject 当前页面的 provider，发送消息时把快照带给后端。
 *
 * 约定返回结构：
 *   phase       : 阶段名（adapt/embody/outline/cast/rhythm/desk/export...）
 *   system_hint : 阶段专属提示（可选）
 *   context     : 页面当前展示/编辑对象的快照（JSON-serializable）
 *
 * 另外全局进度快照（progress）由 App/HomeView 维护，从 workbench state 计算。
 */
export interface ChatPageContext {
  phase: string
  system_hint?: string
  context?: Record<string, unknown>
}

export type ChatContextProvider = () => ChatPageContext | null

export const chatContextKey: InjectionKey<Ref<ChatContextProvider | null>> =
  Symbol('chat-context')

/** 在页面组件的 setup 中调用，提供当前页上下文采集函数 */
export function provideChatContext(provider: ChatContextProvider) {
  const ref_provider = ref<ChatContextProvider | null>(provider)
  provide(chatContextKey, ref_provider)
  return ref_provider
}

/** AIChatFloat 调用，拿到当前页面上下文（若无页面注册返回 null） */
export function useChatContext(): ChatContextProvider | null {
  const f = inject(chatContextKey, null)
  return f?.value ?? null
}
