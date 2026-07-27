// @vitest-environment node
import { describe, expect, it } from 'vitest'

import { urlBase64ToUint8Array } from './push'

describe('urlBase64ToUint8Array', () => {
  it('解出 VAPID public key 慣用的 65 bytes uncompressed point', () => {
    // 65 bytes 全 0（首 byte 之後補 64 個 0）的 base64url，長度 88 字元含兩個 padding。
    const key = Buffer.alloc(65)
    key[0] = 0x04
    const base64url = key.toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

    const out = urlBase64ToUint8Array(base64url)
    expect(out.length).toBe(65)
    expect(out[0]).toBe(0x04)
  })

  it('處理 base64url 專屬字元與缺少的 padding', () => {
    // 0xfb 0xff 0xbe → base64 "+/++"、base64url "-_--"，且刻意不補 padding。
    const out = urlBase64ToUint8Array('-_--')
    expect(Array.from(out)).toEqual([0xfb, 0xff, 0xbe])
  })
})
