const UA = 'music-monitor-lx-gateway/0.1'

export async function searchPlatform(source, keyword, page = 1, limit = 30) {
  const key = String(source || '').trim().toLowerCase()
  if (!keyword) return { items: [], total: 0 }
  if (key === 'tx') return qqSearch(keyword, page, limit)
  if (key === 'wy') return wySearch(keyword, page, limit)
  if (key === 'kw') return kwSearch(keyword, page, limit)
  if (key === 'kg') return kgSearch(keyword, page, limit)
  if (key === 'mg') return mgSearch(keyword, page, limit)
  throw new Error(`不支持的搜索平台: ${key}`)
}

async function get(url, options = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options.timeout || 15000)
  try {
    const response = await fetch(url, {
      headers: { 'User-Agent': UA, Accept: 'application/json,text/plain,*/*', ...(options.headers || {}) },
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return await response.text()
  } finally {
    clearTimeout(timer)
  }
}

function parseJson(text) {
  const value = String(text || '').replace(/^\uFEFF/, '').trim()
  try { return JSON.parse(value) } catch {}
  const start = value.search(/[\[{]/)
  const end = Math.max(value.lastIndexOf('}'), value.lastIndexOf(']'))
  if (start >= 0 && end > start) {
    try { return JSON.parse(value.slice(start, end + 1)) } catch {}
  }
  throw new Error('搜索接口返回不是 JSON')
}

async function qqSearch(keyword, page, limit) {
  const url = 'https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp'
    + `?g_tk=5381&uin=0&format=json&inCharset=utf-8&outCharset=utf-8&notice=0`
    + `&platform=h5&needNewCode=1&w=${encodeURIComponent(keyword)}&zhidaqu=1&catZhida=1`
    + `&t=0&flag=1&ie=utf-8&sem=1&aggr=0&perpage=${limit}&n=${limit}&p=${page}&remoteplace=txt.mqq.all`
  const data = parseJson(await get(url, { headers: { Referer: 'https://y.qq.com/m/index.html' } }))
  const song = data?.data?.song
  const items = (song?.list || []).map((item) => {
    const album = item.album || {}
    const mid = String(item.songmid || '').trim()
    const singers = (item.singer || []).map((s) => s.name).filter(Boolean).join('/')
    return candidate({
      id: mid, source: 'tx', name: item.songname, artist: singers,
      album: album.name || '', duration: Number(item.interval) || 0,
      cover: album.mid ? `https://y.gtimg.cn/music/photo_new/T002R300x300M000${album.mid}.jpg` : '',
      songmid: mid, musicId: mid, extra: { songmid: mid, song_id: String(item.songid || '') },
      qualitys: qualitysFromSizes(item),
    })
  }).filter((item) => item.id)
  return { items, total: Number(song?.totalnum) || items.length }
}

async function wySearch(keyword, page, limit) {
  const offset = (page - 1) * limit
  const url = `https://music.163.com/api/cloudsearch/pc?s=${encodeURIComponent(keyword)}&type=1&offset=${offset}&limit=${limit}`
  const data = parseJson(await get(url, { headers: { Referer: 'https://music.163.com' } }))
  const songs = data?.result?.songs || []
  const items = songs.map((item) => {
    const mid = String(item.id || '')
    const artists = (item.ar || item.artists || []).map((s) => s.name).filter(Boolean).join('/')
    return candidate({
      id: mid, source: 'wy', name: item.name, artist: artists,
      album: item.al?.name || item.album?.name || '',
      duration: Math.floor(Number(item.dt || item.duration || 0) / 1000),
      cover: item.al?.picUrl || '', songId: mid, songmid: mid,
      extra: { songId: mid }, qualitys: qualitysFromTypes(item.privilege),
    })
  }).filter((item) => item.id)
  return { items, total: Number(data?.result?.songCount) || items.length }
}

async function kwSearch(keyword, page, limit) {
  const url = `https://search.kuwo.cn/r.s?client=kt&all=${encodeURIComponent(keyword)}&pn=${page - 1}&rn=${limit}`
    + '&uid=0&ver=kwplayer_ar_9.2.2.1&vipver=1&show_copyright_off=1&newver=1&ft=music&cluster=0&strategy=2012&encoding=utf8&rformat=json&vermerge=1&moession='
  const raw = await get(url)
  const data = parseJson(raw.replace(/'/g, '"'))
  const items = (data?.abslist || []).map((item) => {
    const id = String(item.MUSICRID || item.DC_TARGETID || '').replace(/^MUSIC_/, '')
    return candidate({
      id, source: 'kw', name: stripHtml(item.SONGNAME), artist: stripHtml(item.ARTIST),
      album: stripHtml(item.ALBUM), duration: Number(item.DURATION) || 0,
      cover: '', songId: id, musicId: id, rid: id, dcTargetId: item.DC_TARGETID || '',
      extra: { rid: id, dcTargetId: item.DC_TARGETID || '' }, qualitys: parseKwQualitys(item.N_MINFO || item.MINFO),
    })
  }).filter((item) => item.id)
  return { items, total: Number(data?.TOTAL) || items.length }
}

async function kgSearch(keyword, page, limit) {
  const url = `https://songsearch.kugou.com/song_search_v2?keyword=${encodeURIComponent(keyword)}&page=${page}&pagesize=${limit}&userid=-1&clientver=2000&platform=WebFilter&filter=2&iscorrection=1&privilege_filter=0&area_code=1`
  const data = parseJson(await get(url, { headers: { Referer: 'https://www.kugou.com/' } }))
  const list = data?.data?.lists || []
  const items = list.map((item) => {
    const id = String(item.FileHash || '').trim()
    return candidate({
      id, source: 'kg', name: stripHtml(item.SongName), artist: stripHtml(item.SingerName),
      album: stripHtml(item.AlbumName), duration: Number(item.Duration) || 0,
      cover: String(item.Image || item.AlbumImage || '').replace(/\{size\}/g, '400'),
      hash: id, albumId: item.AlbumID || '', albumAudioId: String(item.ID || item.AlbumAudioID || ''),
      extra: { hash: id, hq_hash: item['320Hash'] || '', sq_hash: item.SQHash || '', album_id: item.AlbumID || '' },
      qualitys: ['128k', '320k', 'flac'],
    })
  }).filter((item) => item.id)
  return { items, total: Number(data?.data?.total) || items.length }
}

async function mgSearch(keyword, page, limit) {
  const url = `https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/search_all.do?text=${encodeURIComponent(keyword)}&pageNo=${page}&pageSize=${limit}&searchSwitch=%7B%22song%22%3A1%7D`
  const data = parseJson(await get(url, { headers: { Referer: 'https://music.migu.cn', channel: '0146951' } }))
  const list = data?.songResultData?.result || []
  const items = list.map((item) => {
    const id = String(item.copyrightId || item.id || '').trim()
    return candidate({
      id, source: 'mg', name: item.name, artist: (item.singers || []).map((s) => s.name).filter(Boolean).join('/'),
      album: item.albums?.[0]?.name || '', duration: 0, cover: item.img3 || item.img2 || item.img1 || '',
      copyrightId: id, extra: { copyrightId: id }, qualitys: ['128k', '320k', 'flac'],
    })
  }).filter((item) => item.id)
  return { items, total: Number(data?.songResultData?.totalCount) || items.length }
}

function candidate(data) {
  return {
    id: String(data.id || ''), source: data.source || '', name: stripHtml(data.name), artist: stripHtml(data.artist),
    album: stripHtml(data.album), duration: Number(data.duration) || 0, cover: data.cover || '',
    songId: data.songId || data.id || '', songmid: data.songmid || '', hash: data.hash || '',
    copyrightId: data.copyrightId || '', musicId: data.musicId || '', rid: data.rid || '',
    albumId: data.albumId || '', albumMid: data.albumMid || '', albumAudioId: data.albumAudioId || '',
    dcTargetId: data.dcTargetId || '', extra: data.extra || {}, qualitys: data.qualitys || [],
  }
}

function qualitysFromSizes(item) {
  const out = []
  if (Number(item.sizeflac) > 0) out.push('flac')
  if (Number(item.size320) > 0) out.push('320k')
  return out.length ? out : ['128k', '320k']
}

function qualitysFromTypes(privilege) {
  return privilege ? ['128k', '320k', 'flac'] : []
}

function parseKwQualitys(raw) {
  const out = []
  for (const part of String(raw || '').split(';')) {
    const match = part.match(/bitrate:(\d+),format:([^,]+)/i)
    if (!match) continue
    if (match[1] === '128' && /mp3/i.test(match[2])) out.push('128k')
    if (match[1] === '320' && /mp3/i.test(match[2])) out.push('320k')
    if (/flac|mflac/i.test(match[2])) out.push(Number(match[1]) >= 4000 ? 'hires' : 'flac')
  }
  return [...new Set(out)]
}

function stripHtml(value) {
  return String(value || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim()
}
