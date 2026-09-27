<template>
  <component :is="props.comp" class="ic" :style="{ width: s, height: s }" />
</template>

<script setup lang="ts">
/**
 * 统一图标封装: @vicons/tabler 线性 SVG (stroke 继承 currentColor),
 * 固定像素尺寸, 颜色随文字色走 (默认灰 / hover 亮 / 选中 indigo).
 */
import type { Component } from 'vue'

const props = withDefaults(
  defineProps<{ comp: Component; size?: number }>(),
  { size: 16 },
)

const s = `${props.size}px`
</script>

<style>
.ic { display: block; flex-shrink: 0; }
</style>
