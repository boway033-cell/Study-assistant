// Only changed leaves are sent. Fixed preset fields cannot become overrides.
export function formatDiff(value, preset) {
  const diff = {}
  for (const [key, item] of Object.entries(value || {})) {
    if (item && typeof item === 'object' && !Array.isArray(item)) {
      const nested = formatDiff(item, preset?.[key])
      if (Object.keys(nested).length) diff[key] = nested
    } else if (item !== preset?.[key]) diff[key] = item
  }
  return diff
}

export function formatChanges(value, previous, prefix = '') {
  return Object.entries(value || {}).flatMap(([key, item]) => {
    const name = prefix ? `${prefix}.${key}` : key
    if (item && typeof item === 'object') return formatChanges(item, previous?.[key], name)
    return item === previous?.[key] ? [] : [{ field: name, before: previous?.[key], after: item }]
  })
}
