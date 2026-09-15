<script setup lang="ts">
import type { ScanResponse } from '#shared/types/api'
import { isMockEnabled } from '#shared/utils/mock'
import type { UploadInfo } from '~/utils/image'

useHead({ title: 'Распознать этикетку' })

type Phase = 'idle' | 'uploading' | 'error' | 'not_found'
type MockStatus = 'found' | 'not_found'

const api = useApi()
const mock = isMockEnabled(useRuntimeConfig().public.mock)
const history = useScanHistory()

const phase = ref<Phase>('idle')
const previewUrl = ref<string | null>(null)
const errorText = ref('')
const result = ref<ScanResponse | null>(null)
const lastFile = shallowRef<File | null>(null)
const uploadInfo = ref<UploadInfo | null>(null)
const mockStatus = ref<MockStatus>('found')

const busy = computed(() => phase.value === 'uploading')

function setPreview(file: File | null) {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = file ? URL.createObjectURL(file) : null
}

onBeforeUnmount(() => setPreview(null))

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = '' // чтобы повторный выбор того же файла снова вызвал change
  if (file) void runScan(file)
}

async function runScan(file: File) {
  lastFile.value = file
  setPreview(file)
  result.value = null
  errorText.value = ''
  if (file.type && !file.type.startsWith('image/')) {
    errorText.value = 'Нужна фотография этикетки — выберите файл изображения.'
    phase.value = 'error'
    return
  }
  phase.value = 'uploading'
  try {
    // отправляем исходное фото целиком (рамка на экране — только подсказка), большие кадры уменьшаются
    const upload = await prepareUpload(file)
    uploadInfo.value = upload.info
    const response = await api.scan(upload.blob, upload.filename, mock ? mockStatus.value : undefined)
    result.value = response

    const nearest = response.top5[0] ?? null
    const found = response.status === 'found' && Boolean(response.slug)
    history.add({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      at: Date.now(),
      status: found ? 'found' : 'not_found',
      slug: found ? response.slug : null,
      name: response.card?.name ?? null,
      winery: response.card?.winery ?? null,
      nearestSlug: nearest?.slug ?? null,
      nearestName: nearest?.name ?? null,
    })

    if (found && response.slug) {
      saveScanContext(response.slug, {
        at: Date.now(),
        confidence: response.confidence,
        top5: response.top5,
        timings_ms: response.timings_ms,
        box: response.box,
        upload: upload.info,
      })
      await navigateTo({ path: wineUrl(response.slug), query: { from: 'scan' } })
      return
    }
    phase.value = 'not_found'
    window.scrollTo({ top: 0 })
  } catch (error) {
    errorText.value = apiErrorMessage(error)
    phase.value = 'error'
  }
}

function retry() {
  if (lastFile.value) void runScan(lastFile.value)
}

function reset() {
  phase.value = 'idle'
  result.value = null
  errorText.value = ''
  lastFile.value = null
  uploadInfo.value = null
  setPreview(null)
}
</script>

