/** 起承转合的展示契约。
 *
 * 为什么单独抽一个文件：方向卡弹窗（选版时）与今日章台（已选定稿）都要渲染同一份四段，
 * 两处各写一份常量必然会漂移——顺序、配色、提示语其中一个改了另一个就静默错位。
 *
 * 字段顺序与后端 brief_routes.BEAT_KEYS 一致，改这里必须同步改那里。
 */
import type { PlotBeats } from './api'

export interface PlotBeatSpec {
  key: keyof PlotBeats
  label: string
  color: string
  bg: string
  hint: string
}

/** 固定顺序：起 → 承 → 转 → 合 */
export const PLOT_BEATS: readonly PlotBeatSpec[] = [
  { key: 'qi', label: '起', color: '#e6a23c', bg: 'rgba(230,162,60,0.10)', hint: '开场发生什么' },
  { key: 'cheng', label: '承', color: '#909399', bg: 'rgba(144,147,153,0.10)', hint: '如何推进加码' },
  { key: 'zhuan', label: '转', color: '#f56c6c', bg: 'rgba(245,108,108,0.10)', hint: '反转/规则显形' },
  { key: 'he', label: '合', color: '#67c23a', bg: 'rgba(103,194,58,0.10)', hint: '收尾与新钩子' },
]

/** 只返回有内容的段，避免渲染出半空白框。 */
export function visibleBeats(beats: PlotBeats | null | undefined): PlotBeatSpec[] {
  if (!beats) return []
  return PLOT_BEATS.filter((b) => (beats[b.key] || '').trim())
}