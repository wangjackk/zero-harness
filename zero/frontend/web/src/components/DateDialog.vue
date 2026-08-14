<template>
  <NCard class="dd-card" :bordered="false" content-style="padding: 0;">
    <!-- 标题栏 -->
    <div class="dd-header">
      <span class="dd-title">请选择日期</span>
      <NTag v-if="countdown !== null" :type="countdown <= 10 ? 'error' : 'default'" size="small">
        {{ countdown }}s
      </NTag>
    </div>

    <!-- 问题文本 -->
    <p class="dd-question">{{ question }}</p>

    <!-- 日期选择器 -->
    <div class="dd-picker-row">
      <NDatePicker
        v-model:value="tsValue"
        type="date"
        :is-date-disabled="isDateDisabled"
        :default-value="defaultTs"
        size="medium"
        class="dd-picker"
      />
    </div>

    <!-- 底部操作 -->
    <div class="dd-footer">
      <NButton size="small" @click="$emit('cancel')">取消</NButton>
      <NButton
        type="primary"
        size="small"
        :disabled="tsValue === null"
        @click="confirm"
      >
        确认
      </NButton>
    </div>
  </NCard>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { NCard, NDatePicker, NButton, NTag } from 'naive-ui'

export interface DateDialogProps {
  question?: string
  min?: string | null
  max?: string | null
  default?: string | null
  timeout?: number | null
}

const props = withDefaults(defineProps<DateDialogProps>(), {
  question: '',
  min: null,
  max: null,
  default: null,
  timeout: null,
})

const emit = defineEmits<{
  select: [value: string]
  cancel: []
}>()

// --- 日期约束(ISO YYYY-MM-DD -> 时间戳)---
function isoToTs(iso: string | null): number | null {
  if (!iso) return null
  const t = Date.parse(iso.length <= 10 ? iso + 'T00:00:00' : iso)
  return Number.isNaN(t) ? null : t
}

const minTs = computed(() => isoToTs(props.min))
const maxTs = computed(() => isoToTs(props.max))
const defaultTs = computed(() => isoToTs(props.default))

// 选中值:默认初始化成 default(若有),否则不限
const tsValue = ref<number | null>(defaultTs.value)

function isDateDisabled(ts: number): boolean {
  if (minTs.value !== null && ts < minTs.value) return true
  if (maxTs.value !== null && ts > maxTs.value) return true
  return false
}

// 时间戳 -> YYYY-MM-DD
function tsToIso(ts: number): string {
  const d = new Date(ts)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function confirm(): void {
  if (tsValue.value === null) return
  emit('select', tsToIso(tsValue.value))
}

// 倒计时
const countdown = ref<number | null>(
  props.timeout != null ? Math.ceil(props.timeout) : null,
)
let timer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  if (countdown.value == null) return
  timer = setInterval(() => {
    if ((countdown.value as number) <= 1) {
      clearInterval(timer!)
      countdown.value = 0
      emit('cancel')
    } else {
      (countdown.value as number)--
    }
  }, 1000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.dd-card {
  width: 420px;
  max-width: 90vw;
  background: #1a1d2e;
  border-radius: 12px !important;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.7);
  overflow: hidden;
}

.dd-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 24px 12px;
  border-bottom: 1px solid #252840;
}

.dd-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: #94a3b8;
}

.dd-question {
  padding: 14px 24px 8px;
  font-size: 15px;
  color: #e2e8f0;
  line-height: 1.65;
  white-space: pre-wrap;
}

.dd-picker-row {
  padding: 4px 24px 16px;
}

.dd-picker {
  width: 100%;
}

.dd-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 24px 18px;
  border-top: 1px solid #252840;
}
</style>
