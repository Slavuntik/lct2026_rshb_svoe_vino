<script setup lang="ts">
import type { ScanContext } from '~/utils/scanContext'

const route = useRoute()
const slug = computed(() => String(route.params.slug ?? ''))
const api = useApi()
const wishlist = useWishlist()

const { data: card, error, refresh } = await useAsyncData(() => `wine:${slug.value}`, () => api.wine(slug.value))

// аналоги грузятся на клиенте после карточки, чтобы она показывалась сразу
const {
  data: analogsData,
  status: analogsStatus,
  error: analogsError,
  refresh: refreshAnalogs,
} = useAsyncData(() => `analogs:${slug.value}`, () => api.analogs(slug.value, 6), { lazy: true, server: false })

const analogs = computed(() => analogsData.value?.analogs ?? [])
const analogsLoading = computed(() => analogsStatus.value === 'pending' || analogsStatus.value === 'idle')
const notFound = computed(() => error.value?.statusCode === 404)

// данные уверенности есть, только если на карточку перешли сразу после скана
const scan = ref<ScanContext | null>(null)
function loadScan() {
  scan.value = route.query.from === 'scan' ? loadScanContext(slug.value) : null
}
onMounted(loadScan)
watch(() => route.fullPath, loadScan)

const alternative = computed(() => scan.value?.top5.find((candidate) => candidate.slug !== slug.value) ?? null)
const isWished = computed(() => wishlist.has(slug.value))

function toggleWish() {
  if (card.value) wishlist.toggle(card.value)
}

useHead(() => ({ title: card.value ? `${card.value.name}, ${card.value.winery}` : 'Карточка вина' }))
</script>

<template>
  <div class="wine-page">
    <div v-if="error" class="alert" role="alert">
      <p class="alert__title">{{ notFound ? 'Вино не найдено' : 'Не удалось загрузить карточку' }}</p>
      <p>
        {{ notFound ? 'Такой карточки нет в каталоге. Попробуйте отсканировать этикетку ещё раз.' : apiErrorMessage(error) }}
      </p>
      <div class="alert__actions">
        <button v-if="!notFound" type="button" class="btn btn--primary" @click="refresh()">Повторить</button>
        <NuxtLink to="/" class="btn btn--secondary">К сканеру</NuxtLink>
      </div>
    </div>

    <article v-else-if="card" class="wine">
      <ConfidenceBadge v-if="scan" :confidence="scan.confidence" :alternative="alternative" class="wine__confidence" />

      <section class="wine-hero">
        <div class="wine-hero__visual">
          <WineImage :slug="card.slug" :alt="`Фото бутылки: ${card.name}`" eager />
        </div>
        <h1 class="wine-hero__title">{{ card.name }}</h1>
        <p class="wine-hero__winery">{{ card.winery }}</p>
      </section>

      <ul class="facts">
        <li v-if="card.region" class="fact">
          <span class="fact__icon"><AppIcon name="pin" :size="22" /></span>
          <span>
            <span class="fact__label">Регион</span>
            <span class="fact__value">{{ card.region }}</span>
          </span>
        </li>
        <li v-if="card.grapes?.length" class="fact">
          <span class="fact__icon"><AppIcon name="grape" :size="22" /></span>
          <span>
            <span class="fact__label">Сорт винограда</span>
            <span class="fact__value">{{ card.grapes.join(', ') }}</span>
          </span>
        </li>
        <li class="fact">
          <span class="fact__icon fact__icon--swatch" :style="{ background: categorySwatch(card.category) }" />
          <span>
            <span class="fact__label">Категория и цвет</span>
            <span class="fact__value">{{ categoryLine(card) }}</span>
            <span v-if="card.color" class="fact__value fact__value--sub">{{ upperFirst(card.color) }}</span>
          </span>
        </li>
        <li v-if="card.attributes.year" class="fact">
          <span class="fact__icon"><AppIcon name="calendar" :size="22" /></span>
          <span>
            <span class="fact__label">Год урожая</span>
            <span class="fact__value">{{ card.attributes.year }}</span>
          </span>
        </li>
        <li v-if="card.attributes.volume_l" class="fact">
          <span class="fact__icon"><AppIcon name="bottle" :size="22" /></span>
          <span>
            <span class="fact__label">Объём</span>
            <span class="fact__value">{{ formatVolume(card.attributes.volume_l) }}</span>
          </span>
        </li>
      </ul>

      <section v-if="card.description" class="wine-section">
        <h2 class="wine-section__title">Вкус и аромат</h2>
        <p class="wine__description">{{ card.description }}</p>
      </section>

      <div class="wine__actions">
        <button
          type="button"
          class="btn btn--large btn--block"
          :class="isWished ? 'btn--secondary' : 'btn--primary'"
          :aria-pressed="isWished"
          @click="toggleWish"
        >
          <AppIcon name="heart" :class="{ 'is-filled': isWished }" />
          {{ isWished ? 'В списке «Хочу попробовать»' : 'Хочу попробовать' }}
        </button>
        <NuxtLink to="/sommelier" class="btn btn--secondary btn--large btn--block">
          <AppIcon name="utensils" />
          Подобрать вино к блюду
        </NuxtLink>
      </div>

      <section class="wine-section" aria-labelledby="analogs-title">
        <h2 id="analogs-title" class="wine-section__title">Аналоги других виноделен</h2>
        <p class="muted">Совпадают цвет и тип вина; учитываются сорт, сладость, регион и описание вкуса.</p>

        <ul v-if="analogsLoading" class="rail" aria-busy="true" aria-label="Загружаем аналоги">
          <li v-for="n in 3" :key="n" class="rail__item">
            <div class="skeleton rail__skeleton" />
          </li>
        </ul>
        <div v-else-if="analogsError" class="notice">
          Не удалось загрузить аналоги.
          <button type="button" class="btn btn--ghost" @click="refreshAnalogs()">Повторить</button>
        </div>
        <ul v-else-if="analogs.length" class="rail">
          <li v-for="analog in analogs" :key="analog.slug" class="rail__item">
            <WineTile :item="analog" />
          </li>
        </ul>
        <p v-else class="notice">В каталоге нет вин других виноделен того же цвета и типа.</p>
      </section>

      <TechDetails
        v-if="scan"
        :confidence="scan.confidence"
        :top5="scan.top5"
        :timings="scan.timings_ms"
        :box="scan.box"
        :upload="scan.upload"
        :current-slug="card.slug"
      />
    </article>
  </div>
