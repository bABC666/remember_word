import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MeaningList } from './MeaningList'
import { splitMeanings } from '../meaningText'

describe('MeaningList', () => {
  it('splits numbered meanings into separate readable lines', () => {
    expect(splitMeanings(['1. 保留 2. 保持', '① 维持 ② 留住'])).toEqual(['保留', '保持', '维持', '留住'])
    render(<MeaningList values={['1. 保留 2. 保持']} />)
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('keeps IPA and ordinary punctuation out of meaning splitting', () => {
    expect(splitMeanings(['尤指长期保持；保留'])).toEqual(['尤指长期保持；保留'])
  })
})
