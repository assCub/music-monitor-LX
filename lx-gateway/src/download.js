import fs from 'fs'
import path from 'path'
import { parseFile } from 'music-metadata'
import { buildMusicCdnHeaders } from './musicCdnHeaders.js'
import { guessExtension, isLosslessQuality, nextLowerQuality, QUALITY_LADDER, qualityLabel } from './quality.js'

export async function downloadSong({ sourceManager, song, songs, preferredQuality, qualityFloor = '', cascade = true, root, subdir = '', artistDir = true, filenameTemplate = '{artist} - {name}', onProgress = null }) {
  const musicInfos = (Array.isArray(songs) && songs.length ? songs : [song]).map(buildMusicInfo)
  const display = musicInfos[0]
  const downloadRoot = resolveDownloadRoot(root, subdir)
  const targetRoot = artistDir
    ? path.join(downloadRoot, sanitize(display.singer || display.artist || 'Unknown'))
    : downloadRoot
  const preferred = normalizeQuality(preferredQuality)
  const attempts = []
  let quality = preferred
  let lastError = null

  while (quality) {
    for (const musicInfo of musicInfos) {
      const usedSourceIds = []
      while (true) {
        let part = ''
        let resolved = null
        try {
        onProgress?.({ status: 'resolving', quality, platform: musicInfo.source, downloadedBytes: 0, totalBytes: 0, progress: 0 })
        // SourceManager 在同一档内已经按激活顺序轮询所有 LX 音源。
        resolved = await sourceManager.request(musicInfo.source, quality, musicInfo, usedSourceIds)
        for (const id of resolved.attemptedIds || [resolved.sourceId]) {
          if (id && !usedSourceIds.includes(id)) usedSourceIds.push(id)
        }
        const ext = guessExtension(quality, resolved.url)
        const safeName = sanitize(filenameTemplate.replace(/\{name\}/g, display.name || 'Unknown').replace(/\{artist\}/g, display.singer || 'Unknown').replace(/\{album\}/g, display.album || ''))
        const destination = path.join(targetRoot, `${safeName}${ext}`)
        if (fs.existsSync(destination)) {
          return { ok: true, skipped: true, path: relativeDownloadPath(destination, root), quality, source: musicInfo.source, sourceId: resolved.sourceId, attempts }
        }
        part = `${destination}.part`
        fs.mkdirSync(path.dirname(destination), { recursive: true })
        onProgress?.({ status: 'downloading', quality, platform: musicInfo.source, sourceId: resolved.sourceId })
        await streamToFile(resolved.url, part, musicInfo.source, quality, onProgress)
        onProgress?.({ status: 'validating', quality, platform: musicInfo.source, sourceId: resolved.sourceId, progress: 1 })
        await validateDownloadedFile(part, quality, musicInfo.duration)
        fs.renameSync(part, destination)
        attempts.push({ quality, source_id: resolved.sourceId, ok: true })
        return {
          ok: true,
          skipped: false,
          path: relativeDownloadPath(destination, root),
          quality,
          source: musicInfo.source,
          sourceId: resolved.sourceId,
          attempts,
        }
      } catch (error) {
        lastError = error
        for (const id of error?.attemptedIds || []) {
          if (id && !usedSourceIds.includes(id)) usedSourceIds.push(id)
        }
        if (part) try { fs.unlinkSync(part) } catch {}
        attempts.push({ quality, platform: musicInfo.source, source_id: resolved?.sourceId || '', ok: false, error: error?.message || String(error) })
        onProgress?.({ status: 'retrying', quality, platform: musicInfo.source, sourceId: resolved?.sourceId || '', error: error?.message || String(error), progress: 0 })
        // 已取得 URL 但文件校验/下载失败：跳过该音源，在同档继续下一个音源。
        if (resolved?.sourceId) continue
        break
      }
      }
    }
    if (!cascade) break
    quality = nextLowerQuality(quality, qualityFloor)
  }

  const message = lastError?.message || `无法获取 ${qualityLabel(preferred)} 音质`
  const error = new Error(message)
  error.code = 'LX_DOWNLOAD_FAILED'
  error.attempts = attempts
  throw error
}

