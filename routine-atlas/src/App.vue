<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import AppHeader from './components/AppHeader.vue'
import AppFooter from './components/AppFooter.vue'
import CommandPalette from './components/CommandPalette.vue'
import { openPalette, paletteOpen, closePalette } from './lib/palette'

const route = useRoute()

function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    paletteOpen.value ? closePalette() : openPalette()
  }
}

onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="shell">
    <div class="aurora" aria-hidden="true"><i /><i /><i /></div>
    <div class="bg-field" aria-hidden="true" />
    <div class="bg-grain" aria-hidden="true" />

    <AppHeader />

    <main class="main">
      <RouterView v-slot="{ Component }">
        <Transition name="fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>

    <AppFooter v-if="route.name !== 'topology' && route.name !== 'engineering'" />
    <CommandPalette />
  </div>
</template>

<style scoped>
.shell { position: relative; min-height: 100vh; isolation: isolate; }
.main { position: relative; z-index: 1; padding-top: var(--nav-h); min-height: 60vh; }
</style>
