import { reactive } from 'vue'
import { listTasks } from '../api'

export const taskCenter = reactive({
  items: [],
  loading: false,
  initialized: false,
  lastUpdated: null,
})

let timer = null
let requestInFlight = null

export async function refreshTasks() {
  if (requestInFlight) return requestInFlight
  taskCenter.loading = true
  requestInFlight = listTasks({ limit: 30 })
    .then((result) => {
      taskCenter.items = result.items || []
      taskCenter.initialized = true
      taskCenter.lastUpdated = new Date()
    })
    .catch(() => {})
    .finally(() => {
      taskCenter.loading = false
      requestInFlight = null
    })
  return requestInFlight
}

export function startTaskPolling() {
  if (timer) return
  refreshTasks()
  // 单个轻量请求、最多 30 行；页面不可见时不轮询。
  timer = window.setInterval(() => {
    if (document.visibilityState === 'visible') refreshTasks()
  }, 8000)
}

export function stopTaskPolling() {
  if (timer) window.clearInterval(timer)
  timer = null
}

export function notifyTaskSubmitted() {
  window.setTimeout(refreshTasks, 250)
}
