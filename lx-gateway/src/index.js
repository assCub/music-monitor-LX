import express from 'express'
import fs from 'fs'
import path from 'path'
import { SourceManager } from './sourceManager.js'
import { downloadSong } from './download.js'
import { mapPlatform, mapPreferredQuality, QUALITY_LADDER } from './quality.js'
import { searchPlatform } from './platformSearch.js'

const PORT = Number(process.env.PORT || 8090)
const SOURCE_DIR = process.env.LX_SOURCES_DIR || '/sources'
const DOWNLOAD_ROOT = process.env.DOWNLOAD_ROOT || '/downloads'
const CONFIG_DIR = process.env.LX_CONFIG_DIR || '/config'
const STATE_FILE = path.join(CONFIG_DIR, 'gateway.json')
const FILENAME_TEMPLATE = process.env.DOWNLOAD_FILENAME || '{artist} - {name}'
const TOKEN = String(process.env.LX_GATEWAY_TOKEN || '').trim()

const manager = new SourceManager(SOURCE_DIR)
const gatewayState = loadGatewayState()
const downloadTasks = new Map((gatewayState.downloads || []).map((item) => [item.id, item]))
const app = express()
app.use(express.json({ limit: '2mb' }))

// 健康检查不暴露配置写操作，保持无鉴权，避免启用 token 后容器永远 unhealthy。
app.get('/healthz', (_req, res) => {
  res.json({ ok: true, sources: manager.list(), quality_ladder: QUALITY_LADDER })
})

function auth(req, res, next) {
  if (!TOKEN) return next()
  const token = String(req.headers.authorization || '').replace(/^Bearer\s+/i, '')
  if (token !== TOKEN) return res.status(401).json({ ok: false, error: 'LX 网关未授权' })
  next()
}

app.use(auth)

app.get('/api/sources', (_req, res) => {
  res.json({ ok: true, items: sourceInventory() })
})

app.get('/api/config', (_req, res) => {
  res.json({
    ok: true,
    download_root: DOWNLOAD_ROOT,
    filename_template: FILENAME_TEMPLATE,
    source_dir: SOURCE_DIR,
  })
})

app.get('/api/downloads', (req, res) => {
  const limit = Math.max(1, Math.min(500, Number(req.query.limit) || 100))
  const userId = safeUserId(req.query.user_id || '')
  const items = [...downloadTasks.values()]
    .filter((item) => !userId || item.user_id === userId)
    .sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
    .slice(0, limit)
  res.json({ ok: true, items })
})

app.delete('/api/downloads/completed', (req, res) => {
  const userId = safeUserId(req.query.user_id || '')
  for (const [id, task] of downloadTasks) {
    if ((!userId || task.user_id === userId) && ['completed', 'failed', 'skipped'].includes(task.status)) downloadTasks.delete(id)
  }
  persistDownloadHistory()
  res.json({ ok: true, items: [...downloadTasks.values()] })
})

app.post('/api/sources/reload', async (_req, res) => {
  try {
    res.json({ ok: true, ...(await reloadManager()), items: sourceInventory() })
  } catch (error) {
    res.status(500).json({ ok: false, error: error?.message || String(error) })
  }
})

app.post('/api/sources/import', async (req, res) => {
  try {
    const requestedName = String(req.body?.name || '').trim()
    const sourceUrl = String(req.body?.url || '').trim()
    let script = String(req.body?.script || '')
    if (!script && sourceUrl) script = await fetchSourceScript(sourceUrl)
    if (!script.trim()) return res.status(400).json({ ok: false, error: '请提供音源 URL 或脚本文本' })
    if (Buffer.byteLength(script, 'utf8') > 5 * 1024 * 1024) {
      return res.status(413).json({ ok: false, error: '音源脚本不能超过 5MB' })
    }

    const id = uniqueSourceId(requestedName || sourceNameFromScript(script) || 'lx-source')
    // 先在沙箱中验证，成功后再写入持久目录；坏脚本不会污染下次启动。
    const sources = await manager.load(id, script)
    const filePath = sourceFilePath(id)
    const tempPath = `${filePath}.tmp-${process.pid}-${Date.now()}`
    try {
      fs.writeFileSync(tempPath, script, { encoding: 'utf8', mode: 0o600 })
      fs.renameSync(tempPath, filePath)
    } catch (error) {
      manager.unload(id)
      try { fs.unlinkSync(tempPath) } catch {}
      throw error
    }
    gatewayState.disabled = gatewayState.disabled.filter((item) => item !== id)
    saveGatewayState()
    res.json({ ok: true, item: { id, name: requestedName || sourceNameFromScript(script) || id, sources, enabled: true } })
  } catch (error) {
    res.status(400).json({ ok: false, error: error?.message || String(error) })
  }
})

