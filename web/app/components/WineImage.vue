<script setup lang="ts">
const props = withDefaults(defineProps<{ slug: string; alt: string; eager?: boolean }>(), { eager: false })

const failed = ref(false)
const img = ref<HTMLImageElement | null>(null)

watch(
  () => props.slug,
  () => {
    failed.value = false
  },
)

// ошибка загрузки могла случиться до гидратации — тогда @error уже не сработает
onMounted(() => {
  const element = img.value
  if (element && element.complete && element.naturalWidth === 0) failed.value = true
})
</script>

<template>
  <div class="wine-image">
    <img
      v-if="!failed"
      ref="img"
      :src="wineImageUrl(slug)"
      :alt="alt"
      :loading="eager ? 'eager' : 'lazy'"
      decoding="async"
      class="wine-image__img"
      @error="failed = true"
    >
    <svg v-else class="wine-image__placeholder" viewBox="0 0 120 360" role="img" :aria-label="alt">
      <path
        fill="currentColor"
        d="M48 12h24v70c0 18 24 30 24 60v196a12 12 0 0 1-12 12H36a12 12 0 0 1-12-12V142c0-30 24-42 24-60z"
      />
    </svg>
  </div>
</template>

<style scoped>
.wine-image {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

.wine-image__img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  /* белый фон эталонных фото растворяется в кремовой подложке, как на портале */
  mix-blend-mode: multiply;
}

.wine-image__placeholder {
  height: 80%;
  color: #efe3c4;
}
</style>
