<script setup lang="ts">
import type { ReasonedWine } from '#shared/types/api'

// rail — карточка в горизонтальной ленте (как «Похожие вина» портала), row — строка подборки сомелье
withDefaults(defineProps<{ item: ReasonedWine; variant?: 'rail' | 'row'; rank?: number | null }>(), {
  variant: 'rail',
  rank: null,
})
</script>

<template>
  <NuxtLink :to="wineUrl(item.slug)" class="tile" :class="`tile--${variant}`">
    <div class="tile__media">
      <span v-if="rank" class="tile__rank" aria-hidden="true">{{ rank }}</span>
      <WineImage :slug="item.slug" :alt="`Фото бутылки: ${item.name}`" />
    </div>
    <div class="tile__body">
      <h3 class="tile__title">{{ item.name }}</h3>
      <p class="tile__winery">{{ item.winery }}</p>
      <ul v-if="item.reasons.length" class="tile__reasons" aria-label="Почему в подборке">
        <li v-for="reason in item.reasons" :key="reason" class="tile__reason">
          <AppIcon name="check" :size="14" class="tile__reason-icon" />
          <span>{{ upperFirst(reason) }}</span>
        </li>
      </ul>
      <span v-if="variant === 'row'" class="tile__more">
        Карточка вина
        <AppIcon name="chevron-right" :size="16" />
      </span>
    </div>
  </NuxtLink>
</template>

<style scoped>
.tile {
  display: flex;
  color: var(--c-text);
  background: var(--c-surface);
  text-decoration: none;
  transition: transform 0.3s ease-out;
  -webkit-tap-highlight-color: transparent;
}

.tile:active {
  transform: scale(0.98);
}

.tile--rail {
  flex-direction: column;
  gap: 12px;
  height: 100%;
  padding: 16px;
  border-radius: 24px;
}

.tile--row {
  display: grid;
  grid-template-columns: 88px 1fr;
  gap: 16px;
  padding: 16px;
  border-radius: 24px;
}

.tile__media {
  position: relative;
}

.tile--rail .tile__media {
  height: 180px;
}

.tile--row .tile__media {
  height: 140px;
}

.tile__rank {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--c-surface-accent);
  color: var(--c-primary);
  font-size: 14px;
  font-weight: 700;
}

.tile__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.tile__title {
  display: -webkit-box;
  overflow: hidden;
  font-family: var(--font-sans);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.tile__winery {
  margin-top: 2px;
  color: var(--c-text-secondary);
  font-size: 14px;
  line-height: 1.3;
}

.tile__reasons {
  display: grid;
  gap: 4px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;
}

.tile__reason {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  font-size: 13px;
  line-height: 18px;
}

.tile__reason-icon {
  flex: none;
  margin-top: 2px;
  color: var(--c-primary);
}

.tile__more {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-top: auto;
  padding-top: 10px;
  color: var(--c-primary);
  font-size: 14px;
  font-weight: 600;
}
</style>