app.delete('/api/sources/:id', async (req, res) => {
  try {
    const id = safeSourceId(req.params.id)
    const filePath = sourceFilePath(id)
    if (fs.existsSync(filePath)) fs.unlinkSync(filePath)
    manager.unload(id)
    gatewayState.disabled = gatewayState.disabled.filter((item) => item !== id)
    saveGatewayState()
    // 重新扫描，保证运行态与磁盘内容一致。
    const result = await reloadManager()
    res.json({ ok: true, ...result, items: sourceInventory() })
  } catch (error) {
    res.status(400).json({ ok: false, error: error?.message || String(error) })
  }
})

app.post('/api/sources/:id/enable', async (req, res) => {
  try {
    const id = safeSourceId(req.params.id)
    if (!fs.existsSync(sourceFilePath(id))) return res.status(404).json({ ok: false, error: '音源文件不存在' })
    const enabled = req.body?.enabled !== false
    const disabled = new Set(gatewayState.disabled)
    if (enabled) disabled.delete(id)
    else disabled.add(id)
    gatewayState.disabled = [...disabled]
    saveGatewayState()
    const result = await reloadManager()
    res.json({ ok: true, ...result, items: sourceInventory() })
  } catch (error) {
    res.status(400).json({ ok: false, error: error?.message || String(error) })
  }
})

app.post('/api/search', async (req, res) => {
  try {
    const source = mapPlatform(req.body?.source)
    const result = await searchPlatform(source, String(req.body?.keyword || '').trim(), Number(req.body?.page) || 1, Math.min(100, Number(req.body?.limit) || 30))
    res.json({ ok: true, source, ...result })
  } catch (error) {
    res.status(502).json({ ok: false, error: error?.message || String(error) })
  }
})

