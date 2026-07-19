export const meta = {
  name: 'dict-kaikki-translate-backfill',
  description: '抓已有 example_en（kaikki_stage 或 backfill_examples_freedictionary.py 填的）但缺 example_zh 的字 → 只翻譯 → 寫 JSON+SQL → 寫 DB',
  phases: [
    { title: 'Fetch missing', detail: '抓還缺 example_zh 的字：example_en 來自 kaikki_stage 或 dict_cache 既有值（例如 FreeDictionary 補的）' },
    { title: 'Translate', detail: '單輪翻譯 example_en → example_zh，不生成、不 judge' },
    { title: 'Write out', detail: '寫 JSON + 跑 SQL 寫進 dict_cache' },
  ],
}

// ponytail: 例句本身是 kaikki 真實語料，不需要品質審查，只需要翻譯正確 —
// 跟 dict-example-backfill 的生成+judge+retry-round 版本比，這條路省掉 judge agent 呼叫和 retry round，
// tokens/字預期大幅低於 2,615/字基準（只是翻譯，沒有生成內容的 token 消耗）。

const FetchJoinedSchema = {
  type: 'object',
  properties: { joined: { type: 'string' }, count: { type: 'integer' } },
  required: ['joined', 'count'],
}

const TranslateJoinedSchema = {
  type: 'object',
  properties: { joined: { type: 'string' }, count: { type: 'integer' } },
  required: ['joined', 'count'],
}

const ApplyResultSchema = {
  type: 'object',
  properties: {
    before_count: { type: 'integer' },
    after_count: { type: 'integer' },
    updated_rows: { type: 'integer' },
    sql_path: { type: 'string' },
  },
  required: ['before_count', 'after_count', 'updated_rows', 'sql_path'],
}

let argsObj = args
if (typeof argsObj === 'string') { try { argsObj = JSON.parse(argsObj) } catch (e) { argsObj = {} } }
if (!argsObj || typeof argsObj !== 'object') argsObj = {}

const TARGET = (typeof argsObj.n === 'number') ? argsObj.n : 50
const CHUNK = (typeof argsObj.chunk === 'number') ? argsObj.chunk : Math.min(100, TARGET)
// ponytail: 固定路徑跨 run 共用，曾經讓 write-sql-file agent 沒真的寫新內容、
// 卻讓上一輪留下的舊檔案矇混過關（apply 對舊檔案跑，example_zh 早被填過 → UPDATE 0，
// 900K tokens 白工還看不出錯）。路徑帶 nonce 讓每輪寫到不同檔案，舊檔案不可能被誤用。
const NONCE = (typeof argsObj.nonce === 'string' && argsObj.nonce) ? argsObj.nonce : 'default'
const OUT_JSON = (typeof argsObj.out === 'string') ? argsObj.out : '/tmp/dict_kaikki_examples_' + NONCE + '.json'
const OUT_SQL = (typeof argsObj.sql_out === 'string') ? argsObj.sql_out : '/tmp/dict_kaikki_apply_' + NONCE + '.sql'
const WRITE_DB = argsObj.write_db === true

// ponytail: 雙工或多工時，如果不進行分片，由於 order by d.frq asc 排序在單字詞頻不重複時是完全確定的，
// 導致多個平行 Worker 每次都會撈到完全相同的前 N 筆單字進行翻譯，撞字率高達 99.93%（白花 token）。
// 傳入 workers 與 worker_id，利用 abs(hashtext(word)) % workers = worker_id 將單字雜湊分片，達成平行且 0 衝突。
const WORKERS = (typeof argsObj.workers === 'number') ? argsObj.workers : 1
const WORKER_ID = (typeof argsObj.worker_id === 'number') ? argsObj.worker_id : 0

log('config: n=' + TARGET + ' chunk=' + CHUNK + ' write_db=' + WRITE_DB + ' workers=' + WORKERS + ' worker_id=' + WORKER_ID)

phase('Fetch missing')

// ponytail: agent 偶爾會把「用 \n 分隔」的指示理解成「輸出字面上的反斜線+n 兩個字元」
// 而非真正的換行字元（第六輪實測：1500 筆被擠成一個字串，split('\n') 全部失效，
// 只剩靠 tab-split 巧合湊出的 1 筆垃圾配對，其移 1499 筆無聲遺失）。
// 兩種情況都先正規化成真正換行字元再切，不管 agent 用哪種理解都能正確解析。
function parseJoinedLines(joined) {
  return String(joined || '').replace(/\\n/g, '\n').split('\n').map(function (l) { return l.trim() }).filter(function (l) { return l.length > 0 })
}

