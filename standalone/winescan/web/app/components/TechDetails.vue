<script setup lang="ts">
import type { BoundingBox, ScanCandidate, ScanConfidence, ScanTimings } from '#shared/types/api'
import type { UploadInfo } from '~/utils/image'

// Раскрывающийся блок для демонстрации жюри: скоры кандидатов, отрыв, тайминги этапов.
const props = defineProps<{
  confidence: ScanConfidence
  top5: ScanCandidate[]
  timings: ScanTimings
  box: BoundingBox | null
  upload?: UploadInfo | null
  currentSlug?: string | null
}>()

const TIMING_LABELS: Record<string, string> = {
  detect: 'Детекция бутылки',
  embed_search: 'Эмбеддинг и поиск',
  local_match: 'Локальное сопоставление',
  ocr: 'Распознавание текста',
  total: 'Всего на сервере',
}

const timingRows = computed(() =>
  Object.entries(props.timings).map(([key, ms]) => ({ key, label: TIMING_LABELS[key] ?? key, ms })),
)

const uploadLabel = computed(() => {
  const upload = props.upload
  if (!upload) return null
  const size = `${Math.max(1, Math.round(upload.bytes / 1024)).toLocaleString('ru-RU')} КБ`
  const dims = upload.width && upload.height ? `${upload.width}×${upload.height}, ` : ''
  return `${dims}${size}${upload.resized ? ' (уменьшено в браузере)' : ''}`
})
</script>

<template>
  <details class="tech">
    <summary class="tech__summary">
      <span>Технические детали</span>
      <span class="tech__summary-hint">скоры и время обработки</span>
    </summary>
    <div class="tech__body">
      <dl class="tech__params">
        <div>
          <dt>Итоговый скор top-1</dt>
          <dd>{{ formatScore(confidence.score_top1) }}</dd>
        </div>
        <div>
          <dt>Визуальный скор top-1</dt>
          <dd>{{ formatScore(confidence.visual_score_top1) }}</dd>
        </div>
        <div>
          <dt>Отрыв top-1 от top-2</dt>
          <dd>{{ formatScore(confidence.margin_top1_top2) }}</dd>
        </div>
        <div v-if="confidence.decision_reason">
          <dt>Причина решения</dt>
          <dd>{{ confidence.decision_reason }}</dd>
        </div>
        <div v-if="box">
          <dt>Рамка бутылки, px</dt>
          <dd>{{ box.map((v) => Math.round(v)).join(', ') }}</dd>
        </div>
        <div v-if="uploadLabel">
          <dt>Отправлено фото</dt>
          <dd>{{ uploadLabel }}</dd>
        </div>
      </dl>

      <h3 class="tech__heading">Кандидаты top-5</h3>
      <div class="tech__table-wrap" tabindex="0" role="region" aria-label="Таблица кандидатов top-5">
        <table class="tech__table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Вино</th>
              <th scope="col">Итог</th>
              <th scope="col">Визуал</th>
              <th scope="col">Точки</th>
              <th scope="col">Текст</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(candidate, index) in top5" :key="candidate.slug" :class="{ 'is-current': candidate.slug === currentSlug }">
              <td>{{ index + 1 }}</td>
              <td class="tech__name">
                <NuxtLink :to="wineUrl(candidate.slug)" class="link">{{ candidate.name }}</NuxtLink>
              </td>
              <td>{{ formatScore(candidate.score) }}</td>
              <td>{{ formatScore(candidate.visual_score) }}</td>
              <td>{{ candidate.local_inliers ?? '—' }}</td>
              <td>{{ formatScore(candidate.text_score, 2) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted tech__legend">
        «Итог» — объединённый скор, «Визуал» — сходство эмбеддингов, «Точки» — число совпавших локальных
        особенностей, «Текст» — совпадение распознанного текста с названием.
      </p>

      <h3 class="tech__heading">Время обработки</h3>
      <ul class="tech__timings">
        <li v-for="row in timingRows" :key="row.key" :class="{ 'is-total': row.key === 'total' }">
          <span>{{ row.label }}</span>
          <span>{{ formatMs(row.ms) }}</span>
        </li>
      </ul>

      <template v-if="confidence.ocr_text">
        <h3 class="tech__heading">Текст, распознанный на этикетке</h3>
        <p class="tech__ocr">{{ confidence.ocr_text }}</p>
      </template>
    </div>
  </details>
</template>

<style scoped>
.tech {
  margin-top: 32px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-m);
}

.tech__summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 8px;
  padding: 14px 16px;
  font-weight: 600;
  cursor: pointer;
  list-style-position: inside;
}

.tech__summary-hint {
  color: var(--c-text-secondary);
  font-size: 13px;
  font-weight: 400;
}

.tech__body {
  padding: 0 16px 16px;
}

.tech__params {
  display: grid;
  gap: 8px;
  margin: 0;
}

.tech__params div {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 14px;
  line-height: 20px;
}

.tech__params dt {
  color: var(--c-text-secondary);
}

.tech__params dd {
  margin: 0;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.tech__heading {
  margin: 20px 0 8px;
  font-family: var(--font-sans);
  font-size: 15px;
  font-weight: 600;
}

.tech__table-wrap {
  overflow-x: auto;
  margin-inline: -16px;
  padding-inline: 16px;
}

.tech__table {
  width: 100%;
  min-width: 460px;
  border-collapse: collapse;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.tech__table th,
.tech__table td {
  padding: 6px 6px;
  border-bottom: 1px solid var(--c-border-soft);
  text-align: right;
  white-space: nowrap;
}

.tech__table th {
  color: var(--c-text-secondary);
  font-weight: 600;
}

.tech__table th:nth-child(2),
.tech__table td.tech__name {
  text-align: left;
  white-space: normal;
}

.tech__table tr.is-current td {
  background: var(--c-primary-soft);
}

.tech__legend {
  margin-top: 8px;
  font-size: 12px;
  line-height: 16px;
}

.tech__timings {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 14px;
}

.tech__timings li {
  display: flex;
  justify-content: space-between;
  font-variant-numeric: tabular-nums;
}

.tech__timings li.is-total {
  padding-top: 4px;
  border-top: 1px solid var(--c-border-soft);
  font-weight: 600;
}

.tech__ocr {
  padding: 10px 12px;
  border-radius: var(--radius-s);
  background: var(--c-surface);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 18px;
  overflow-wrap: anywhere;
}
</style>
