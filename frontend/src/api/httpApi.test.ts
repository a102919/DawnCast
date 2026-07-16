import { afterEach, describe, expect, it, vi } from 'vitest'
import { httpApi, AppError } from './httpApi'

// Auth 一律回 null token，避免真的呼叫 Supabase。
vi.mock('../lib/supabaseClient', () => ({
  getAccessToken: async () => null,
}))

interface FetchCall {
  readonly url: string
  readonly init: RequestInit | undefined
}

function mockFetchOnce(status: number, body: unknown): { calls: FetchCall[] } {
  const calls: FetchCall[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return { calls }
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

describe('httpApi.triggerGenerateJob（T1）', () => {
  it('POST /jobs/orders/{date}/generate，method/scheme/schema 都對', async () => {
    const { calls } = mockFetchOnce(202, { ok: true, data: null, error: null })
    await expect(httpApi.triggerGenerateJob('2026-07-16')).resolves.toBeUndefined()
    expect(calls).toHaveLength(1)
    const call = calls[0]!
    expect(call.url).toMatch(/\/jobs\/orders\/2026-07-16\/generate$/)
    expect(call.init?.method).toBe('POST')
    // 無 body、不解析回應
    expect(call.init?.body).toBeUndefined()
  })

  it('409 時 reject 帶 code=conflict（對齊 backend 測試的 envelope 形狀）', async () => {
    mockFetchOnce(409, {
      ok: false,
      data: null,
      error: { code: 'conflict', message: '已排入排程或已播放' },
    })
    await expect(httpApi.triggerGenerateJob('2026-07-16')).rejects.toMatchObject({
      name: 'AppError',
      code: 'conflict',
    })
  })

  it('401 未授權時 reject 帶 code=unauthorized', async () => {
    mockFetchOnce(401, {
      ok: false,
      data: null,
      error: { code: 'unauthorized', message: '未登入' },
    })
    await expect(httpApi.triggerGenerateJob('2026-07-16')).rejects.toMatchObject({
      name: 'AppError',
      code: 'unauthorized',
    })
  })

  it('404 查無訂單時 reject 帶 code=not_found', async () => {
    mockFetchOnce(404, {
      ok: false,
      data: null,
      error: { code: 'not_found', message: '查無當日訂單' },
    })
    await expect(httpApi.triggerGenerateJob('2026-07-16')).rejects.toMatchObject({
      name: 'AppError',
      code: 'not_found',
    })
  })
})

describe('httpApi.getMe / deleteAccount（T4 帳號自我管理）', () => {
  it('getMe GET /me 並解開 envelope 回 AccountInfo', async () => {
    const { calls } = mockFetchOnce(200, {
      ok: true,
      error: null,
      data: {
        id: 'user-1',
        email: 'alice@example.com',
        tz: 'Asia/Taipei',
        deliveryTime: '08:30',
        createdAt: '2026-07-01T00:00:00Z',
      },
    })
    const me = await httpApi.getMe()
    expect(me).toEqual({
      id: 'user-1',
      email: 'alice@example.com',
      tz: 'Asia/Taipei',
      deliveryTime: '08:30',
      createdAt: '2026-07-01T00:00:00Z',
    })
    expect(calls).toHaveLength(1)
    const call = calls[0]!
    expect(call.url).toMatch(/\/me$/)
    expect(call.init?.method ?? 'GET').toBe('GET')
  })

  it('deleteAccount DELETE /me 並回 void', async () => {
    const { calls } = mockFetchOnce(200, { ok: true, data: null, error: null })
    await expect(httpApi.deleteAccount()).resolves.toBeUndefined()
    expect(calls).toHaveLength(1)
    const call = calls[0]!
    expect(call.url).toMatch(/\/me$/)
    expect(call.init?.method).toBe('DELETE')
  })

  it('deleteAccount 401 時丟出帶 code=unauthorized 的 AppError', async () => {
    mockFetchOnce(401, {
      ok: false,
      data: null,
      error: { code: 'unauthorized', message: '未登入' },
    })
    await expect(httpApi.deleteAccount()).rejects.toMatchObject({
      name: 'AppError',
      code: 'unauthorized',
    })
  })

  it('getMe schema 不符時丟出 schema_mismatch', async () => {
    mockFetchOnce(200, {
      ok: true,
      error: null,
      // 故意少 createdAt → zod schema 驗證失敗
      data: { id: 'user-1', email: '', tz: 'Asia/Taipei', deliveryTime: '07:00' },
    })
    await expect(httpApi.getMe()).rejects.toMatchObject({ code: 'schema_mismatch' })
  })
})
