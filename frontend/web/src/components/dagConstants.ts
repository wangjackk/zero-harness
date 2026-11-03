export const NODE_W    = 260
export const HEADER_H  = 52   // title + subtitle 两行
export const ROW_H     = 26   // 每个 port 行高
export const SEC_PAD_T = 5    // inputs section 顶部内边距
export const CHIP_H    = 27   // 状态胶囊高度

export const NODE_LABELS: Record<string, string> = {
  pending: 'pending', running: 'running', completed: 'done',
  failed: 'failed', skipped: 'skipped', retrying: 'retrying',
}
