export function extractMusicUrl(data) {
  if (data == null || data === '') return ''
  if (typeof data === 'string') {
    const value = data.trim()
    return /^https?:\/\//i.test(value) ? value : ''
  }
  if (typeof data !== 'object') return ''
  const direct = [data.url, data.musicUrl, data.link, data.playUrl, data.src]
    .find((value) => typeof value === 'string' && value.trim())
  if (direct) return direct.trim()
  if (data.data != null) return extractMusicUrl(data.data)
  if (data.body != null) return extractMusicUrl(data.body)
  return ''
}

export function assertMusicUrl(data) {
  const value = extractMusicUrl(data)
  if (!value) throw new Error('未获取到音频 URL')
  return value
}
