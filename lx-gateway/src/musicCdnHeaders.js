const SOURCE_REFERERS = {
  tx: { Referer: 'https://y.qq.com/', Origin: 'https://y.qq.com' },
  kw: { Referer: 'https://www.kuwo.cn/' },
  kg: { Referer: 'https://www.kugou.com/' },
  wy: { Referer: 'https://music.163.com/' },
  mg: { Referer: 'https://music.migu.cn/' },
}

export function buildMusicCdnHeaders(source = '') {
  return {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
    Accept: '*/*',
    ...(SOURCE_REFERERS[String(source || '').toLowerCase()] || {}),
  }
}
