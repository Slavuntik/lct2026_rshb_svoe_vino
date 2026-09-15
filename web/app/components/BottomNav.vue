<script setup lang="ts">
const route = useRoute()

const items = [
  { to: '/', label: 'Сканер', icon: 'camera', match: (path: string) => path === '/' || path.startsWith('/wine/') },
  { to: '/sommelier', label: 'Сомелье', icon: 'glass', match: (path: string) => path.startsWith('/sommelier') },
  { to: '/history', label: 'История', icon: 'clock', match: (path: string) => path.startsWith('/history') },
]
</script>

<template>
  <nav class="bottom-nav" aria-label="Основные разделы">
    <ul class="bottom-nav__list">
      <li v-for="item in items" :key="item.to" class="bottom-nav__item">
        <NuxtLink
          :to="item.to"
          class="bottom-nav__link"
          :class="{ 'bottom-nav__link--active': item.match(route.path) }"
        >
          <AppIcon :name="item.icon" :size="24" />
          <span>{{ item.label }}</span>
        </NuxtLink>
      </li>
    </ul>
  </nav>
</template>

<style scoped>
.bottom-nav {
  position: fixed;
  inset: auto 0 0;
  z-index: 30;
  padding-bottom: var(--safe-bottom);
  border-top: 1px solid var(--c-border-soft);
  background: var(--c-bg);
  box-shadow: 0 -4px 16px #2c2a280a;
}

.bottom-nav__list {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  max-width: var(--content-max);
  height: var(--nav-h);
  margin: 0 auto;
  padding: 0;
  list-style: none;
}

.bottom-nav__link {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  height: 100%;
  color: var(--c-text-secondary);
  font-size: 12px;
  font-weight: 600;
  line-height: 16px;
  text-decoration: none;
  -webkit-tap-highlight-color: transparent;
  transition: color 0.15s ease-in-out;
}

.bottom-nav__link:hover {
  color: var(--c-primary-hover);
}

.bottom-nav__link--active {
  color: var(--c-primary);
}
</style>
