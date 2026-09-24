<script setup lang="ts">
import type { NuxtError } from '#app'

const props = defineProps<{ error: NuxtError }>()
const is404 = computed(() => props.error.statusCode === 404)

useHead({ title: is404.value ? 'Страница не найдена' : 'Ошибка' })

function goHome() {
  clearError({ redirect: '/' })
}
</script>

<template>
  <NuxtLayout>
    <section class="error-page">
      <p class="eyebrow">{{ is404 ? 'Ошибка 404' : 'Ошибка' }}</p>
      <h1>{{ is404 ? 'Страница не найдена' : 'Что-то пошло не так' }}</h1>
      <p class="muted">
        {{ is404 ? 'Возможно, ссылка устарела. Попробуйте отсканировать этикетку заново.' : 'Обновите страницу или вернитесь к сканеру.' }}
      </p>
      <button type="button" class="btn btn--primary btn--large btn--block" @click="goHome">К сканеру</button>
    </section>
  </NuxtLayout>
</template>

<style scoped>
.error-page {
  display: grid;
  gap: 12px;
  padding-top: 24px;
}
.error-page .btn {
  margin-top: 12px;
}
</style>
