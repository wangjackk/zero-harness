<template>
  <div class="prompt-panel">
    <div class="pp-header">
      <span class="pp-title">Prompt</span>
      <NTag size="small" :bordered="false" style="font-family: monospace; color: #818cf8; background: #1e2235;">
        epoch {{ epoch }}
      </NTag>
      <NButton size="tiny" quaternary @click="toggleMode">{{ mode }}</NButton>
    </div>

    <NScrollbar ref="scrollbarRef" class="pp-body">
      <template v-for="(msg, i) in messages" :key="i">
        <div class="msg-header" :style="{ color: roleColor(msg.role) }">
          ▌ {{ msg.role }}
        </div>
        <div
          v-if="mode === 'md' && msg.role === 'system'"
          class="msg-content md"
          v-html="renderMarkdown(msg.content)"
        />
        <div v-else class="msg-content text">{{ msg.content }}</div>
      </template>
      <div v-if="!messages.length" class="empty">等待 sys_prompt...</div>
    </NScrollbar>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { NScrollbar, NButton, NTag } from 'naive-ui'
import { renderMarkdown } from '../utils/markdown'

interface PromptMessage {
  role: string
  content: string
}

const props = defineProps<{
  epoch: number
  messages: PromptMessage[]
}>()

const mode = ref<'md' | 'text'>('md')
const scrollbarRef = ref<InstanceType<typeof NScrollbar> | null>(null)

const ROLE_COLORS: Record<string, string> = {
  system: '#6a9fb5',
  user: '#90a959',
  assistant: '#d0a070',
}

function roleColor(role: string): string {
  return ROLE_COLORS[role] ?? '#aaa'
}

function toggleMode(): void {
  mode.value = mode.value === 'md' ? 'text' : 'md'
}

watch(() => props.messages, async () => {
  await nextTick()
  const el = scrollbarRef.value?.scrollbarInstRef?.containerRef
  if (el) el.scrollTop = 0
})
</script>

<style scoped>
.prompt-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-left: 1px solid #2d3148;
  background: #13151f;
  min-width: 0;
}

.pp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid #2d3148;
  flex-shrink: 0;
}

.pp-title {
  font-size: 11px;
  font-weight: 700;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  flex: 1;
}

.pp-body {
  flex: 1;
  padding: 8px 12px;
  font-size: 12px;
}

.msg-header {
  font-weight: 700;
  font-size: 11px;
  margin-top: 12px;
  padding-top: 8px;
  border-top: 1px solid #1e2235;
}
.msg-header:first-child { border-top: none; margin-top: 0; }

.msg-content.text {
  white-space: pre-wrap;
  word-break: break-word;
  color: #94a3b8;
  font-family: 'Consolas', monospace;
  line-height: 1.5;
  margin: 4px 0 8px;
}

.msg-content.md {
  color: #cbd5e1;
  line-height: 1.6;
  margin: 4px 0 8px;
}

.msg-content.md :deep(h1),
.msg-content.md :deep(h2),
.msg-content.md :deep(h3) {
  color: #e2e8f0;
  margin: 8px 0 4px;
  font-size: 13px;
}
.msg-content.md :deep(code) {
  background: #1e2235;
  padding: 1px 4px;
  border-radius: 3px;
  font-family: 'Consolas', monospace;
  font-size: 11px;
  color: #a5b4fc;
}
.msg-content.md :deep(pre) {
  background: #1e2235;
  padding: 8px;
  border-radius: 4px;
  overflow-x: auto;
}
.msg-content.md :deep(pre code) { background: none; padding: 0; }
.msg-content.md :deep(ul),
.msg-content.md :deep(ol) { padding-left: 16px; }
.msg-content.md :deep(blockquote) {
  border-left: 3px solid #3d4270;
  margin: 4px 0;
  padding-left: 8px;
  color: #6b7280;
}

.empty {
  color: #4a5568;
  font-size: 12px;
  margin-top: 20px;
  text-align: center;
}
</style>
