export function tokenizeReadingText(text: string): Array<{ text: string; isWord: boolean }> {
  return text.split(/([A-Za-z]+(?:['’-][A-Za-z]+)*)/g).filter(Boolean).map((part) => ({
    text: part,
    isWord: /^[A-Za-z]+(?:['’-][A-Za-z]+)*$/.test(part),
  }))
}
