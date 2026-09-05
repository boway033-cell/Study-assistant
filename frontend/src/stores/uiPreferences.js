import { reactive, watch } from 'vue'

const storageKey = 'study-ui-preferences'
const defaults = { glowBorders: true, keycapButtons: true, libraryDensity: 'comfortable' }

function readPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || '{}')
    return {
      glowBorders: typeof saved.glowBorders === 'boolean' ? saved.glowBorders : defaults.glowBorders,
      keycapButtons: typeof saved.keycapButtons === 'boolean' ? saved.keycapButtons : defaults.keycapButtons,
      libraryDensity: saved.libraryDensity === 'compact' ? 'compact' : 'comfortable',
    }
  } catch {
    return { ...defaults }
  }
}

export const uiPreferences = reactive(readPreferences())

watch(uiPreferences, preferences => {
  try { localStorage.setItem(storageKey, JSON.stringify(preferences)) } catch { /* Storage may be unavailable in private windows. */ }
}, { deep: true })

export function resetUiPreferences() {
  Object.assign(uiPreferences, defaults)
}
