<script setup lang="ts">
// Собственные линейные иконки 24×24 (без сторонних наборов), цвет — currentColor.
const ICONS: Record<string, string[]> = {
  camera: [
    'M3.5 8.5A1.5 1.5 0 0 1 5 7h2.2l1.6-2.2h6.4L16.8 7H19a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z',
    'M8.8 13a3.2 3.2 0 1 0 6.4 0 3.2 3.2 0 1 0-6.4 0',
  ],
  image: [
    'M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5z',
    'M4 16l4.5-4.5 4 4 2.5-2.5L20 18',
    'M14 8.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0',
  ],
  glass: ['M7 3.5h10l-.6 5.2a4.4 4.4 0 0 1-8.8 0z', 'M12 13.1v6.9', 'M8.5 20.5h7'],
  clock: ['M3.5 12a8.5 8.5 0 1 0 17 0 8.5 8.5 0 1 0-17 0', 'M12 7.5V12l3 2'],
  pin: ['M12 21s-6.5-5.6-6.5-11a6.5 6.5 0 0 1 13 0c0 5.4-6.5 11-6.5 11z', 'M10 10a2 2 0 1 0 4 0 2 2 0 1 0-4 0'],
  grape: [
    'M12 3.5v3',
    'M12 5c1.4-1.4 3.4-1.9 5-1.4',
    'M6.4 9.5a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0',
    'M12.4 9.5a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0',
    'M9.4 14.2a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0',
    'M9.4 18.9a2.6 2.6 0 1 0 5.2 0 2.6 2.6 0 1 0-5.2 0',
  ],
  calendar: [
    'M4.5 6.5A1.5 1.5 0 0 1 6 5h12a1.5 1.5 0 0 1 1.5 1.5v12A1.5 1.5 0 0 1 18 20H6a1.5 1.5 0 0 1-1.5-1.5z',
    'M4.5 10h15',
    'M8.5 3.5v3',
    'M15.5 3.5v3',
  ],
  bottle: ['M10 3h4v4.5c0 1.6 2.5 2.6 2.5 5.5v7.5a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1V13c0-2.9 2.5-3.9 2.5-5.5z', 'M7.5 14h9'],
  'chevron-right': ['M9 6l6 6-6 6'],
  'chevron-left': ['M15 6l-6 6 6 6'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  alert: ['M12 8v5', 'M12 16.5v.5', 'M3 12a9 9 0 1 0 18 0 9 9 0 1 0-18 0'],
  heart: ['M12 20s-7.5-4.6-7.5-10.2A4.3 4.3 0 0 1 12 7.2a4.3 4.3 0 0 1 7.5 2.6C19.5 15.4 12 20 12 20z'],
  utensils: ['M7 3.5v17', 'M4.5 3.5V8a2.5 2.5 0 0 0 5 0V3.5', 'M16.5 20.5v-17c-2 1-3 3.5-3 6.5v3h3'],
  trash: ['M4.5 7h15', 'M9.5 7V4.5h5V7', 'M6.5 7l1 13h9l1-13'],
  refresh: ['M19.5 12a7.5 7.5 0 1 1-2.2-5.3', 'M19.5 4.5v4h-4'],
}

const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 24 })
const paths = computed(() => ICONS[props.name] ?? [])
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path v-for="(d, index) in paths" :key="index" :d="d" />
  </svg>
</template>