let shardClause = ''
if (WORKERS > 1) {
  shardClause = "and abs(hashtext(d.word)) % " + WORKERS + " = " + WORKER_ID + " "
}

// ponytail: 曾經 TARGET=1500 單一 agent 呼叫把全部 102KB 塞進一次 StructuredOutput，
// 卡 schema 驗證兩次失敗（超大 joined 字串把 count 欄位擠掉），agent 最後放棄真內容、
// 回傳假的 { count: 1500, joined: "test" } 矇混過關 —— 完全沒報錯，直到 parseJoinedLines
// 解析出 0 筆才被攔下。跟 translate 一樣拆批次，單批控制在安全大小以內。
// 分批需要 LIMIT/OFFSET 分頁，但原本 order by 用 random() 每次查詢都重新洗牌，會讓批次重疊/漏字，
// 改用 hashtext(word || nonce) 當確定性 tiebreaker：同一 nonce 下排序穩定，不同 nonce 選字不同。
const FETCH_BATCH = 250
const nonceLiteral = NONCE.replace(/'/g, "''")
function buildFetchSql(limit, offset) {
  return "select d.word, coalesce(nullif(d.example_en, ''), k.example_en) as example_en " +
    "from dict_cache d left join kaikki_stage k on k.word = d.word " +
    "where (d.example_zh is null or d.example_zh = '') " +
    shardClause +
    "and coalesce(nullif(d.example_en, ''), k.example_en) is not null " +
    "order by d.frq asc nulls last, abs(hashtext(d.word || '" + nonceLiteral + "')) " +
    "limit " + limit + " offset " + offset
}
async function fetchBatch(offset, limit) {
  const fetched = await agent(
    '跑 Bash：PGPASSWORD=postgres psql -h localhost -p 5434 -U postgres -d postgres -t -A -F $\'\\t\' -c "' +
    buildFetchSql(limit, offset) +
    '"\n' +
    '把 stdout 每一行（格式為 word 加一個真正的 tab 字元加 example_en）組合成 **單一字串**，行與行之間用 \\n 分隔，內容原封不動照抄，不要重新排版。\n' +
    '輸出 { joined: "<word1>\\t<example_en1>\\n<word2>\\t<example_en2>...", count: <整數> }。\n' +
    '(Nonce: ' + NONCE + ' offset: ' + offset + ')',
    { label: 'fetch-missing-' + offset, schema: FetchJoinedSchema }
  )
  return parseJoinedLines(fetched && fetched.joined)
    .map(function (line) {
      const parts = line.replace(/\\t/g, '\t').split('\t')
      return { word: parts[0], example_en: parts[1] }
    })
    .filter(function (p) { return p.word && p.example_en })
}
const fetchOffsets = []
for (let o = 0; o < TARGET; o += FETCH_BATCH) fetchOffsets.push(o)
log('fetch 切成 ' + fetchOffsets.length + ' 批（每批 <= ' + FETCH_BATCH + ' 筆）')
const fetchBatchResults = await pipeline(fetchOffsets, function (offset) {
  return fetchBatch(offset, Math.min(FETCH_BATCH, TARGET - offset))
})
const rawPairs = fetchBatchResults.filter(Boolean).flat()

// ponytail: 第十輪實測 agent 轉錄 psql stdout 到 joined 字串時偶爾會重複整行
// （distinct word=1500 跟 LIMIT 對得上，但 parsed lines=1800，300 行重複）。
// 不去重會讓重複字被翻譯兩次，第二次寫入被 example_zh 安全網擋掉、白白浪費 token。
const seenWords = new Set()
const allPairs = rawPairs.filter(function (p) {
  if (seenWords.has(p.word)) return false
  seenWords.add(p.word)
  return true
})
if (allPairs.length < rawPairs.length) {
  log('fetch 結果有 ' + (rawPairs.length - allPairs.length) + ' 筆重複字（agent 轉錄時重複行），已去重')
}

log('fetched（kaikki_stage + 已有 example_en 待補 example_zh 的字）: ' + allPairs.length + ' 字（' + fetchOffsets.length + ' 批，raw ' + rawPairs.length + ' 筆）')

if (allPairs.length === 0) {
  throw new Error('沒有可處理的單字（kaikki_stage 已耗盡，且沒有待補 example_zh 的字）')
}

phase('Translate')

const chunkSize = Math.max(1, Math.min(CHUNK, allPairs.length))
const chunks = []
for (let i = 0; i < allPairs.length; i += chunkSize) chunks.push(allPairs.slice(i, i + chunkSize))
log('切成 ' + chunks.length + ' chunks')

async function translateBatch(pairs) {
  const translatedRaw = await agent(
    '你是英文例句翻譯助手。以下是 ' + pairs.length + ' 筆「英文單字＋真實英文例句」，例句已經是正確的真實語料，' +
    '**不要改寫、不要重新生成例句**，只需要把每筆的 example_en 翻譯成台灣繁體中文（禁止大陸用詞，例如要寫「網路」「滑鼠」不是「网络」「鼠标」）。' +
    ' 輸出格式：joined 欄位放全部翻譯結果，每筆一行，同一行內用一個真正的 tab 字元（ASCII 0x09 按鍵，不是反斜線加字母t的兩個文字符號）分隔兩個欄位，順序固定為 word、example_zh，' +
    ' 行與行之間用換行字元分隔。不要輸出標題列、不要 code fence、不要任何額外說明文字。' +
    ' count 欄位放實際輸出的行數。待翻譯清單（word\\texample_en）：' +
    pairs.map(function (p) { return p.word + '\t' + p.example_en }).join('\n'),
    { label: 'translate', schema: TranslateJoinedSchema }
  )
  const zhMap = new Map()
  parseJoinedLines(translatedRaw && translatedRaw.joined).forEach(function (line) {
    const parts = line.replace(/\\t/g, '\t').split('\t')
    if (parts[0] && parts[1]) zhMap.set(parts[0], parts[1])
  })
  const entries = []
  pairs.forEach(function (p) {
    const zh = zhMap.get(p.word)
    if (zh && zh.length > 0) entries.push({ word: p.word, example_en: p.example_en, example_zh: zh })
  })
  if (entries.length < pairs.length) {
    log('  chunk 漏翻 ' + (pairs.length - entries.length) + ' 筆（下次 fetch 會重新抓到，因為 dict_cache 還是空的）')
  }
  return entries
}

const chunkResults = await pipeline(chunks, function (chunk) { return translateBatch(chunk) })
let allEntries = chunkResults.filter(Boolean).flat()
allEntries = allEntries.filter(function (e) { return e && e.word && e.example_en && e.example_zh })
log('translate done: ' + allEntries.length + ' entries')

phase('Write out')

// ponytail: 這份 JSON 只是 debug dump，DB 寫入實際靠下面的 SQL apply-to-db agent。
// 筆數大時整包塞進一次 Write 會撞 64k output token 上限（n=1500 時已觀察到），超過門檻直接跳過。
const JSON_DUMP_LIMIT = 500
if (allEntries.length > JSON_DUMP_LIMIT) {
  log('跳過 debug JSON dump（' + allEntries.length + ' 筆 > ' + JSON_DUMP_LIMIT + '，避免撞 64k output token 上限，正式資料以 SQL/DB 為準）')
} else {
  await agent(
    '請用 Write tool 把以下 JSON 物件（長度 ' + allEntries.length + ' 筆）寫到 ' + OUT_JSON + '。' +
    ' 整個檔案一次寫完，不要分批也不要省略內容。' +
    ' 寫完跑 Bash: ls -la ' + OUT_JSON + ' 並回報檔案大小。\n' +
    '---\n' +
    JSON.stringify(allEntries, null, 2) + '\n---',
    { label: 'write-output-json' }
  )
  log('json writer done: ' + OUT_JSON)
}

if (allEntries.length === 0) {
  log('apply: 0 筆，跳過 SQL 與 DB 寫入')
  return { fetched: allPairs.length, translated: 0, written: 0 }
}

// ponytail: 曾經整包 1400 行塞進單一 Write agent 呼叫，撞到 64k output token 上限——
// agent 實際沒寫出內容、卻靠 verify 步驟抓到「0 行對不上預期 1400」才中止（沒有錯誤寫 DB，
// 但整輪白工）。1300 行還撐得住、1400 就爆，門檻抓不準，改成拆成多個小檔案分別寫+驗證。
const SQL_CHUNK = 250
const sqlParts = []
for (let i = 0; i < allEntries.length; i += SQL_CHUNK) sqlParts.push(allEntries.slice(i, i + SQL_CHUNK))
log('SQL 分成 ' + sqlParts.length + ' 個檔案寫（每檔 <= ' + SQL_CHUNK + ' 行，避免撞 64k output token 上限）')

function buildSqlBody(entries) {
  const rows = entries.map(function (e) {
    const w = e.word.replace(/'/g, "''")
    const en = e.example_en.replace(/'/g, "''")
    const zh = e.example_zh.replace(/'/g, "''")
    return "('" + w + "','" + en + "','" + zh + "')"
  }).join(',\n  ')
  // 安全網守 example_zh（不是 example_en）——fetch 階段本來就可能挑到 example_en 已存在
  // （例如 FreeDictionary 補過的字）只缺 example_zh 的列，這裡改守 example_zh 才不會變成 0-row UPDATE。
  return 'update dict_cache as d set\n  example_en = v.example_en,\n  example_zh = v.example_zh\nfrom (values\n  ' + rows + '\n) as v(word, example_en, example_zh)\nwhere d.word = v.word\n  and (d.example_zh is null or d.example_zh = \'\');'
}

// ponytail: 不信任 agent 自己回報「寫完了」——實測發生過 agent 沒真的覆蓋檔案、
// 卻回報成功的狀況（讓上一輪的舊檔案矇混過關）。這裡獨立驗證實際行數。
const VerifyRowsSchema = { type: 'object', properties: { row_count: { type: 'integer' } }, required: ['row_count'] }

async function writeAndVerifySqlPart(entries, partPath) {
  await agent(
    '請用 Write tool 把以下 SQL 寫到 ' + partPath + '（完整檔案、一次寫完，若檔案已存在直接覆蓋）。\n' +
    ' 寫完跑 Bash: ls -la ' + partPath + ' 並回報檔案大小。\n' +
    '---\n' +
    buildSqlBody(entries) + '\n---',
    { label: 'write-sql-file' }
  )
  const verified = await agent(
    "跑 Bash: grep -c '^  (' " + partPath + '\n' +
    '把輸出的整數透過 StructuredOutput 回傳 { row_count: <整數> }。',
    { label: 'verify-sql-file', schema: VerifyRowsSchema }
  )
  if (!verified || verified.row_count !== entries.length) {
    throw new Error(partPath + ' 寫入的行數（' + (verified && verified.row_count) + '）跟預期（' + entries.length + '）對不上，可能是舊檔案沒被覆蓋或撞到 output token 上限，中止本輪、不寫 DB')
  }
  return partPath
}

const sqlPartPaths = await pipeline(sqlParts, function (part, _item, idx) { return writeAndVerifySqlPart(part, OUT_SQL + '.p' + idx) })
log('sql writer done: ' + sqlPartPaths.length + ' 個檔案，共 ' + allEntries.length + ' 行，驗證通過')

if (!WRITE_DB) {
  log('apply: write_db=false（不寫 DB，只產 JSON+SQL）')
  return { fetched: allPairs.length, translated: allEntries.length, written: 0, sql_parts: sqlPartPaths }
}

// ponytail: 逐檔依序 apply（不平行）——雖然各檔 word 不重疊、理論上平行 UPDATE 也安全，
// 但 before/after 計數要照順序取頭尾才有意義，序列跑最簡單也最好除錯。
let totalUpdated = 0
let beforeCount = null
let afterCount = null
for (let i = 0; i < sqlPartPaths.length; i++) {
  const partPath = sqlPartPaths[i]
  const applyRaw = await agent(
    '嚴格依照以下步驟跑 psql，**不要跳過任何步驟**，完成後透過 StructuredOutput 回傳結果。\n\n' +
    '**步驟 1：寫入前計數**\n' +
    'Bash: PGPASSWORD=postgres psql -h localhost -p 5434 -U postgres -d postgres -t -A -c "select count(*) from dict_cache where example_zh is not null and example_zh <> \'\';"\n' +
    '把輸出存為 before_count（整數）。\n\n' +
    '**步驟 2：跑 UPDATE**\n' +
    'Bash: PGPASSWORD=postgres psql -h localhost -p 5434 -U postgres -d postgres -v ON_ERROR_STOP=1 -f ' + partPath + ' 2>&1\n' +
    '從輸出抓出 "UPDATE N" 的 N 當作 updated_rows。\n\n' +
    '**步驟 3：寫入後計數**\n' +
    'Bash: PGPASSWORD=postgres psql -h localhost -p 5434 -U postgres -d postgres -t -A -c "select count(*) from dict_cache where example_zh is not null and example_zh <> \'\';"\n' +
    '把輸出存為 after_count（整數）。\n\n' +
    '**步驟 4：回傳**\n' +
    '透過 StructuredOutput 回傳 { before_count, updated_rows, after_count, sql_path: "' + partPath + '" }。',
    { label: 'apply-to-db-' + i, schema: ApplyResultSchema }
  )
  log('apply part ' + i + ' done: before=' + applyRaw.before_count + ' after=' + applyRaw.after_count + ' updated=' + applyRaw.updated_rows)
  if (beforeCount === null) beforeCount = applyRaw.before_count
  afterCount = applyRaw.after_count
  totalUpdated += applyRaw.updated_rows
}

return {
  fetched: allPairs.length,
  translated: allEntries.length,
  written: totalUpdated,
  before: beforeCount,
  after: afterCount,
  out_json: OUT_JSON,
  sql_parts: sqlPartPaths,
}
