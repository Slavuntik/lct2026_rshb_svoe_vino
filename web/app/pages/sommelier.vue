<script setup lang="ts">
import type { QuestionId, ReasonedWine, SommelierQuestion, SuggestRequest } from '#shared/types/api'

useHead({ title: 'Цифровой сомелье' })

type Phase = 'questions' | 'loading' | 'results' | 'error'
const KNOWN_QUESTIONS: QuestionId[] = ['dish', 'category', 'sweetness', 'body']

const api = useApi()
const route = useRoute()

const {
  data: questionsData,
  error: questionsError,
  refresh: reloadQuestions,
} = await useAsyncData('sommelier:questions', () => api.questions())

const questions = computed<SommelierQuestion[]>(() =>
  (questionsData.value?.questions ?? []).filter((question) => KNOWN_QUESTIONS.includes(question.id)),
)

const step = ref(0)
// null — «не важно», undefined — ещё не отвечали
const answers = ref<Partial<Record<QuestionId, string | null>>>({})
const phase = ref<Phase>('questions')
const suggestions = ref<ReasonedWine[]>([])
const disclaimer = ref('')
const shownSlugs = ref<string[]>([])
const exhausted = ref(false)
const errorText = ref('')
const lastRequestFresh = ref(true)
const questionHeading = ref<HTMLElement | null>(null)

const current = computed(() => questions.value[step.value] ?? null)

// блюдо можно передать ссылкой: /sommelier?dish=fish
const presetDish = typeof route.query.dish === 'string' ? route.query.dish : ''
if (presetDish && questions.value.find((q) => q.id === 'dish')?.options.some((o) => o.id === presetDish)) {
  answers.value = { dish: presetDish }
  step.value = 1
}

function optionTitle(id: QuestionId, value: string | null | undefined): string | null {
  if (!value) return null
  const title = questions.value.find((q) => q.id === id)?.options.find((o) => o.id === value)?.title
  return upperFirst(title ?? value)
}

const summary = computed(() =>
  questions.value
    .map((question) => ({ id: question.id, value: optionTitle(question.id, answers.value[question.id]) }))
    .filter((item): item is { id: QuestionId; value: string } => Boolean(item.value)),
)

watch(step, async () => {
  await nextTick()
  questionHeading.value?.focus()
})

function choose(value: string | null) {
  const question = current.value
  if (!question) return
  answers.value = { ...answers.value, [question.id]: value }
  if (step.value < questions.value.length - 1) {
    step.value += 1
  } else {
    void fetchSuggestions(true)
  }
}

function goBack() {
  if (step.value > 0) step.value -= 1
}

function editAnswers() {
  phase.value = 'questions'
  step.value = 0
}

async function fetchSuggestions(fresh: boolean) {
  lastRequestFresh.value = fresh
  if (fresh) {
    shownSlugs.value = []
    exhausted.value = false
  }
  phase.value = 'loading'
  errorText.value = ''

  const request: SuggestRequest = { exclude_slugs: [...shownSlugs.value] }
  for (const id of KNOWN_QUESTIONS) {
    const value = answers.value[id]
    if (value) request[id] = value
  }

  try {
    const response = await api.suggest(request)
    disclaimer.value = response.disclaimer
    if (!fresh && response.suggestions.length === 0) {
      exhausted.value = true // новых вариантов нет — оставляем прежнюю подборку
    } else {
      suggestions.value = response.suggestions
      shownSlugs.value.push(...response.suggestions.map((s) => s.slug))
    }
    phase.value = 'results'
    window.scrollTo({ top: 0, behavior: 'smooth' })
  } catch (error) {
    errorText.value = apiErrorMessage(error)
    phase.value = 'error'
  }
}
</script>

