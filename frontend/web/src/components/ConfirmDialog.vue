<script setup lang="ts">
/* 全局确认弹窗宿主: 根组件挂一个, confirmDialog() 驱动 (见 composables/confirm).
   零依赖自绘 (主应用 / flow 共用); Esc=取消, Enter=确认, 点遮罩=取消. */
import { onBeforeUnmount, onMounted } from 'vue'
import { _confirmAnswer, confirmState } from '../composables/confirm'

function onKey(e: KeyboardEvent): void {
  if (!confirmState.open) return
  if (e.key === 'Escape') _confirmAnswer(false)
  else if (e.key === 'Enter') _confirmAnswer(true)
}
onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div v-if="confirmState.open" class="cf-mask" @click.self="_confirmAnswer(false)">
      <div class="cf-card" role="alertdialog">
        <div v-if="confirmState.req?.title" class="cf-title">{{ confirmState.req.title }}</div>
        <div class="cf-body">{{ confirmState.req?.content }}</div>
        <div class="cf-actions">
          <button class="cf-btn" @click="_confirmAnswer(false)">
            {{ confirmState.req?.negativeText ?? '取消' }}
          </button>
          <button class="cf-btn cf-ok" :class="{ danger: confirmState.req?.danger }"
                  @click="_confirmAnswer(true)">
            {{ confirmState.req?.positiveText ?? '确认' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 色板用 var(--x, fallback): flow 吃自己的变量, 主应用吃 fallback (暗色协调). */
.cf-mask {
  position: fixed; inset: 0; z-index: 3000;
  display: flex; align-items: center; justify-content: center;
  background: rgba(0, 0, 0, .45);
}
.cf-card {
  min-width: 300px; max-width: 440px; padding: 16px 18px 14px;
  border-radius: var(--rad, 8px);
  background: var(--bg3, #1d212a);
  border: 1px solid var(--br, #262b35);
  box-shadow: 0 8px 32px rgba(0, 0, 0, .4);
}
.cf-title {
  font-size: 14px; font-weight: 600; margin-bottom: 8px;
  color: var(--fg, #d7dce4);
}
.cf-body {
  font-size: 13px; line-height: 1.55; white-space: pre-wrap;
  color: var(--fg, #d7dce4);
}
.cf-actions {
  display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px;
}
.cf-btn {
  font-size: 12px; padding: 5px 14px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--br, #262b35);
  background: var(--bg2, #16191f); color: var(--fg-dim, #7d8593);
}
.cf-btn:hover { color: var(--fg, #d7dce4); border-color: var(--acc, #5b9dd9); }
.cf-ok { color: var(--acc, #5b9dd9); border-color: var(--acc, #5b9dd9); }
.cf-ok:hover { background: rgba(91, 157, 217, .14); }
.cf-ok.danger { color: var(--err, #d96b6b); border-color: var(--err, #d96b6b); }
.cf-ok.danger:hover { background: rgba(217, 107, 107, .12); }
</style>
