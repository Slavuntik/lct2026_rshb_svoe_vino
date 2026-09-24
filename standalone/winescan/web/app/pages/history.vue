<script setup lang="ts">
useHead({ title: 'История' })

const { items: scans, loaded: scansLoaded, clear: clearScans } = useScanHistory()
const { items: wishes, loaded: wishesLoaded, remove: removeWish, clear: clearWishes } = useWishlist()

const dateFormat = new Intl.DateTimeFormat('ru-RU', {
  day: 'numeric',
  month: 'long',
  hour: '2-digit',
  minute: '2-digit',
})

function formatDate(timestamp: number): string {
  return dateFormat.format(new Date(timestamp))
}

function confirmAndClearScans() {
  if (window.confirm('Очистить историю сканов?')) clearScans()
}

function confirmAndClearWishes() {
  if (window.confirm('Очистить список «Хочу попробовать»?')) clearWishes()
}
</script>

<template>
  <div class="history">
    <header>
      <h1>История</h1>
      <p class="muted">Хранится только в этом браузере и никуда не отправляется.</p>
    </header>

    <section class="history__section" aria-labelledby="wishes-title">
      <div class="history__head">
        <h2 id="wishes-title">Хочу попробовать</h2>
        <button v-if="wishes.length" type="button" class="btn btn--ghost" @click="confirmAndClearWishes">
          Очистить
        </button>
      </div>
      <p v-if="!wishesLoaded" class="muted">Загружаем…</p>
      <p v-else-if="!wishes.length" class="notice">
        Пока пусто. Нажмите «Хочу попробовать» на карточке вина, чтобы сохранить его здесь.
      </p>
      <ul v-else class="entries">
        <li v-for="wish in wishes" :key="wish.slug" class="entry">
          <NuxtLink :to="wineUrl(wish.slug)" class="entry__link">
            <span class="entry__media">
              <WineImage :slug="wish.slug" :alt="`Фото бутылки: ${wish.name}`" />
            </span>
            <span class="entry__text">
              <span class="entry__title">{{ wish.name }}</span>
              <span class="entry__meta">{{ wish.winery }}</span>
            </span>
          </NuxtLink>
          <button
            type="button"
            class="entry__remove"
            :aria-label="`Убрать «${wish.name}» из списка`"
            @click="removeWish(wish.slug)"
          >
            <AppIcon name="trash" :size="20" />
          </button>
        </li>
      </ul>
    </section>

    <section class="history__section" aria-labelledby="scans-title">
      <div class="history__head">
        <h2 id="scans-title">Сканы</h2>
        <button v-if="scans.length" type="button" class="btn btn--ghost" @click="confirmAndClearScans">
          Очистить
        </button>
      </div>
      <p v-if="!scansLoaded" class="muted">Загружаем…</p>
      <div v-else-if="!scans.length" class="notice">
        Сканов пока не было.
        <NuxtLink to="/" class="link">Сфотографировать этикетку</NuxtLink>
      </div>
      <ul v-else class="entries">
        <li v-for="scan in scans" :key="scan.id" class="entry">
          <NuxtLink v-if="scan.status === 'found' && scan.slug" :to="wineUrl(scan.slug)" class="entry__link">
            <span class="entry__media">
              <WineImage :slug="scan.slug" :alt="`Фото бутылки: ${scan.name ?? 'вино'}`" />
            </span>
            <span class="entry__text">
              <span class="entry__status entry__status--found">Найдено</span>
              <span class="entry__title">{{ scan.name ?? 'Вино из каталога' }}</span>
              <span class="entry__meta">{{ [scan.winery, formatDate(scan.at)].filter(Boolean).join(' · ') }}</span>
            </span>
          </NuxtLink>
          <div v-else class="entry__link">
            <span class="entry__media entry__media--empty">
              <AppIcon name="alert" :size="24" />
            </span>
            <span class="entry__text">
              <span class="entry__status">Нет в каталоге</span>
              <span class="entry__meta">{{ formatDate(scan.at) }}</span>
              <span v-if="scan.nearestSlug" class="entry__meta">
                Похоже на:
                <NuxtLink :to="wineUrl(scan.nearestSlug)" class="link">{{ scan.nearestName }}</NuxtLink>
              </span>
            </span>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.history__section {
  margin-top: 28px;
}

.history__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.entries {
  margin: 0;
  padding: 0;
  list-style: none;
}

.entry {
  display: flex;
  align-items: center;
  gap: 8px;
}

.entry + .entry {
  border-top: 1px solid var(--c-border-soft);
}

.entry__link {
  display: flex;
  flex: 1;
  align-items: center;
  gap: 12px;
  min-width: 0;
  padding: 10px 0;
  color: var(--c-text);
  text-decoration: none;
}

.entry__media {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 76px;
  padding: 6px;
  border-radius: var(--radius-s);
  background: var(--c-surface);
}

.entry__media--empty {
  color: var(--c-text-muted);
}

.entry__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.entry__status {
  color: var(--c-text-secondary);
  font-size: 12px;
  font-weight: 600;
  line-height: 16px;
}

.entry__status--found {
  color: var(--c-primary);
}

.entry__title {
  font-weight: 600;
  line-height: 1.35;
}

.entry__meta {
  color: var(--c-text-secondary);
  font-size: 13px;
  line-height: 18px;
}

.entry__remove {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--c-text-secondary);
  cursor: pointer;
}

.entry__remove:hover {
  background: var(--c-primary-soft);
  color: var(--c-primary);
}
</style>
