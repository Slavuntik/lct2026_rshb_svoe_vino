<script setup lang="ts">
import { isMockEnabled } from '#shared/utils/mock'

const mock = isMockEnabled(useRuntimeConfig().public.mock)
</script>

<template>
  <div class="page">
    <a href="#content" class="skip-link">К содержимому</a>
    <header class="app-header">
      <div class="container app-header__inner">
        <NuxtLink to="/" class="app-header__brand">
          <AppIcon name="glass" :size="22" class="app-header__mark" />
          <span class="app-header__text">
            <span class="app-header__title">Сканер вин</span>
            <span class="app-header__caption">модуль каталога «Своё вино»</span>
          </span>
        </NuxtLink>
        <span
          v-if="mock"
          class="app-header__mock"
          title="Данные из фикстур web/mocks, сервис распознавания не вызывается"
        >демо-данные</span>
      </div>
    </header>

    <main id="content" class="app-main container">
      <slot />
    </main>

    <AppFooter />
    <BottomNav />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  min-height: 100vh;
  min-height: 100dvh;
  padding-bottom: calc(var(--nav-h) + var(--safe-bottom));
  background: var(--c-bg);
}

.app-header {
  position: sticky;
  top: 0;
  z-index: 20;
  border-bottom: 1px solid var(--c-border-soft);
  background: rgb(254 253 250 / 92%);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.app-header__inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: var(--header-h);
}

.app-header__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  color: var(--c-text);
  text-decoration: none;
}

.app-header__mark {
  flex: none;
  color: var(--c-primary);
}

.app-header__text {
  display: flex;
  flex-direction: column;
  line-height: 1.15;
}

.app-header__title {
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 600;
}

.app-header__caption {
  color: var(--c-text-secondary);
  font-size: 12px;
}

.app-header__mock {
  flex: none;
  padding: 4px 10px;
  border-radius: var(--radius-pill);
  background: var(--c-surface-accent);
  color: var(--c-brown);
  font-size: 12px;
  font-weight: 600;
}

.app-main {
  flex: 1 0 auto;
  padding-top: 20px;
  padding-bottom: 32px;
}

.skip-link {
  position: absolute;
  top: 8px;
  left: -9999px;
  z-index: 100;
  padding: 8px 12px;
  border-radius: 8px;
  background: #fff;
}

.skip-link:focus {
  left: 16px;
}
</style>
