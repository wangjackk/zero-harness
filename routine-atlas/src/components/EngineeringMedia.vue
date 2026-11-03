<script setup lang="ts">
withDefaults(defineProps<{
  kind: 'video' | 'image'
  title: string
  note: string
  asset: string
  src?: string
  poster?: string
  alt?: string
  href?: string
}>(), {
  src: '',
  poster: '',
  alt: '',
  href: ''
})
</script>

<template>
  <figure class="engineering-media">
    <video
      v-if="kind === 'video'"
      class="media-asset"
      :src="src"
      :poster="poster || undefined"
      controls
      playsinline
      preload="none"
      :aria-label="title"
    />
    <template v-else>
      <a
        v-if="href"
        class="media-link"
        :href="href"
        target="_blank"
        rel="noopener"
        :aria-label="`${title}：查看完整截图`"
      >
        <img class="media-asset" :src="src" :alt="alt || title" loading="lazy" />
        <span class="media-zoom">查看完整截图 ↗</span>
      </a>
      <img
        v-else
        class="media-asset"
        :src="src"
        :alt="alt || title"
        loading="lazy"
      />
    </template>
    <figcaption v-if="src">
      <strong>{{ title }}</strong>
      <span>{{ note }}</span>
    </figcaption>
  </figure>
</template>

<style scoped>
.engineering-media { min-width: 0; margin: 0; color: #1d2735; }
.media-asset { display: block; width: 100%; height: auto; border: 1px solid #dfe4eb; border-radius: 7px; background: #eef1f5; }
.media-link { position: relative; display: block; border-radius: 7px; }
.media-link .media-asset { transition: border-color .2s ease, box-shadow .2s ease; }
.media-link:hover .media-asset { border-color: #aab5c5; box-shadow: 0 8px 24px rgba(30, 42, 58, .09); }
.media-zoom { position: absolute; right: 12px; bottom: 12px; padding: 5px 8px; border: 1px solid rgba(255,255,255,.16); border-radius: 4px; background: rgba(12,16,24,.78); color: #eef2f8; font-size: 10px; line-height: 1.4; backdrop-filter: blur(6px); }
figcaption { display: grid; gap: 5px; margin-top: 12px; }
figcaption strong { color: #354154; font-size: 12px; font-weight: 650; }
figcaption span { color: #748092; font-size: 11.5px; line-height: 1.75; }
</style>
