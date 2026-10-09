import { describe, expect, it } from 'vitest'
import { dictionarySourceName, dictionaryMeaningNotice } from './dictionarySource'

describe('NETEM gap source labels', () => {
  it('names the actual source and separates Chinese selection from AI translation', () => {
    expect(dictionarySourceName('enwiktionary-zh_selection-0256.csv')).toBe('英文维基词典')
    expect(dictionarySourceName('enwiktionary-translation-0271.csv')).toBe('英文维基词典')
    expect(dictionaryMeaningNotice(['enwiktionary-translation-0271.csv'])).toContain('AI翻译整理')
    expect(dictionaryMeaningNotice(['enwiktionary-translation-0271.csv'])).toContain('未经人工逐词核准')
    expect(dictionaryMeaningNotice(['enwiktionary-zh_selection-0256.csv'])).toContain('中文选取')
    expect(dictionaryMeaningNotice(['wikdict.csv'])).toBe('词典释义 · 抽取片段，未做全库逐词语义校订')
  })
})