<template>
  <div class="scanner">
    <!-- Экран «нет в каталоге» -->
    <section v-if="phase === 'not_found' && result" class="not-found" aria-labelledby="not-found-title">
      <div class="not-found__head">
        <img v-if="previewUrl" :src="previewUrl" alt="Ваше фото" class="not-found__photo">
        <div>
          <p class="eyebrow">Результат сканирования</p>
          <h1 id="not-found-title">Этого вина нет в каталоге</h1>
        </div>
      </div>

      <p class="not-found__text">
        Мы сравнили фото с эталонными фотографиями каталога «Своё вино» и не нашли достаточно уверенного совпадения.
      </p>
      <p class="muted">
        Так бывает, если вина пока нет в каталоге или этикетку плохо видно: блик, сильный наклон, тень, часть этикетки
        закрыта. Можно переснять при ровном свете.
      </p>

      <div class="not-found__actions">
        <NuxtLink to="/sommelier" class="btn btn--primary btn--large btn--block">
          <AppIcon name="utensils" />
          Подобрать похожее к блюду
        </NuxtLink>
        <button type="button" class="btn btn--secondary btn--large btn--block" @click="reset">
          <AppIcon name="camera" />
          Сфотографировать ещё раз
        </button>
      </div>

      <section v-if="result.top5.length" class="not-found__similar" aria-labelledby="similar-title">
        <h2 id="similar-title">Похожие этикетки в каталоге</h2>
        <p class="muted">Это не совпадение, а ближайшие по внешнему виду вина — возможно, среди них есть нужное.</p>
        <ul class="candidates">
          <li v-for="candidate in result.top5" :key="candidate.slug">
            <NuxtLink :to="wineUrl(candidate.slug)" class="candidate">
              <span class="candidate__media">
                <WineImage :slug="candidate.slug" :alt="`Фото бутылки: ${candidate.name}`" />
              </span>
              <span class="candidate__name">{{ candidate.name }}</span>
              <AppIcon name="chevron-right" :size="20" class="candidate__chevron" />
            </NuxtLink>
          </li>
        </ul>
      </section>

      <TechDetails
        :confidence="result.confidence"
        :top5="result.top5"
        :timings="result.timings_ms"
        :box="result.box"
        :upload="uploadInfo"
      />
    </section>

    <!-- Сканер -->
    <template v-else>
      <header class="scanner__intro">
        <h1>Узнайте вино по этикетке</h1>
        <p class="muted">
          Сфотографируйте этикетку российского вина — покажем карточку из каталога, аналоги других виноделен и
          подбор к блюду.
        </p>
      </header>

      <div class="viewfinder" :class="{ 'viewfinder--photo': previewUrl, 'viewfinder--busy': busy }">
        <img v-if="previewUrl" :src="previewUrl" alt="Выбранное фото" class="viewfinder__photo">
        <AppIcon v-else name="bottle" :size="160" class="viewfinder__bottle" />
        <div class="viewfinder__frame" aria-hidden="true">
          <span /><span /><span /><span />
        </div>
        <div v-if="busy" class="viewfinder__scanline" aria-hidden="true" />
        <p class="viewfinder__hint">{{ busy ? 'Распознаём этикетку…' : 'Этикетка по центру, без бликов' }}</p>
      </div>
      <p class="scanner__note muted">Рамка — только подсказка: на распознавание отправляется всё фото.</p>

      <div v-if="busy" class="scanner__status" role="status" aria-live="polite">
        <span class="spinner" aria-hidden="true" />
        Ищем совпадение среди эталонов каталога…
      </div>

      <div v-if="phase === 'error'" class="alert" role="alert">
        <p class="alert__title">Не получилось распознать</p>
        <p>{{ errorText }}</p>
        <div class="alert__actions">
          <button v-if="lastFile" type="button" class="btn btn--primary" @click="retry">
            <AppIcon name="refresh" :size="20" />
            Повторить
          </button>
          <button type="button" class="btn btn--ghost" @click="reset">Выбрать другое фото</button>
        </div>
      </div>

      <div class="scanner__actions">
        <label class="btn btn--primary btn--large btn--block" :aria-disabled="busy">
          <input
            type="file"
            accept="image/*"
            capture="environment"
            class="visually-hidden"
            :disabled="busy"
            @change="onFileChange"
          >
          <AppIcon name="camera" />
          Сфотографировать этикетку
        </label>
        <label class="btn btn--secondary btn--large btn--block" :aria-disabled="busy">
          <input type="file" accept="image/*" class="visually-hidden" :disabled="busy" @change="onFileChange">
          <AppIcon name="image" />
          Загрузить из галереи
        </label>
      </div>

      <fieldset v-if="mock" class="mock-switch">
        <legend class="mock-switch__legend">Демо без сервиса: какой ответ вернуть</legend>
        <label class="chip" :class="{ 'chip--selected': mockStatus === 'found' }">
          <input v-model="mockStatus" type="radio" name="mock-status" value="found" class="visually-hidden">
          вино найдено
        </label>
        <label class="chip" :class="{ 'chip--selected': mockStatus === 'not_found' }">
          <input v-model="mockStatus" type="radio" name="mock-status" value="not_found" class="visually-hidden">
          нет в каталоге
        </label>
      </fieldset>

      <section class="tips" aria-labelledby="tips-title">
        <h2 id="tips-title" class="tips__title">Как снять, чтобы узнать точнее</h2>
        <ol class="tips__list">
          <li>Этикетка целиком в кадре, бутылка стоит ровно.</li>
          <li>Без вспышки и бликов — поверните бутылку к свету.</li>
          <li>Одна бутылка в центре: соседние мешают распознаванию.</li>
        </ol>
      </section>
    </template>
  </div>
