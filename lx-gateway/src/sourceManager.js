import fs from 'fs'
import path from 'path'
import vm from 'vm'
import needle from 'needle'
import { createLxUtils, createSandboxRequire } from './lxSourceRuntime.js'
import { assertMusicUrl } from './sourceResult.js'

const SUPPORTED_SOURCES = ['kw', 'kg', 'tx', 'wy', 'mg']
const SUPPORTED_ACTIONS = ['musicUrl', 'lyric', 'pic']
const SUPPORTED_QUALITYS = ['128k', '320k', 'flac', 'flac24bit', 'hires', 'atmos', 'atmos_plus', 'master']

export class SourceManager {
  constructor(dir) {
    this.dir = dir
    this.active = new Map()
    this.errors = []
  }

  async reload({ disabledIds = [] } = {}) {
    this.active.clear()
    const disabled = new Set(disabledIds.map(String))
    fs.mkdirSync(this.dir, { recursive: true })
    const files = fs.readdirSync(this.dir)
      .filter((name) => /\.(js|mjs|cjs)$/i.test(name))
      .sort()
    const errors = []
    for (const name of files) {
      const id = path.basename(name).replace(/\.[^.]+$/, '')
      if (disabled.has(id)) continue
      try {
        const script = fs.readFileSync(path.join(this.dir, name), 'utf8')
        await this.load(id, script)
      } catch (error) {
        errors.push({ id, error: error?.message || String(error) })
      }
    }
    this.errors = errors
    return { loaded: this.list(), errors }
  }

  async load(id, script) {
    const entry = await createEntry(String(id), String(script))
    this.active.set(entry.id, entry)
    return entry.sources
  }

  unload(id) {
    return this.active.delete(String(id || ''))
  }

  list() {
    return [...this.active.values()].map((entry) => ({
      id: entry.id,
      name: entry.name,
      sources: entry.sources,
    }))
  }

  listErrors() {
    return [...this.errors]
  }

  async request(platform, quality, musicInfo, skipIds = []) {
    const candidates = [...this.active.values()]
      .filter((entry) => !skipIds.includes(entry.id))
      .filter((entry) => entry.handler)
      .filter((entry) => {
        const info = entry.sources?.[platform]
        return info
          && (!info.actions.length || info.actions.includes('musicUrl'))
          && (!info.qualitys.length || info.qualitys.includes(quality))
      })
    if (!candidates.length) throw new Error(`没有可用的 LX 音源（平台 ${platform}）`)

    let lastError = null
    const attemptedIds = []
    for (const entry of candidates) {
      attemptedIds.push(entry.id)
      try {
        const data = await invoke(entry, {
          source: platform,
          action: 'musicUrl',
          info: { type: quality, quality, musicInfo },
        })
        const url = assertMusicUrl(data)
        return { url, sourceId: entry.id, sourceName: entry.name, attemptedIds, data }
      } catch (error) {
        lastError = error
      }
    }
    const error = lastError || new Error('LX 音源请求失败')
    error.attemptedIds = attemptedIds
    throw error
  }
}

