<script setup lang="ts">
import type { MetricsRun } from '#shared/types/api'

useHead({ title: 'Метрики' })

const api = useApi()
const { data, error, refresh } = await useAsyncData('metrics', () => api.metrics())

const notReady = computed(() => (error.value as { statusCode?: number } | null)?.statusCode === 404)

const service = computed(() => (data.value?.runs ?? []).filter((run) => run.kind === 'сервис'))
const search = computed(() => (data.value?.runs ?? []).filter((run) => run.kind === 'поиск'))

const percent = new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 1 })
const dateFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })

function share(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : percent.format(value)
}

function milliseconds(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value / 1000).toFixed(2)} с`
}

function when(value: string | undefined): string {
  return value ? dateFormat.format(new Date(value)) : '—'
}

/** Выборка: synth_v1 и synth_v2 — синтетика, public — реальные фото. */
function splitTitle(run: MetricsRun): string {
  if (run.split === 'public') return 'публичные фото'
  if (run.split === 'synth_v1') return 'синтетика v1'
  if (run.split === 'synth_v2') return 'синтетика v2 (повороты, тесные соседи)'
  return run.split ?? 'выборка не указана'
}
</script>

<template>
  <div class="metrics">
    <header>
      <h1>Метрики</h1>
      <p class="muted">
        Числа берутся из прогонов оценки, а не вписаны руками. Синтетические выборки собраны из эталонных фото
        каталога, поэтому они оптимистичнее реальной съёмки у полки.
      </p>
    </header>

    <p v-if="notReady" class="notice">
      Сводка прогонов ещё не собрана. Выполните <code>make report</code> — он запишет
      <code>artifacts/eval/summary.json</code>.
    </p>
    <div v-else-if="error" class="notice">
      <p>Не удалось загрузить метрики.</p>
      <button type="button" class="btn btn--ghost" @click="refresh()">Повторить</button>
    </div>

    <template v-else>
      <section class="metrics__section" aria-labelledby="service-title">
        <h2 id="service-title">Сквозные прогоны сервиса</h2>
        <p class="muted">Полный путь: выбор бутылки в кадре, поиск, проверка кандидатов и решение «не найдено».</p>
        <ul class="cards">
          <li v-for="run in service" :key="run.run" class="card">
            <p class="card__title">{{ splitTitle(run) }}</p>
            <p class="card__meta">{{ run.queries }} запросов · {{ when(run.finished_at) }}</p>
            <dl class="stats">
              <div class="stat">
                <dt>Верный ответ первым</dt>
                <dd>{{ share(run.top1) }}</dd>
              </div>
              <div class="stat">
                <dt>Верный в пятёрке</dt>
                <dd>{{ share(run.top5) }}</dd>
              </div>
              <div class="stat">
                <dt>Ответ верен, когда отвечаем</dt>
                <dd>{{ share(run.decision?.precision_of_answers) }}</dd>
              </div>
              <div class="stat">
                <dt>Отказались отвечать</dt>
                <dd>{{ share(run.decision?.rejected) }}</dd>
              </div>
              <div class="stat">
                <dt>Половина ответов быстрее</dt>
                <dd>{{ milliseconds(run.latency_p50_ms) }}</dd>
              </div>
              <div class="stat">
                <dt>95% ответов быстрее</dt>
                <dd>{{ milliseconds(run.latency_p95_ms) }}</dd>
              </div>
            </dl>
            <p v-if="run.decision?.out_of_catalog" class="card__note">
              Вин вне каталога: {{ run.decision.out_of_catalog }}, из них отклонено
              {{ share(run.decision.out_of_catalog_rejected) }}.
            </p>
            <p class="card__run">{{ run.run }}</p>
          </li>
        </ul>
      </section>

      <section v-if="search.length" class="metrics__section" aria-labelledby="search-title">
        <h2 id="search-title">Прогоны поиска</h2>
        <p class="muted">Только поиск по каталогу, без решения об отказе: верхняя граница для сквозного прогона.</p>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th scope="col">Прогон</th>
                <th scope="col">Запросов</th>
                <th scope="col">Первым</th>
                <th scope="col">В пятёрке</th>
                <th scope="col">Похожие этикетки</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="run in search" :key="run.run">
                <td class="table__run">{{ run.run }}</td>
                <td>{{ run.queries ?? '—' }}</td>
                <td>{{ share(run.top1) }}</td>
                <td>{{ share(run.top5) }}</td>
                <td>{{ share(run.top1_in_phash_group) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <p class="muted metrics__generated">Сводка собрана {{ when(data?.generated_at) }}.</p>
    </template>
  </div>
</template>

<style scoped>
.metrics__section {
  margin-top: 28px;
}

.metrics__section h2 {
  font-family: var(--font-serif);
  font-size: 22px;
}

.cards {
  display: grid;
  gap: 12px;
  margin-top: 12px;
  padding: 0;
  list-style: none;
}

.card {
  padding: 16px;
  border-radius: var(--radius-m);
  background: var(--c-surface);
  box-shadow: var(--shadow-soft);
}

.card__title {
  font-size: 17px;
  font-weight: 600;
}

.card__meta,
.card__run {
  margin-top: 4px;
  color: var(--c-text-muted);
  font-size: 13px;
}

.card__run {
  margin-top: 12px;
  overflow-wrap: anywhere;
}

.card__note {
  margin-top: 10px;
  color: var(--c-text-secondary);
  font-size: 14px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-top: 14px;
}

.stat dt {
  color: var(--c-text-secondary);
  font-size: 13px;
  line-height: 17px;
}

.stat dd {
  margin: 2px 0 0;
  font-size: 20px;
  font-weight: 600;
}

.table-wrap {
  margin-top: 12px;
  overflow-x: auto;
}

.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

.table th,
.table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--c-border-soft);
  text-align: right;
  white-space: nowrap;
}

.table th:first-child,
.table td:first-child {
  text-align: left;
}

.table__run {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.metrics__generated {
  margin-top: 24px;
  font-size: 13px;
}
</style>