function buildMusicInfo(song = {}) {
  const extra = song.extra && typeof song.extra === 'object' ? song.extra : {}
  const source = String(song.source || '').trim().toLowerCase()
  const id = String(song.id || song.songId || '').trim()
  const pick = (...values) => values.find((value) => value != null && value !== '') || ''
  const songId = pick(song.songId, extra.songId, id)
  // 与 Lemon Music 的 musicInfo 兼容层一致：不少 LX 脚本不按平台读取字段，
  // 而是统一取 hash ?? songmid，所以所有平台都要提供可用回退值。
  const songmid = pick(song.songmid, extra.songmid, songId, song.hash, extra.hash, song.copyrightId, id)
  const hash = pick(song.hash, extra.hash, extra.file_hash, songId, songmid)
  const rid = pick(song.rid, extra.rid, source === 'kw' ? id : '')
  const copyrightId = pick(song.copyrightId, extra.copyrightId, source === 'mg' ? id : '')
  return {
    ...song,
    source,
    name: song.name || '',
    singer: song.singer || song.artist || '',
    artist: song.artist || song.singer || '',
    album: song.album || '',
    albumName: song.album || '',
    songId,
    songmid,
    hash,
    rid,
    copyrightId,
    musicId: pick(song.musicId, extra.musicId, songId),
    strMediaMid: pick(song.strMediaMid, extra.strMediaMid),
    albumId: pick(song.albumId, extra.albumId, extra.album_id),
    albumMid: pick(song.albumMid, extra.albumMid, extra.albummid),
    albummid: pick(song.albummid, extra.albummid),
    albumAudioId: pick(song.albumAudioId, extra.albumAudioId, extra.album_audio_id),
    dcTargetId: pick(song.dcTargetId, extra.dcTargetId),
    duration: song.duration || 0,
    interval: song.duration || 0,
    types: song.types || [],
    qualitys: song.qualitys || [],
  }
}

function normalizeQuality(value) {
  const q = String(value || '').trim().toLowerCase()
  if (q === 'standard') return '128k'
  if (q === 'high') return '320k'
  if (q === 'lossless') return 'flac'
  if (q === 'hires') return 'hires'
  return QUALITY_LADDER.includes(q) ? q : 'master'
}

async function streamToFile(url, partPath, source, quality, onProgress = null) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 300000)
  try {
    const response = await fetch(url, { headers: buildMusicCdnHeaders(source), signal: controller.signal })
    if (!response.ok) throw new Error(`下载响应异常 HTTP ${response.status}`)
    if (isLosslessQuality(quality) && /^(text\/|application\/(json|xml|javascript)|image\/)/i.test(response.headers.get('content-type') || '')) {
      throw new Error('无损档返回了错误页而不是音频')
    }
    if (!response.body) throw new Error('下载响应没有内容')
    const total = Number(response.headers.get('content-length') || 0)
    let downloaded = 0
    let lastReportedAt = 0
    const file = fs.createWriteStream(partPath)
    try {
      for await (const chunk of response.body) {
        file.write(chunk)
        downloaded += chunk.length
        const now = Date.now()
        if (now - lastReportedAt >= 400 || (total > 0 && downloaded >= total)) {
          lastReportedAt = now
          onProgress?.({
            status: 'downloading',
            downloadedBytes: downloaded,
            totalBytes: total,
            progress: total > 0 ? Math.min(1, downloaded / total) : 0,
          })
        }
      }
    } finally {
      await new Promise((resolve) => file.end(resolve))
    }
  } finally {
    clearTimeout(timer)
  }
}

async function validateDownloadedFile(filePath, quality, expectedDuration) {
  const stat = fs.statSync(filePath)
  if (stat.size < 1024) throw new Error('下载文件过小，疑似错误页或试听片段')
  const metadata = await parseFile(filePath, { duration: true })
  const actual = Number(metadata?.format?.duration) || 0
  if (expectedDuration && actual && expectedDuration >= 45 && actual < expectedDuration * 0.55 && expectedDuration - actual >= 25) {
    throw new Error(`检测到试听片段：实际 ${Math.round(actual)} 秒，原曲约 ${Math.round(expectedDuration)} 秒`)
  }
  if (isLosslessQuality(quality)) {
    const fd = fs.openSync(filePath, 'r')
    const header = Buffer.alloc(16)
    try { fs.readSync(fd, header, 0, header.length, 0) } finally { fs.closeSync(fd) }
    const hasFlac = header.includes(Buffer.from('fLaC'))
    if (!hasFlac) throw new Error('目标音质要求无损，但返回文件不是有效 FLAC')
  }
}

function sanitize(value) {
  return String(value || 'Unknown').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').replace(/\s+/g, ' ').trim().slice(0, 180) || 'Unknown'
}

function resolveDownloadRoot(root, subdir) {
  const base = path.resolve(root)
  const value = String(subdir || '').trim()
  if (!value) return base
  if (path.isAbsolute(value)) throw new Error('下载目录必须填写挂载根目录下的相对路径')
  const target = path.resolve(base, value)
  if (target !== base && !target.startsWith(`${base}${path.sep}`)) throw new Error('下载目录不能超出挂载根目录')
  return target
}

function relativeDownloadPath(filePath, root) {
  return `data/downloads/${path.relative(root, filePath).split(path.sep).join('/')}`
}
