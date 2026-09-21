import assert from 'node:assert/strict'
import test from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { nextLowerQuality, mapPlatform, mapPreferredQuality, QUALITY_LADDER } from '../src/quality.js'
import { downloadSong } from '../src/download.js'

test('platform aliases map to LX keys', () => {
  assert.equal(mapPlatform('qq'), 'tx')
  assert.equal(mapPlatform('netease'), 'wy')
  assert.equal(mapPlatform('kugou'), 'kg')
  assert.equal(mapPlatform('kuwo'), 'kw')
  assert.equal(mapPlatform('migu'), 'mg')
})

test('legacy qualities map to explicit LX qualities', () => {
  assert.equal(mapPreferredQuality('standard'), '128k')
  assert.equal(mapPreferredQuality('high'), '320k')
  assert.equal(mapPreferredQuality('lossless'), 'flac')
  assert.equal(mapPreferredQuality('master'), 'master')
})

test('quality ladder descends and observes floor', () => {
  assert.equal(nextLowerQuality('master'), 'atmos_plus')
  assert.equal(nextLowerQuality('flac', 'flac'), '')
  assert.equal(nextLowerQuality('320k', '128k'), '128k')
})

test('download attempts every quality until the configured floor', async () => {
  const seen = []
  const sourceManager = {
    async request(_source, quality) {
      seen.push(quality)
      const error = new Error(`missing ${quality}`)
      error.attemptedIds = ['stub']
      throw error
    },
  }
  await assert.rejects(
    downloadSong({
      sourceManager,
      songs: [{ source: 'tx', id: '1', name: 'Song', artist: 'Artist' }],
      preferredQuality: 'master',
      qualityFloor: 'flac',
      cascade: true,
      root: '/tmp/music-monitor-lx-unit',
    }),
    (error) => error.code === 'LX_DOWNLOAD_FAILED',
  )
  assert.deepEqual(seen, QUALITY_LADDER.slice(0, QUALITY_LADDER.indexOf('flac') + 1))
})

test('artist directory is used before duplicate-file check', async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lx-artist-dir-'))
  try {
    const artistDir = path.join(root, 'Artist')
    fs.mkdirSync(artistDir)
    fs.writeFileSync(path.join(artistDir, 'Artist - Song.mp3'), Buffer.alloc(2048))
    const sourceManager = {
      async request() {
        return { url: 'https://example.com/song.mp3', sourceId: 'stub', attemptedIds: ['stub'] }
      },
    }
    const result = await downloadSong({
      sourceManager,
      songs: [{ source: 'tx', id: '1', name: 'Song', artist: 'Artist' }],
      preferredQuality: '128k',
      root,
      artistDir: true,
    })
    assert.equal(result.skipped, true)
    assert.equal(result.path, 'data/downloads/Artist/Artist - Song.mp3')
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
})