async function createEntry(id, script) {
  let resolveInit
  let rejectInit
  const ready = new Promise((resolve, reject) => { resolveInit = resolve; rejectInit = reject })
  const timeout = setTimeout(() => rejectInit(new Error('音源初始化超时(20s)')), 20000)
  let requestHandler = null
  let sources = {}
  let settled = false
  const finish = (error, result) => {
    if (settled) return
    settled = true
    clearTimeout(timeout)
    if (error) rejectInit(error)
    else resolveInit(result)
  }

  const meta = parseMeta(script)
  const lx = {
    version: '2.0.0',
    env: 'desktop',
    EVENT_NAMES: { request: 'request', inited: 'inited', updateAlert: 'updateAlert' },
    currentScriptInfo: {
      name: meta.name || id,
      description: meta.description || '',
      version: meta.version || '',
      author: meta.author || '',
      homepage: meta.homepage || '',
      rawScript: script,
    },
    send(event, data) {
      if (event === 'inited') {
        sources = validateSources(data?.sources || {})
        finish(null, sources)
      }
    },
    on(event, handler) {
      if (event === 'request') requestHandler = handler
    },
    request(url, options = {}, callback) { return lxRequest(url, options, callback) },
    utils: createLxUtils(),
  }

  const moduleExports = {}
  const sandbox = {
    lx,
    console: { log() {}, warn() {}, error() {}, info() {}, debug() {}, group() {}, groupEnd() {} },
    setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask,
    URL, URLSearchParams, Buffer, JSON, Promise, Object, Array, String, Number,
    Boolean, Symbol, Math, Date, RegExp, Error, TypeError, RangeError, SyntaxError,
    Map, Set, WeakMap, WeakSet, Proxy, Reflect, ArrayBuffer, Uint8Array, Int8Array,
    Uint16Array, Uint8ClampedArray, Int16Array, Int32Array, Uint32Array,
    Float32Array, Float64Array, DataView, TextEncoder, TextDecoder,
    atob: (value) => Buffer.from(String(value), 'base64').toString('binary'),
    btoa: (value) => Buffer.from(String(value), 'binary').toString('base64'),
    parseInt, parseFloat, isNaN, isFinite, encodeURIComponent, decodeURIComponent,
    encodeURI, decodeURI, escape, unescape,
    require: createSandboxRequire(),
    module: { exports: moduleExports },
    exports: moduleExports,
    process: { env: {}, version: process.version, versions: process.versions, platform: process.platform, nextTick: process.nextTick.bind(process) },
  }
  vm.createContext(sandbox)
  sandbox.global = sandbox
  sandbox.globalThis = sandbox
  sandbox.window = sandbox
  sandbox.self = sandbox
  try {
    vm.runInContext(script, sandbox, { timeout: 15000, displayErrors: true })
    // 兼容 Lemon 支持的原生澜音插件导出格式；没有 lx.inited 的脚本暂不强行猜测。
    if (!settled && sandbox.module?.exports?.sources && typeof sandbox.module.exports.musicUrl === 'function') {
      sources = validateSources(sandbox.module.exports.sources)
      requestHandler = ({ source, info }) => sandbox.module.exports.musicUrl({ source, ...info })
      finish(null, sources)
    }
  } catch (error) {
    finish(new Error(`音源脚本执行失败: ${error.message}`))
  }
  const declared = await ready
  if (!requestHandler) throw new Error('音源未注册 lx.on("request")')
  return { id, name: meta.name || id, sources: declared, handler: requestHandler }
}

function lxRequest(target, options = {}, callback) {
  const method = String(options.method || 'get').toLowerCase()
  const opts = {
    follow_max: 5,
    parse_response: false,
    headers: { connection: 'close', ...(options.headers || {}) },
  }
  if (options.timeout) opts.response_timeout = Math.min(Number(options.timeout) || 60000, 60000)
  let body = null
  if (options.body != null) body = options.body
  else if (options.form != null) { body = options.form; opts.json = false }
  else if (options.formData != null) { body = options.formData; opts.json = false }
  const req = needle.request(method, target, body, opts, (error, response) => {
    if (typeof callback !== 'function') return
    if (error) return callback(error, null, null)
    let parsed = response.body
    try { parsed = JSON.parse(Buffer.isBuffer(response.body) ? response.body.toString() : String(response.body)) } catch {}
    callback(null, {
      statusCode: response.statusCode,
      statusMessage: response.statusMessage,
      headers: response.headers,
      bytes: response.bytes,
      body: parsed,
      raw: response.raw,
    }, parsed)
  })
  return () => { try { req?.request?.abort() } catch {} }
}

function invoke(entry, payload) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`音源 ${entry.id} 请求超时(30s)`)), 30000)
    try {
      const result = entry.handler(payload)
      Promise.resolve(result).then((data) => { clearTimeout(timer); resolve(data) }, (error) => { clearTimeout(timer); reject(error) })
    } catch (error) {
      clearTimeout(timer)
      reject(error)
    }
  })
}

function validateSources(raw) {
  const out = {}
  for (const [key, info] of Object.entries(raw || {})) {
    if (!SUPPORTED_SOURCES.includes(key)) continue
    out[key] = {
      name: info?.name || key,
      type: info?.type || 'music',
      actions: (info?.actions || []).filter((action) => SUPPORTED_ACTIONS.includes(action)),
      qualitys: (info?.qualitys || []).filter((quality) => SUPPORTED_QUALITYS.includes(quality)),
    }
  }
  return out
}

function parseMeta(script) {
  const get = (key) => {
    const match = String(script).match(new RegExp(`@${key}\\s+([^\\r\\n*]+)`, 'i'))
    return match ? match[1].trim() : ''
  }
  return { name: get('name'), description: get('description'), version: get('version'), author: get('author'), homepage: get('homepage') }
}
