/* 全局确认弹窗 (模块级单例, 零依赖 -- 主应用 / flow 两个独立 app 共用).

   confirmDialog(opts): Promise<boolean> 替代原生 confirm() 与散落的
   自制确认 UI: 所有危险操作 (删除 / 清空 / 不可恢复类) 统一走这里. */
import { reactive } from 'vue'

export interface ConfirmOptions {
  /** 弹窗标题 (缺省无标题行). */
  title?: string
  /** 正文 (单段文本). */
  content: string
  positiveText?: string
  negativeText?: string
  /** 危险操作样式 (确认钮红). */
  danger?: boolean
}

/* 队列语义: 同一时刻只显示一个; 叠加调用先驳回前一个 (resolve false). */
const state = reactive<{ open: boolean; req: ConfirmOptions | null }>({
  open: false, req: null,
})
let _resolve: ((v: boolean) => void) | null = null

/** 弹确认框, 返回用户是否确认. Esc / 点遮罩 / 叠加弹新框 = false. */
export function confirmDialog(req: ConfirmOptions): Promise<boolean> {
  _resolve?.(false)
  state.req = req
  state.open = true
  return new Promise<boolean>(resolve => { _resolve = resolve })
}

/** ConfirmDialog 组件内部提交答案用, 业务侧不要直接调. */
export function _confirmAnswer(v: boolean): void {
  state.open = false
  state.req = null
  const r = _resolve
  _resolve = null
  r?.(v)
}

export const confirmState = state
