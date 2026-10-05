export function dictionarySourceName(name?: string): string {
  if (name === 'wikdict.csv') return 'WikDict'
  if (name === 'zhwiktionary.csv') return '中文维基词典'
  return '词典来源'
}