</template>

<style scoped>
.scanner__intro h1 {
  margin-bottom: 8px;
}

.viewfinder {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  width: 100%;
  height: min(56vh, 460px);
  min-height: 300px;
  margin-top: 20px;
  border-radius: var(--radius-l);
  background: var(--c-surface);
}

.viewfinder__photo {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.viewfinder--busy .viewfinder__photo {
  filter: brightness(0.85);
}

.viewfinder__bottle {
  color: var(--c-surface-accent);
  stroke-width: 1.2;
}

.viewfinder__frame {
  position: absolute;
  inset: 12% 18% 20%;
  pointer-events: none;
}

.viewfinder__frame span {
  position: absolute;
  width: 34px;
  height: 34px;
  border: 3px solid var(--c-primary);
}

.viewfinder--photo .viewfinder__frame span {
  border-color: #fff;
}

.viewfinder__frame span:nth-child(1) {
  top: 0;
  left: 0;
  border-right: 0;
  border-bottom: 0;
  border-top-left-radius: 14px;
}

.viewfinder__frame span:nth-child(2) {
  top: 0;
  right: 0;
  border-bottom: 0;
  border-left: 0;
  border-top-right-radius: 14px;
}

.viewfinder__frame span:nth-child(3) {
  bottom: 0;
  left: 0;
  border-top: 0;
  border-right: 0;
  border-bottom-left-radius: 14px;
}

.viewfinder__frame span:nth-child(4) {
  right: 0;
  bottom: 0;
  border-top: 0;
  border-left: 0;
  border-bottom-right-radius: 14px;
}

.viewfinder__scanline {
  position: absolute;
  top: 12%;
  right: 18%;
  left: 18%;
  height: 3px;
  border-radius: 3px;
  background: linear-gradient(90deg, transparent, #fff, transparent);
  box-shadow: 0 0 14px #fff;
  animation: scan 1.6s ease-in-out infinite alternate;
}

@keyframes scan {
  to {
    top: 80%;
  }
}

.viewfinder__hint {
  position: absolute;
  bottom: 14px;
  left: 50%;
  max-width: calc(100% - 32px);
  padding: 6px 14px;
  border-radius: var(--radius-pill);
  background: rgb(254 253 250 / 92%);
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
  text-align: center;
  transform: translateX(-50%);
  white-space: nowrap;
}

.scanner__note {
  margin-top: 8px;
  text-align: center;
}

.scanner__status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-top: 16px;
  font-weight: 600;
}

.scanner__actions {
  display: grid;
  gap: 12px;
  margin-top: 20px;
}

.mock-switch {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 20px 0 0;
  padding: 10px 14px 14px;
  border: 1px dashed var(--c-border);
  border-radius: var(--radius-m);
}

.mock-switch__legend {
  padding: 0 4px;
  color: var(--c-text-secondary);
  font-size: 13px;
}

.tips {
  margin-top: 28px;
  padding: 20px;
  border-radius: 24px;
  background: var(--c-surface);
}

.tips__title {
  font-size: 20px;
}

.tips__list {
  display: grid;
  gap: 6px;
  margin: 10px 0 0;
  padding-left: 20px;
  font-size: 15px;
  line-height: 22px;
}

/* «Нет в каталоге» */
.not-found__head {
  display: flex;
  align-items: center;
  gap: 16px;
}

.not-found__photo {
  flex: none;
  width: 72px;
  height: 96px;
  border-radius: var(--radius-s);
  object-fit: cover;
}

.not-found__text {
  margin: 16px 0 8px;
  font-size: 16px;
  line-height: 24px;
}

.not-found__actions {
  display: grid;
  gap: 12px;
  margin-top: 20px;
}

.not-found__similar {
  margin-top: 32px;
}

.not-found__similar h2 {
  margin-bottom: 6px;
}

.candidates {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

.candidates li + li {
  border-top: 1px solid var(--c-border-soft);
}

.candidate {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  color: var(--c-text);
  text-decoration: none;
}

.candidate__media {
  flex: none;
  width: 56px;
  height: 80px;
  padding: 6px;
  border-radius: var(--radius-s);
  background: var(--c-surface);
}

.candidate__name {
  flex: 1;
  font-weight: 600;
  line-height: 1.35;
}

.candidate__chevron {
  flex: none;
  color: var(--c-primary);
}
</style>
