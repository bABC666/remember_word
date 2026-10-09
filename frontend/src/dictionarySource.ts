export function dictionarySourceName(name?: string): string {
  if (name === 'wikdict.csv') return 'WikDict'
  if (name === 'zhwiktionary.csv') return '中文维基词典'
  if (name?.startsWith('enwiktionary-')) return '英文维基词典'
  return '词典来源'
}

export function dictionaryMeaningNotice(names: (string | undefined)[]): string {
  if (names.some(name => name?.startsWith('enwiktionary-translation-'))) {
    return '词典译释 · AI翻译整理，未经人工逐词核准'
  }
  if (names.some(name => name?.startsWith('enwiktionary-zh_selection-'))) {
    return '词典释义 · 中文选取、繁简转换，未经人工逐词核准'
  }
  return '词典释义 · 抽取片段，未做全库逐词语义校订'
}
