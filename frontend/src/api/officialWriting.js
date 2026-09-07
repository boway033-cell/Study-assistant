import http from './index'

const base = '/writing/official'
export const officialOptions = () => http.get(`${base}/options`)
export const officialHistory = (page = 1) => http.get(`${base}/outputs`, { params: { page, page_size: 20 } })
export const officialDetail = id => http.get(`${base}/outputs/${id}`)
export const officialOutline = brief => http.post(`${base}/outline`, { brief, allow_model: true }, { timeout: 180000 })
export const officialAction = (id, action, payload) => http.post(`${base}/outputs/${id}/${action}`, payload, { timeout: 180000 })
export const officialSaveProfile = payload => http.put(`${base}/profile`, payload)
