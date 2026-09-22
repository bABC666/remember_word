export const fileKey = (file: File) => `${file.name}:${file.size}:${file.lastModified}`

export function mergeImageFiles(current: File[], incoming: File[]) {
  const merged = new Map(current.map((file) => [fileKey(file), file]))
  incoming.filter((file) => file.type.startsWith('image/')).forEach((file) => merged.set(fileKey(file), file))
  return Array.from(merged.values())
}
