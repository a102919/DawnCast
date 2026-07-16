import { afterEach, describe, expect, it, vi } from 'vitest'
import { httpApi, AppError } from './httpApi'

// Auth 一律回 null token，避免真的呼叫 Supabase。
vi.mock('../lib/supabaseClient', () => ({
  getAccessToken: async () => null,
}))

function mockFetchOnce(status: number, body: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('httpApi envelope 解包', () => {
  it('ok=true 時回傳 data（逐欄位對齊 types.ts）', async () => {
    mockFetchOnce(200, {
      ok: true,
      error: null,
      data: {
        word: 'loop',
        pos: ['noun'],
        translation: '迴圈',
      },
    })
    const entry = await httpApi.lookupDict('loop')
    expect(entry).toEqual({ word: 'loop', pos: ['noun'], translation: '迴圈' })
  })

  it('ok=false 時丟出帶 code 的 AppError', async () => {
    mockFetchOnce(400, {
      ok: false,
      data: null,
      error: { code: 'bad_request', message: '參數錯誤' },
    })
    await expect(httpApi.listVocab()).rejects.toMatchObject({
      name: 'AppError',
      code: 'bad_request',
    })
    await expect(httpApi.listVocab()).rejects.toBeInstanceOf(AppError)
  })

  it('lookupDict 查無字（404）回 null', async () => {
    mockFetchOnce(404, { ok: false, data: null, error: { code: 'not_found', message: 'x' } })
    const entry = await httpApi.lookupDict('zzz')
    expect(entry).toBeNull()
  })

  it('無內容方法（DELETE）data=null 時 resolve 不丟錯', async () => {
    mockFetchOnce(200, { ok: true, data: null, error: null })
    await expect(httpApi.clearVocab()).resolves.toBeUndefined()
  })

  it('回應 data 結構不符 schema 時丟出 schema_mismatch', async () => {
    mockFetchOnce(200, { ok: true, error: null, data: { word: 123 } })
    await expect(httpApi.lookupDict('loop')).rejects.toMatchObject({ code: 'schema_mismatch' })
  })

  it('isFavorite 解包 slug 陣列並判斷成員', async () => {
    mockFetchOnce(200, { ok: true, error: null, data: ['a', 'b'] })
    await expect(httpApi.isFavorite('b')).resolves.toBe(true)
  })
})
