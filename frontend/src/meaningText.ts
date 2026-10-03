const NUMBERED_MEANING = /(?=(?:\d{1,2}[.)、]\s*|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*))/u
const LEADING_MARKER = /^(?:\d{1,2}[.)、]\s*|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]\s*)/u
const PART_OF_SPEECH_ONLY = /^(?:(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|aux|modal|int|abbr)\.?)(?:\s*[/、,&，]\s*(?:(?:n|v|vt|vi|adj|adv|prep|conj|pron|num|art|aux|modal|int|abbr)\.?))*$/iu

export function hasMeaningText(values: string[], fallback: string) {
  return values.some((value) => value.trim().length > 0) || fallback.trim().length > 0
}

export function splitMeanings(values: string[]) {
  const parts = values.flatMap((value) => {
    const trimmed = value.trim()
    if (!trimmed) return []
    const numberedParts = trimmed.split(NUMBERED_MEANING).map((part) => part.replace(LEADING_MARKER, '').trim()).filter(Boolean)
    return numberedParts.length > 1 ? numberedParts : [trimmed.replace(LEADING_MARKER, '').trim()]
  })

  return parts.reduce<string[]>((meanings, part, index) => {
    if (PART_OF_SPEECH_ONLY.test(part) && index < parts.length - 1) {
      meanings.push(part)
      return meanings
    }
    const previous = meanings.at(-1)
    if (previous && PART_OF_SPEECH_ONLY.test(previous)) {
      meanings[meanings.length - 1] = `${previous} ${part}`
    } else {
      meanings.push(part)
    }
    return meanings
  }, [])
}
