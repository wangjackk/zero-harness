import { ref } from 'vue'
import type { TableDialogProps } from '../components/TableDialog.vue'
import type { SelectorDialogProps } from '../components/SelectorDialog.vue'
import type { DateDialogProps } from '../components/DateDialog.vue'

// Discriminated union keyed by `component`: each component carries exactly the
// props its dialog expects. This lets `uiQueue[0]` narrow to the right props
// type inside the matching `v-if component === ...` branch, so v-bind stays
// type-checked instead of leaking `Record<string, unknown>` (which broke
// required-prop checks for TableDialog's columns/rows at build time).
export type UiRequest =
  | { id: string; component: 'selector'; props: SelectorDialogProps }
  | { id: string; component: 'table'; props: TableDialogProps }
  | { id: string; component: 'date'; props: DateDialogProps }
  | { id: string; component: string; props: Record<string, unknown> }

/**
 * Manage the ui_request queue pushed by the backend.
 * The frontend is routine-agnostic: it renders whichever component `component`
 * names.
 *
 * Protocol:
 *   server -> client: { type: 'ui_request', id, component, props }
 *   client -> server: { type: 'ui_response', id, value? | error? }
 */
export function useUIRequests(send: (data: Record<string, unknown>) => void) {
  const queue = ref<UiRequest[]>([])

  function receive(msg: Record<string, unknown>): void {
    queue.value.push({
      id: msg.id as string,
      component: msg.component as string,
      props: (msg.props as Record<string, unknown>) ?? {},
    })
  }

  function respond(id: string, value: unknown): void {
    queue.value = queue.value.filter(r => r.id !== id)
    send({ type: 'ui_response', id, value })
  }

  function cancel(id: string, reason = 'cancelled'): void {
    queue.value = queue.value.filter(r => r.id !== id)
    send({ type: 'ui_response', id, error: reason })
  }

  // Backend-initiated cancel (timeout): just drop from the queue, no response.
  function serverCancel(msg: Record<string, unknown>): void {
    queue.value = queue.value.filter(r => r.id !== msg.id)
  }

  return { queue, receive, respond, cancel, serverCancel }
}