</template>

<style scoped>
.wine__confidence {
  margin-bottom: 16px;
}

.wine-hero__visual {
  height: 340px;
  margin-bottom: 16px;
  padding: 24px;
  border-radius: var(--radius-l);
  background: var(--c-surface);
}

.wine-hero__title {
  font-size: 30px;
}

.wine-hero__winery {
  margin-top: 4px;
  color: var(--c-primary);
  font-size: 16px;
  font-weight: 600;
}

.facts {
  display: grid;
  gap: 14px;
  margin: 20px 0 0;
  padding: 0;
  list-style: none;
}

.fact {
  display: flex;
  align-items: center;
  gap: 12px;
}

.fact__icon {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border-radius: 50%;
  background: var(--c-surface);
  color: var(--c-primary);
}

.fact__label {
  display: block;
  color: var(--c-text-secondary);
  font-size: 13px;
  line-height: 18px;
}

.fact__value {
  display: block;
  font-weight: 600;
  line-height: 20px;
}

.fact__value--sub {
  color: var(--c-text-secondary);
  font-weight: 400;
}

.wine-section {
  margin-top: 32px;
}

.wine-section__title {
  margin-bottom: 8px;
}

.wine__description {
  font-size: 16px;
  line-height: 1.6;
}

.wine__actions {
  display: grid;
  gap: 12px;
  margin-top: 28px;
}

.is-filled {
  fill: currentColor;
}

.rail {
  display: flex;
  gap: 12px;
  overflow-x: auto;
  margin: 16px calc(-1 * var(--gutter)) 0;
  padding: 0 var(--gutter) 8px;
  list-style: none;
  scroll-padding-inline: var(--gutter);
  scroll-snap-type: x mandatory;
  overscroll-behavior-x: contain;
  scrollbar-width: none;
}

.rail::-webkit-scrollbar {
  display: none;
}

.rail__item {
  flex: 0 0 min(64%, 220px);
  scroll-snap-align: start;
}

.rail__skeleton {
  height: 320px;
}
</style>
