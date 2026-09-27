<template>
  <NCard class="sd-card" :bordered="false" content-style="padding: 0;">
    <!-- 标题栏 -->
    <div class="sd-header">
      <span class="sd-title">请做出选择</span>
      <NTag v-if="countdown !== null" :type="countdown <= 10 ? 'error' : 'default'" size="small">
        {{ countdown }}s
      </NTag>
    </div>

    <!-- 问题文本 -->
    <p class="sd-question">{{ question }}</p>

    <!-- 选项列表 -->
    <NRadioGroup v-model:value="radioValue" class="sd-radio-group">
      <div class="sd-options">
        <NRadio
          v-for="opt in parsedOptions"
          :key="opt.value"
          :value="opt.value"
          class="sd-radio"
          @click="onRadioClick(opt.value)"
        >
          {{ opt.label }}
        </NRadio>
      </div>
    </NRadioGroup>

    <!-- 自由输入(allow_other):直接在选项下方,placeholder 即提示 -->
    <div v-if="allow_other" class="sd-freetext-row">
      <NInput
        ref="otherInputRef"
        v-model:value="otherText"
        size="medium"
        placeholder="其他..."
        :status="radioValue === '__other__' ? 'success' : undefined"
        class="sd-freetext"
        @focus="onFreeTextFocus"
        @keydown.enter.prevent="confirmOther"
      />
      <NButton
        v-if="radioValue === '__other__'"
        type="primary"
        size="medium"
        :disabled="!otherText.trim()"
        @click="confirmOther"
      >
        确认
      </NButton>
    </div>

    <!-- 底部操作 -->
    <div class="sd-footer">
      <NButton size="small" @click="$emit('cancel')">取消</NButton>
    </div>
  </NCard>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { NCard, NRadioGroup, NRadio, NInput, NButton, NTag } from 'naive-ui'

type RawOption = string | { value?: unknown; label?: unknown }

export interface SelectorDialogProps {
  question?: string
  options?: RawOption[]
  allow_other?: boolean
  timeout?: number | null
}

interface OptionItem {
  value: string
  label: string
}

const props = withDefaults(defineProps<SelectorDialogProps>(), {
  question: '',
  options: () => [],
  allow_other: false,
  timeout: null,
})

const emit = defineEmits<{
  select: [value: string]
  cancel: []
}>()

const radioValue = ref<string | null>(null)
const otherText = ref('')
const otherInputRef = ref<InstanceType<typeof NInput> | null>(null)

const parsedOptions = computed<OptionItem[]>(() => {
  return props.options.map(o => {
    if (typeof o === 'string') return { value: o, label: o }
    const obj = o as { value?: unknown; label?: unknown }
    return {
      value: String(obj.value ?? obj.label ?? ''),
      label: String(obj.label ?? obj.value ?? ''),
    }
  })
})

function onRadioClick(value: string): void {
  radioValue.value = value
  emit('select', value)
}

function onFreeTextFocus(): void {
  radioValue.value = '__other__'
}

function confirmOther(): void {
  const text = otherText.value.trim()
  if (!text) return
  emit('select', text)
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
.sd-card {
  width: 420px;
  max-width: 90vw;
  background: #1a1d2e;
  border-radius: 12px !important;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.7);
  overflow: hidden;
}

.sd-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 24px 12px;
  border-bottom: 1px solid #252840;
}

.sd-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: #94a3b8;
}

.sd-question {
  padding: 14px 24px 8px;
  font-size: 15px;
  color: #e2e8f0;
  line-height: 1.65;
  white-space: pre-wrap;
}

.sd-radio-group {
  width: 100%;
}

.sd-options {
  display: flex;
  flex-direction: column;
  padding: 4px 16px 12px;
}

.sd-radio {
  padding: 9px 8px;
  border-radius: 8px;
  transition: background 0.12s;
  align-items: center !important;
}
.sd-radio:hover {
  background: #252840;
}

/* 自由输入行 */
.sd-freetext-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px 12px;
}

.sd-freetext {
  flex: 1;
}

.sd-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 24px 18px;
  border-top: 1px solid #252840;
}
</style>
