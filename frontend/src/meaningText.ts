const NUMBERED_MEANING = /(?=(?:\d{1,2}[.)、]\s*|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*))/u
const LEADING_MARKER = /^(?:\d{1,2}[.)、]\s*|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*)/u

export function splitMeanings(values: string[]) {
  return values.flatMap((value) => {
    const trimmed = value.trim()
    if (!trimmed) return []
    const parts = trimmed.split(NUMBERED_MEANING).map((part) => part.replace(LEADING_MARKER, '').trim()).filter(Boolean)
    return parts.length > 1 ? parts : [trimmed.replace(LEADING_MARKER, '').trim()]
  })
}