app.post('/api/download', async (req, res) => {
  const taskId = `lx_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
  let task = null
  try {
    const songs = (Array.isArray(req.body?.candidates) && req.body.candidates.length
      ? req.body.candidates
      : [req.body?.song || req.body]).map(normalizeSong)
    if (songs.some((song) => !song.name || !song.artist)) return res.status(400).json({ ok: false, error: '缺少歌曲名或歌手' })
    if (songs.some((song) => !song.source)) return res.status(400).json({ ok: false, error: '缺少歌曲来源平台' })
    const rawFloor = String(req.body?.quality_floor || '').trim()
    const userId = safeUserId(req.body?.user_id || '')
    const requestedSubdir = String(req.body?.download_subdir || '')
    const userSubdir = userId ? path.posix.join('users', userId, requestedSubdir) : requestedSubdir
    const display = songs[0]
    task = {
      id: taskId,
      user_id: userId,
      name: display.name,
      artist: display.artist,
      status: 'resolving',
      requested_quality: mapPreferredQuality(req.body?.preferred_quality || req.body?.quality),
      quality: '',
      platform: '',
      source_id: '',
      progress: 0,
      downloaded_bytes: 0,
      total_bytes: 0,
      error: '',
      path: '',
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    downloadTasks.set(taskId, task)
    trimDownloadTasks()
    const result = await downloadSong({
      sourceManager: manager,
      songs,
      preferredQuality: mapPreferredQuality(req.body?.preferred_quality || req.body?.quality),
      qualityFloor: rawFloor ? mapPreferredQuality(rawFloor) : '',
      cascade: req.body?.cascade !== false,
      root: DOWNLOAD_ROOT,
      subdir: userSubdir,
      artistDir: req.body?.artist_dir !== false,
      filenameTemplate: String(req.body?.filename_template || FILENAME_TEMPLATE).slice(0, 200),
      onProgress(update) {
        updateDownloadTask(taskId, {
          status: update.status || task.status,
          quality: update.quality || task.quality,
          platform: update.platform || task.platform,
          source_id: update.sourceId || task.source_id,
          progress: Number(update.progress ?? task.progress) || 0,
          downloaded_bytes: Number(update.downloadedBytes ?? task.downloaded_bytes) || 0,
          total_bytes: Number(update.totalBytes ?? task.total_bytes) || 0,
          error: update.error || '',
        })
      },
    })
    updateDownloadTask(taskId, {
      status: result.skipped ? 'skipped' : 'completed',
      quality: result.quality || task.quality,
      platform: result.source || task.platform,
      source_id: result.sourceId || task.source_id,
      progress: 1,
      path: result.path || '',
      error: '',
    }, { persist: true })
    res.json({ ...result, task_id: taskId })
  } catch (error) {
    if (task) updateDownloadTask(taskId, { status: 'failed', error: error?.message || String(error) }, { persist: true })
    res.status(502).json({ ok: false, task_id: taskId, code: error?.code || 'LX_DOWNLOAD_FAILED', error: error?.message || String(error), attempts: error?.attempts || [] })
  }
})

async function fetchSourceScript(sourceUrl) {
  let parsed
  try { parsed = new URL(sourceUrl) } catch { throw new Error('音源 URL 格式不正确') }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('音源 URL 只支持 HTTP/HTTPS')
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 20000)
  try {
    const response = await fetch(parsed, { redirect: 'follow', signal: controller.signal })
    if (!response.ok) throw new Error(`下载音源失败：HTTP ${response.status}`)
    const length = Number(response.headers.get('content-length') || 0)
    if (length > 5 * 1024 * 1024) throw new Error('音源脚本不能超过 5MB')
    const script = await response.text()
    if (Buffer.byteLength(script, 'utf8') > 5 * 1024 * 1024) throw new Error('音源脚本不能超过 5MB')
    return script
  } finally {
    clearTimeout(timer)
  }
}

function sourceNameFromScript(script) {
  const match = String(script || '').match(/@name\s+([^\r\n*]+)/i)
  return match ? match[1].trim() : ''
}

function safeSourceId(value) {
  const id = String(value || '').trim()
  if (!/^[a-zA-Z0-9_-]{1,80}$/.test(id)) throw new Error('音源 ID 不合法')
  return id
}

function safeUserId(value) {
  const id = String(value || '').trim()
  if (!id) return ''
  if (!/^[a-f0-9-]{36}$/i.test(id)) throw new Error('用户 ID 不合法')
  return id
}

function slugSourceId(value) {
  const ascii = String(value || '').trim().toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
  return ascii || `source-${Date.now()}`
}

function uniqueSourceId(value) {
  const base = slugSourceId(value)
  let id = base
  let suffix = 2
  while (fs.existsSync(sourceFilePath(id)) || manager.list().some((item) => item.id === id)) {
    id = `${base}-${suffix++}`
  }
  return safeSourceId(id)
}

function sourceFilePath(id) {
  const safe = safeSourceId(id)
  const base = path.resolve(SOURCE_DIR)
  const target = path.resolve(base, `${safe}.js`)
  if (!target.startsWith(`${base}${path.sep}`)) throw new Error('音源路径越界')
  return target
}

function loadGatewayState() {
  try {
    const data = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'))
    return {
      disabled: Array.isArray(data?.disabled) ? data.disabled.map(String) : [],
      downloads: Array.isArray(data?.downloads) ? data.downloads : [],
    }
  } catch {
    return { disabled: [], downloads: [] }
  }
}

function saveGatewayState() {
  fs.mkdirSync(CONFIG_DIR, { recursive: true })
  const temp = `${STATE_FILE}.tmp-${process.pid}`
  fs.writeFileSync(temp, JSON.stringify(gatewayState, null, 2), { encoding: 'utf8', mode: 0o600 })
  fs.renameSync(temp, STATE_FILE)
}

function updateDownloadTask(id, update, { persist = false } = {}) {
  const current = downloadTasks.get(id)
  if (!current) return
  Object.assign(current, update, { updated_at: new Date().toISOString() })
  downloadTasks.set(id, current)
  if (persist) persistDownloadHistory()
}

function trimDownloadTasks() {
  const ordered = [...downloadTasks.values()].sort((a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')))
  for (const task of ordered.slice(200)) downloadTasks.delete(task.id)
}

function persistDownloadHistory() {
  trimDownloadTasks()
  gatewayState.downloads = [...downloadTasks.values()]
    .filter((task) => ['completed', 'failed', 'skipped'].includes(task.status))
    .slice(-200)
  saveGatewayState()
}

async function reloadManager() {
  return manager.reload({ disabledIds: gatewayState.disabled })
}

function sourceInventory() {
  fs.mkdirSync(SOURCE_DIR, { recursive: true })
  const active = new Map(manager.list().map((item) => [item.id, item]))
  const disabled = new Set(gatewayState.disabled)
  const errors = new Map(manager.listErrors().map((item) => [item.id, item.error]))
  return fs.readdirSync(SOURCE_DIR)
    .filter((name) => /\.(js|mjs|cjs)$/i.test(name))
    .sort()
    .map((name) => {
      const id = path.basename(name).replace(/\.[^.]+$/, '')
      const loaded = active.get(id)
      let scriptName = id
      try { scriptName = sourceNameFromScript(fs.readFileSync(path.join(SOURCE_DIR, name), 'utf8')) || id } catch {}
      return {
        id,
        name: loaded?.name || scriptName,
        enabled: !disabled.has(id),
        loaded: Boolean(loaded),
        error: errors.get(id) || '',
        sources: loaded?.sources || {},
      }
    })
}

function normalizeSong(raw = {}) {
  raw = raw || {}
  return {
    ...raw,
    source: mapPlatform(raw.source),
    name: String(raw.name || '').trim(),
    artist: String(raw.artist || raw.singer || '').trim(),
    singer: String(raw.singer || raw.artist || '').trim(),
    album: String(raw.album || '').trim(),
    id: String(raw.id || raw.songId || '').trim(),
    duration: Number(raw.duration || raw.interval || 0) || 0,
    extra: raw.extra && typeof raw.extra === 'object' ? raw.extra : {},
  }
}

await reloadManager()
fs.mkdirSync(DOWNLOAD_ROOT, { recursive: true })
app.listen(PORT, '0.0.0.0', () => {
  console.log(`lx-gateway listening on ${PORT}; sources=${manager.list().length}; downloadRoot=${path.resolve(DOWNLOAD_ROOT)}`)
})
