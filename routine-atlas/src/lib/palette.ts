import { ref } from 'vue'

export const paletteOpen = ref(false)
export const openPalette = () => (paletteOpen.value = true)
export const closePalette = () => (paletteOpen.value = false)
