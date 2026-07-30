/** Cloudflare Worker：R2 audio proxy for DawnCast。
 *
 * 解 Safari 對 `<audio src>` 的 NotSupportedError bug：
 *   - Safari 預設發 byte-range 預讀（Range: bytes=0-46828）
 *   - R2 對超出檔案大小的 Range 回 416 + Content-Type: text/plain
 *   - Safari 視 416 為「URL 不支援 range → 不支援播放」→ 永久 NotSupportedError
 *
 * Worker 自己 parse Range、向 R2 拿對應 bytes、回標準 206 Partial Content +
 * Content-Range + Accept-Ranges。Safari 拿到正確 206 後就 happy 開播。
 *
 * 支援：
 *   - GET / HEAD / OPTIONS（其他 method 405）
 *   - 單 range（封閉 bytes=0-46828 / open bytes=500- / suffix bytes=-500）
 *   - 多 range / 不合法 → 416 Range Not Satisfiable + Content-Range: bytes STAR/{total}
 *   - 沒 Range → 200 + 完整檔案 + Accept-Ranges: bytes
 *
 * 路由：pathname（去掉首 /）就是 R2 object key。原樣透傳，無 query string。
 *   GET /dawncast/episodes/<id>/segments/001.mp3
 *     → R2.get('dawncast/episodes/<id>/segments/001.mp3')
 */

export interface Env {
  AUDIO: R2Bucket;
  ALLOWED_ORIGIN: string;
}

/** Safari 必須能在 response header 讀到這幾個（via Access-Control-Expose-Headers）。 */
const EXPOSED_HEADERS = [
  "Content-Range",
  "Accept-Ranges",
  "Content-Length",
  "Content-Type",
  "ETag",
  "Last-Modified",
].join(", ");

/** CORS headers。Origin 用 env 寫死的 prod 值，不 echo request Origin（避免 wildcard 攻擊）。
 *  dev 用 wrangler dev 本機 R2 mock，不打 prod worker。 */
function corsHeaders(env: Env): Headers {
  const h = new Headers();
  h.set("Access-Control-Allow-Origin", env.ALLOWED_ORIGIN);
  h.set("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS");
  h.set(
    "Access-Control-Allow-Headers",
    "Range, If-Range, If-None-Match, Content-Type",
  );
  h.set("Access-Control-Expose-Headers", EXPOSED_HEADERS);
  h.set("Access-Control-Max-Age", "86400");
  h.set("Vary", "Origin");
  return h;
}

interface ParsedRange {
  offset: number;
  length: number;
}

/** 解析單 range。RFC 7233 §2.1。Safari/Chrome 都只發單 range。多 range 或格式錯回 null。 */
function parseSingleRange(header: string, total: number): ParsedRange | null {
  const m = /^bytes=(\d*)-(\d*)$/.exec(header.trim());
  if (!m || (m[1] === "" && m[2] === "")) return null;

  // suffix: bytes=-500 → 最後 500 bytes
  if (m[1] === "") {
    const suffix = Number(m[2]);
    if (!Number.isFinite(suffix) || suffix <= 0) return null;
    const length = Math.min(suffix, total);
    return { offset: total - length, length };
  }

  const start = Number(m[1]);
  if (!Number.isFinite(start) || start < 0) return null;

  // open: bytes=500- → 從 500 到結尾
  if (m[2] === "") {
    if (start >= total) return null;
    return { offset: start, length: total - start };
  }

  // 封閉: bytes=0-46828
  const end = Number(m[2]);
  if (!Number.isFinite(end) || end < start) return null;
  const length = Math.min(end - start + 1, total - start);
  return length > 0 ? { offset: start, length } : null;
}

function notFound(env: Env): Response {
  return new Response("Not Found", { status: 404, headers: corsHeaders(env) });
}

function methodNotAllowed(env: Env): Response {
  return new Response("Method Not Allowed", {
    status: 405,
    headers: corsHeaders(env),
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // OPTIONS preflight：跨 origin audio 必須通過的握手。
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(env) });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return methodNotAllowed(env);
    }

    // pathname → R2 key。Worker host 之後的路徑直接當 key（含 /dawncast/... 多層）。
    const url = new URL(request.url);
    const key = decodeURIComponent(url.pathname.replace(/^\//, ""));
    if (!key) return notFound(env);

    // head() 先拿 size：省 R2 read ops，又能正確填 Content-Range 的 total。
    const meta = await env.AUDIO.head(key);
    if (!meta) return notFound(env);
    const total = meta.size;

    if (request.method === "HEAD") {
      const h = corsHeaders(env);
      h.set("Accept-Ranges", "bytes");
      h.set("Content-Length", String(total));
      meta.writeHttpMetadata(h);
      h.set("etag", meta.httpEtag);
      h.set("Cache-Control", "public, max-age=31536000, immutable");
      return new Response(null, { status: 200, headers: h });
    }

    // 有 Range → parse。有 Range 但解析失敗（多 range / 格式錯 / start 超 total）→ 416。
    const rangeHeader = request.headers.get("Range");
    const parsed = rangeHeader ? parseSingleRange(rangeHeader, total) : null;
    if (rangeHeader && !parsed) {
      const h = corsHeaders(env);
      // RFC 7233 §4.4：416 帶 Content-Range: bytes */{total}，Safari 才能拿到正確大小。
      h.set("Content-Range", `bytes */${total}`);
      h.set("Accept-Ranges", "bytes");
      return new Response("Range Not Satisfiable", {
        status: 416,
        headers: h,
      });
    }

    // 從 R2 拿 bytes。range 帶 offset/length 給 R2。
    const obj = parsed
      ? await env.AUDIO.get(key, {
          range: { offset: parsed.offset, length: parsed.length },
        })
      : await env.AUDIO.get(key);
    if (!obj || !obj.body) return notFound(env);

    const h = corsHeaders(env);
    h.set("Accept-Ranges", "bytes");
    obj.writeHttpMetadata(h);
    h.set("etag", obj.httpEtag);
    // 音檔內容不可變（r2 key 含 episode uuid + segment index），一年 cache 直接命中。
    h.set("Cache-Control", "public, max-age=31536000, immutable");

    if (parsed) {
      // obj.size 在 R2 SDK 對 range 請求不一定等於實際回傳 bytes（某些版本會回
      // object 的 total size），用 parsed.length 才是「真的回多少 bytes」的可靠來源。
      // end byte 不超過 total - 1（撞 EOF 的話 parsed.length < requested length，
      // 但 Safari 仍可接受：它的 range 請求是 best-effort，只要看到 Content-Range
      // 跟 Content-Length 一致就 happy）。
      const actualLength = Math.min(parsed.length, total - parsed.offset);
      const endByte = parsed.offset + actualLength - 1;
      h.set("Content-Range", `bytes ${parsed.offset}-${endByte}/${total}`);
      h.set("Content-Length", String(actualLength));
      return new Response(obj.body, { status: 206, headers: h });
    }
    h.set("Content-Length", String(total));
    return new Response(obj.body, { status: 200, headers: h });
  },
};