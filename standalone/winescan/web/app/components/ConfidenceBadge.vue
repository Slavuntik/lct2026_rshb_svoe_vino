<script setup lang="ts">
import type { ScanCandidate, ScanConfidence } from '#shared/types/api'

const props = defineProps<{ confidence: ScanConfidence; alternative?: ScanCandidate | null }>()
const level = computed(() => confidenceLevel(props.confidence))
</script>

<template>
  <div class="confidence" :class="`confidence--${level}`" role="status">
    <span class="confidence__icon">
      <AppIcon :name="level === 'high' ? 'check' : 'alert'" :size="18" />
    </span>
    <div class="confidence__body">
      <p class="confidence__title">
        Распознано по фото · {{ level === 'high' ? 'высокая уверенность' : 'средняя уверенность' }}
      </p>
      <p v-if="level === 'medium'" class="confidence__hint">
        Похожие этикетки есть у других вин — сверьте название с бутылкой.
        <template v-if="alternative">
          Не то вино? Возможно,
          <NuxtLink :to="wineUrl(alternative.slug)" class="link">{{ alternative.name }}</NuxtLink>.
        </template>
      </p>
    </div>
  </div>
</template>

<style scoped>
.confidence {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 14px;
  border-radius: var(--radius-m);
}

.confidence--high {
  background: var(--c-primary-soft);
}

.confidence--medium {
  background: var(--c-surface-accent);
}

.confidence__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #fff;
  color: var(--c-primary);
}

.confidence--medium .confidence__icon {
  color: var(--c-brown);
}

.confidence__title {
  font-size: 14px;
  font-weight: 600;
  line-height: 24px;
}

.confidence__hint {
  font-size: 14px;
  line-height: 20px;
}
</style>
