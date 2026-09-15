import type { WineCard } from '#shared/types/api'

/**
 * Списки в localStorage: история сканов и «Хочу попробовать».
 * Данные живут только в этом браузере; читаются после монтирования, чтобы SSR и гидратация совпадали.
 */

export interface ScanHistoryItem {
  id: string
  at: number
  status: 'found' | 'not_found'
  slug: string | null
  name: string | null
  winery: string | null
  /** ближайший кандидат, если вино не найдено */
  nearestSlug: string | null
  nearestName: string | null
}

export interface WishItem {
  slug: string
  name: string
  winery: string
  at: number
}

function readList<T>(key: string): T[] {
  try {
    const raw = localStorage.getItem(key)
    const value: unknown = raw ? JSON.parse(raw) : []
    return Array.isArray(value) ? (value as T[]) : []
  } catch {
    return []
  }
}

function writeList<T>(key: string, items: T[]): void {
  try {
    localStorage.setItem(key, JSON.stringify(items))
  } catch {
    // хранилище недоступно (приватный режим) — список проживёт до перезагрузки страницы
  }
}

function useStoredList<T>(key: string, limit: number) {
  const items = useState<T[]>(`stored:${key}`, () => [])
  const loaded = useState<boolean>(`stored-loaded:${key}`, () => false)

  function load() {
    if (import.meta.client && !loaded.value) {
      items.value = readList<T>(key)
      loaded.value = true
    }
  }

  onMounted(load)

  function update(change: (current: T[]) => T[]) {
    load()
    items.value = change(items.value).slice(0, limit)
    writeList(key, items.value)
  }

  return { items, loaded, update }
}

export function useScanHistory() {
  const { items, loaded, update } = useStoredList<ScanHistoryItem>('winescan:scan-history', 50)
  return {
    items,
    loaded,
    add: (item: ScanHistoryItem) => update((current) => [item, ...current]),
    clear: () => update(() => []),
  }
}

export function useWishlist() {
  const { items, loaded, update } = useStoredList<WishItem>('winescan:wishlist', 100)
  const has = (slug: string) => items.value.some((item) => item.slug === slug)

  function toggle(card: Pick<WineCard, 'slug' | 'name' | 'winery'>) {
    update((current) =>
      current.some((item) => item.slug === card.slug)
        ? current.filter((item) => item.slug !== card.slug)
        : [{ slug: card.slug, name: card.name, winery: card.winery, at: Date.now() }, ...current],
    )
  }

  return {
    items,
    loaded,
    has,
    toggle,
    remove: (slug: string) => update((current) => current.filter((item) => item.slug !== slug)),
    clear: () => update(() => []),
  }
}