<template>
  <div class="sommelier">
    <header class="sommelier__intro">
      <p class="eyebrow">По правилам сочетаний вина и блюд</p>
      <h1>Цифровой сомелье</h1>
      <p class="muted">Ответьте на несколько вопросов — подберём вина из каталога и объясним выбор.</p>
    </header>

    <div v-if="questionsError" class="alert" role="alert">
      <p class="alert__title">Не удалось загрузить вопросы</p>
      <p>{{ apiErrorMessage(questionsError) }}</p>
      <div class="alert__actions">
        <button type="button" class="btn btn--primary" @click="reloadQuestions()">Повторить</button>
      </div>
    </div>

    <section v-else-if="phase === 'questions' && current" class="quiz">
      <div class="quiz__progress">
        <span class="muted">Вопрос {{ step + 1 }} из {{ questions.length }}</span>
        <div
          class="progress"
          role="progressbar"
          aria-label="Прогресс вопросов"
          :aria-valuenow="step + 1"
          aria-valuemin="1"
          :aria-valuemax="questions.length"
        >
          <span class="progress__bar" :style="{ width: `${((step + 1) / questions.length) * 100}%` }" />
        </div>
      </div>

      <h2 ref="questionHeading" class="quiz__title" tabindex="-1">{{ current.title }}</h2>
      <p class="muted">{{ current.id === 'dish' ? 'Обязательный вопрос' : 'Можно пропустить — выберите «Не важно»' }}</p>

      <div class="quiz__options">
        <button
          v-for="option in current.options"
          :key="option.id"
          type="button"
          class="option"
          :class="{ 'option--selected': answers[current.id] === option.id }"
          :aria-pressed="answers[current.id] === option.id"
          @click="choose(option.id)"
        >
          {{ upperFirst(option.title) }}
        </button>
        <button
          v-if="current.id !== 'dish'"
          type="button"
          class="option option--neutral"
          :class="{ 'option--selected': answers[current.id] === null }"
          :aria-pressed="answers[current.id] === null"
          @click="choose(null)"
        >
          Не важно
        </button>
      </div>

      <button v-if="step > 0" type="button" class="btn btn--ghost quiz__back" @click="goBack">
        <AppIcon name="chevron-left" :size="20" />
        Назад
      </button>

      <ul v-if="summary.length" class="summary" aria-label="Ваши ответы">
        <li v-for="item in summary" :key="item.id" class="chip chip--static">{{ item.value }}</li>
      </ul>
    </section>

    <div v-else-if="phase === 'loading'" class="sommelier__loading" role="status" aria-live="polite">
      <span class="spinner" aria-hidden="true" />
      Подбираем вина из каталога…
    </div>

    <div v-else-if="phase === 'error'" class="alert" role="alert">
      <p class="alert__title">Не получилось подобрать</p>
      <p>{{ errorText }}</p>
      <div class="alert__actions">
        <button type="button" class="btn btn--primary" @click="fetchSuggestions(lastRequestFresh)">Повторить</button>
        <button type="button" class="btn btn--ghost" @click="editAnswers">Изменить ответы</button>
      </div>
    </div>

    <section v-else-if="phase === 'results'" class="results" aria-labelledby="results-title">
      <h2 id="results-title" class="results__title">Подборка из каталога</h2>
      <ul v-if="summary.length" class="summary" aria-label="Ваши ответы">
        <li v-for="item in summary" :key="item.id" class="chip chip--static">{{ item.value }}</li>
      </ul>

      <p v-if="exhausted" class="notice">Других вариантов по этим ответам в каталоге нет — ниже прежняя подборка.</p>

      <ol v-if="suggestions.length" class="results__list">
        <li v-for="(suggestion, index) in suggestions" :key="suggestion.slug">
          <WineTile :item="suggestion" variant="row" :rank="index + 1" />
        </li>
      </ol>
      <p v-else class="notice">
        По этим ответам в каталоге ничего не нашлось. Попробуйте ответить «Не важно» на часть вопросов.
      </p>

      <div class="results__actions">
        <button
          v-if="suggestions.length && !exhausted"
          type="button"
          class="btn btn--primary btn--large btn--block"
          @click="fetchSuggestions(false)"
        >
          <AppIcon name="refresh" />
          Другие варианты
        </button>
        <button type="button" class="btn btn--secondary btn--large btn--block" @click="editAnswers">
          Изменить ответы
        </button>
      </div>

      <p v-if="disclaimer" class="results__disclaimer">{{ disclaimer }}</p>
    </section>
  </div>
</template>

<style scoped>
.sommelier__intro h1 {
  margin: 2px 0 8px;
}

.quiz {
  margin-top: 24px;
}

.quiz__progress {
  display: grid;
  gap: 6px;
}

.progress {
  overflow: hidden;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--c-primary-soft-hover);
}

.progress__bar {
  display: block;
  height: 100%;
  background: var(--c-primary);
  transition: width 0.3s ease-out;
}

.quiz__title {
  margin: 20px 0 4px;
  outline: none;
}

.quiz__options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 16px;
}

.option {
  min-height: 56px;
  padding: 10px 14px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-m);
  background: transparent;
  color: var(--c-primary);
  font-size: 15px;
  font-weight: 600;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: background-color 0.2s ease-in, border-color 0.2s ease-in, color 0.2s ease-in, transform 0.2s linear;
}

.option:hover {
  border-color: var(--c-primary);
}

.option:active {
  transform: scale(0.98);
  background: var(--c-primary-soft);
}

.option--neutral {
  border-style: dashed;
  color: var(--c-text-secondary);
}

.option--selected,
.option--selected:active {
  border-color: var(--c-primary);
  background: var(--c-primary);
  color: #fff;
}

.quiz__back {
  margin-top: 12px;
}

.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}

.sommelier__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 200px;
  font-weight: 600;
}

.results {
  margin-top: 24px;
}

.results__list {
  display: grid;
  gap: 12px;
  margin: 16px 0 0;
  padding: 0;
  list-style: none;
}

.results__actions {
  display: grid;
  gap: 12px;
  margin-top: 20px;
}

.results__disclaimer {
  margin-top: 20px;
  color: var(--c-text-secondary);
  font-size: 13px;
  line-height: 18px;
}
</style>
