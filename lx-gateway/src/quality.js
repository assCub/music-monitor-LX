export const QUALITY_LADDER = [
  'master', 'atmos_plus', 'atmos', 'hires', 'flac24bit', 'flac', '320k', '128k',
]

export const QUALITY_LABELS = {
  '128k': '128K',
  '320k': '320K',
  flac: 'FLAC',
  flac24bit: 'FLAC Hi-Res',
  hires: 'Hi-Res',
  atmos: '杜比全景声',
  atmos_plus: '杜比全景声 Plus',
  master: '超清母带',
}

export const PLATFORM_MAP = {
  netease: 'wy',
  qq: 'tx',
  kugou: 'kg',
  kuwo: 'kw',
  migu: 'mg',
  wy: 'wy',
  tx: 'tx',
  kg: 'kg',
  kw: 'kw',
  mg: 'mg',
}

export function mapPlatform(source) {
  return PLATFORM_MAP[String(source || '').trim().toLowerCase()] || String(source || '').trim().toLowerCase()
}

export function mapPreferredQuality(value) {
  const q = String(value || '').trim().toLowerCase()
  if (q === 'standard') return '128k'
  if (q === 'high') return '320k'
  if (q === 'lossless') return 'flac'
  if (q === 'hires') return 'hires'
  if (QUALITY_LADDER.includes(q)) return q
  return 'master'
}

export function nextLowerQuality(current, floor = '') {
  const index = QUALITY_LADDER.indexOf(String(current || '').trim())
  if (index < 0 || index >= QUALITY_LADDER.length - 1) return ''
  const next = QUALITY_LADDER[index + 1]
  if (floor && qualityRank(next) > qualityRank(floor)) return ''
  return next
}

export function qualityRank(value) {
  const index = QUALITY_LADDER.indexOf(String(value || '').trim())
  return index < 0 ? 999 : index
}

export function qualityLabel(value) {
  return QUALITY_LABELS[value] || value || ''
}

export function isLosslessQuality(value) {
  return /^(flac|flac24bit|hires|master)$/i.test(String(value || '').trim())
}

export function guessExtension(quality, url = '') {
  const match = String(url).match(/\.([a-z0-9]{2,5})(?:[?#]|$)/i)
  if (match && /^(mp3|flac|m4a|aac|ogg|opus|wav|ape)$/i.test(match[1])) return `.${match[1].toLowerCase()}`
  if (/^(atmos|atmos_plus)$/i.test(quality)) return '.m4a'
  if (isLosslessQuality(quality)) return '.flac'
  return '.mp3'
}
