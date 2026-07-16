export const meta = {
  name: 'dawncast-ux-late',
  description: '後期 UX 細節打磨：結構已穩定，優化微觀互動/文案/視覺一致性/無障礙/轉換。直接讀 frontend 真實程式碼稽核。',
  phases: [
    { title: '選角與繼承', detail: '沿用 _cast.json + 繼承 mid 的 validated persona/旅程/Top 痛點' },
    { title: '轉換漏斗 + A/B 計畫', detail: '定位漏斗流失點，設計 A/B 實驗（交付設計非贏家）' },
    { title: '文案 microcopy', detail: '多版本 CTA/空狀態/錯誤訊息 + 台灣正體語氣審查' },
    { title: 'WCAG 無障礙稽核', detail: '讀 frontend 元件逐條稽核 WCAG 2.x AA' },
    { title: '視覺與細節', detail: '視覺層級/5-second + 設計系統一致性 + nitpick' },
    { title: '交付規格與報告', detail: 'edge/error states 規格 + 打磨報告' },
  ],
}

// ═══════════════════════════════════════════════════════════
// 參數與輸出路徑
// ═══════════════════════════════════════════════════════════

// args 可能以 JSON 字串或物件兩種形式注入，統一容錯解析
const A = (() => {
  if (!args) return {}
  if (typeof args === 'string') { try { return JSON.parse(args) } catch { return {} } }
  return args
})()
const OUTPUT_BASE = A.outputDir || '/Users/alan/Desktop/code/DawnCast/ux-research'
const OUTPUT_DIR = `${OUTPUT_BASE}/late`
const CAST_PATH = `${OUTPUT_BASE}/_cast.json`
const MID_DIR = `${OUTPUT_BASE}/mid`
const FRONTEND_SRC = A.frontendSrc || '/Users/alan/Desktop/code/DawnCast/frontend/src'
const reuseCast = !(A.reuseCast === false)
const projectType = A.projectType || ''

const DEFAULT_BRIEF = `
【DawnCast 產品現況】
AI 生成英語 Podcast 訂閱 SaaS，台灣 CEFR B1 學習者。React 19 + TypeScript + Tailwind 4 + React Router v7。
畫面：首頁(集數卡列表+主題篩選+鎖頭付費牆) / 播放器(進度條+控制欄+雙語字幕高亮+點擊查詞 popup) /
單字本(列表+搜尋+篩選+付費牆 50 字上限) / 閃卡複習(SRS) / 方案頁(月年切換+Free/Pro 比較) /
設定頁(字幕字級/語速/主題/訂閱管理) / 進度頁(三卡統計)。底部導航 4 tab。
Free：每週 1 集、每集查詞 3 次、單字本 50 字。Pro：NT$149/月，無限。
全 mock data，pre-launch。前端原始碼在 ${FRONTEND_SRC}。
`

const projectBrief = A.projectBrief || DEFAULT_BRIEF

// ═══════════════════════════════════════════════════════════
// 模型分級：預設便宜模型，只有需要綜合/判斷/讀真實碼稽核的才用 opus
// ═══════════════════════════════════════════════════════════

const M_REASON = 'opus'     // 思考：選角、漏斗+A/B 實驗設計、最終報告
const M_AUDIT = 'opus'      // 讀真實程式碼的高可信度稽核（WCAG）——正字率不妥協
const M_WORK = 'sonnet'     // 中階：microcopy、視覺/設計系統稽核、交付規格
const M_MECH = 'haiku'      // 機械：讀檔摘要繼承

// ═══════════════════════════════════════════════════════════
// 共用 Schema + 選角 helper（與 early/mid 一致）
// ═══════════════════════════════════════════════════════════

const CASTING_SCHEMA = {
  type: 'object',
  properties: {
    reused: { type: 'boolean' },
    projectTypeAssessment: { type: 'string' },
    targetSegments: { type: 'array', items: { type: 'string' } },
    personas: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' }, name: { type: 'string' }, age: { type: 'number' },
          occupation: { type: 'string' }, background: { type: 'string' },
          speechStyle: { type: 'string' }, context: { type: 'string' }, segmentRationale: { type: 'string' },
        },
        required: ['id', 'name', 'occupation', 'background', 'speechStyle', 'segmentRationale'],
      },
    },
    competitors: {
      type: 'array',
      items: {
        type: 'object',
        properties: { id: { type: 'string' }, name: { type: 'string' }, description: { type: 'string' }, focusQuestion: { type: 'string' } },
        required: ['id', 'name', 'description', 'focusQuestion'],
      },
    },
    stakeholderRoles: { type: 'array', items: { type: 'string' } },
    emphasizedDeliverables: { type: 'array', items: { type: 'string' } },
    rationale: { type: 'string' },
  },
  required: ['reused', 'projectTypeAssessment', 'targetSegments', 'personas', 'competitors', 'stakeholderRoles', 'rationale'],
}

async function runCasting(stageHint) {
  return await agent(`
你是 UX 研究總監，為下列專案決定研究選角。
【專案說明】${projectBrief}
${projectType ? `【指定專案類型】：${projectType}` : '【專案類型】：未指定，請自行判定。'}
【研究階段】：${stageHint}

步驟 1：${reuseCast
    ? `先用 Read 讀 ${CAST_PATH}。若存在且為含 personas 的合法 JSON → 原樣回傳並把 reused 設 true（確保三階段用同一批人物）。否則進入步驟 2。`
    : '已指定不沿用，直接生成新選角。'}
步驟 2：判定專案類型；推導用戶區隔；依差異**動態決定 persona 人數**（典型 3-6，非固定）；每個 persona 含姓名/年齡/職業/背景/說話風格/情境/segmentRationale 且隨專案類型不同；挑 4-6 相關競品；列利害關係人角色與 emphasizedDeliverables。
步驟 3：${reuseCast ? '若為新生成，' : ''}用 Write 寫入 ${CAST_PATH}。
回傳符合 schema 的 JSON。
`, { schema: CASTING_SCHEMA, label: '研究總監選角', phase: '選角與繼承', model: M_REASON })
}

// ═══════════════════════════════════════════════════════════
// Schema：漏斗/AB、文案、WCAG、視覺、交付規格、報告
// ═══════════════════════════════════════════════════════════

const FUNNEL_SCHEMA = {
  type: 'object',
  properties: {
    funnelStages: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          stage: { type: 'string', description: '如 著陸→註冊→首次聽完→查詞→升級' },
          hypothesizedDropReason: { type: 'string' },
          isBiggestLeak: { type: 'boolean' },
        },
        required: ['stage', 'hypothesizedDropReason', 'isBiggestLeak'],
      },
    },
    abTests: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          testName: { type: 'string' },
          targetLeak: { type: 'string', description: '針對哪個漏斗流失點' },
          hypothesis: { type: 'string', description: '若改 X 則 Y 指標提升，因為 Z' },
          controlVariant: { type: 'string' },
          treatmentVariant: { type: 'string' },
          primaryMetric: { type: 'string' },
          guardrailMetrics: { type: 'array', items: { type: 'string' } },
          minSampleNote: { type: 'string', description: '樣本量/判讀方向（粗估，需真實流量驗證）' },
        },
        required: ['testName', 'targetLeak', 'hypothesis', 'controlVariant', 'treatmentVariant', 'primaryMetric'],
      },
    },
    credibilityNote: { type: 'string', description: '固定註明：A/B 結果須真實流量產生，此處僅交付實驗設計' },
  },
  required: ['funnelStages', 'abTests', 'credibilityNote'],
}

const MICROCOPY_SCHEMA = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          location: { type: 'string', description: '哪個畫面/元件的文案（CTA/空狀態/錯誤訊息/付費牆）' },
          currentCopyGuess: { type: 'string' },
          variants: { type: 'array', items: { type: 'string' }, description: '2-3 個改寫版本（繁體中文台灣用詞）' },
          recommendedVariant: { type: 'string' },
          toneRationale: { type: 'string', description: '語氣選擇理由（鼓勵 vs 教學、中英比例）' },
          taiwanLocalizationCheck: { type: 'string', description: '台灣正體用詞審查（揪出大陸用詞/生硬翻譯）' },
        },
        required: ['location', 'variants', 'recommendedVariant', 'toneRationale', 'taiwanLocalizationCheck'],
      },
    },
  },
  required: ['items'],
}

const WCAG_SCHEMA = {
  type: 'object',
  properties: {
    filesReviewed: { type: 'array', items: { type: 'string' }, description: '實際 Read 過的 frontend 檔案路徑' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          component: { type: 'string' },
          wcagCriterion: { type: 'string', description: '如 1.4.3 對比、2.1.1 鍵盤、4.1.2 名稱角色值' },
          level: { type: 'string', description: 'A / AA / AAA' },
          issue: { type: 'string' },
          evidence: { type: 'string', description: '程式碼依據（class/屬性/缺漏）' },
          fix: { type: 'string' },
        },
        required: ['component', 'wcagCriterion', 'level', 'issue', 'fix'],
      },
    },
    audioControlNote: { type: 'string', description: '播放器音訊控制可及性（Podcast 產品尤其重要）' },
  },
  required: ['filesReviewed', 'findings'],
}

const VISUAL_SCHEMA = {
  type: 'object',
  properties: {
    fiveSecondTest: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          screen: { type: 'string' },
          whatStandsOut: { type: 'string', description: '5 秒內最先抓住注意力的元素' },
          isPrimaryActionClear: { type: 'boolean' },
          visualHierarchyIssue: { type: 'string' },
        },
        required: ['screen', 'whatStandsOut', 'isPrimaryActionClear'],
      },
    },
    designSystemConsistency: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          inconsistency: { type: 'string', description: '間距/色彩/字級/元件樣式不一致處' },
          locations: { type: 'array', items: { type: 'string' } },
          recommendation: { type: 'string' },
        },
        required: ['inconsistency', 'recommendation'],
      },
    },
    nitpicks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          item: { type: 'string', description: 'micro-interaction/間距/動效/狀態回饋的細節問題' },
          severity: { type: 'string', description: 'low/medium' },
          fix: { type: 'string' },
        },
        required: ['item', 'fix'],
      },
    },
  },
  required: ['fiveSecondTest', 'designSystemConsistency', 'nitpicks'],
}

const HANDOFF_SCHEMA = {
  type: 'object',
  properties: {
    edgeCases: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          screen: { type: 'string' },
          state: { type: 'string', description: 'empty/loading/error/offline/max-limit 等狀態' },
          currentHandling: { type: 'string', description: '現有處理（讀碼推斷）' },
          recommendedSpec: { type: 'string', description: '建議的狀態規格與文案' },
        },
        required: ['screen', 'state', 'recommendedSpec'],
      },
    },
  },
  required: ['edgeCases'],
}

const LATE_REPORT_SCHEMA = {
  type: 'object',
  properties: {
    executiveSummary: { type: 'array', items: { type: 'string' } },
    quickWins: { type: 'array', items: { type: 'string' }, description: '低成本高影響的微觀優化' },
    accessibilityCritical: { type: 'array', items: { type: 'string' }, description: '必修的 WCAG A/AA 缺失' },
    abTestBacklog: { type: 'array', items: { type: 'string' } },
    polishChecklist: { type: 'array', items: { type: 'string' } },
  },
  required: ['executiveSummary', 'quickWins', 'accessibilityCritical', 'polishChecklist'],
}

// ═══════════════════════════════════════════════════════════
// Phase 0：選角 + 繼承 mid（不重做 persona/旅程）
// ═══════════════════════════════════════════════════════════

phase('選角與繼承')
log('Phase 0：選角（沿用 _cast.json）+ 繼承 mid 的 validated persona / 旅程 / Top 痛點...')

const casting = await runCasting('late（後期 · 細節打磨）')
log(`選角完成（${casting?.reused ? '沿用' : '新生成'}）：${(casting?.personas || []).length} persona。`)

const midInheritance = await agent(`
你的任務：讀取上一階段（mid 評估研究）的成果，作為本階段「打磨對象」的輸入。本階段**不重做** persona/旅程地圖，只沿用。
依序用 Read 嘗試讀：
- ${MID_DIR}/ux-research-report.md（找 Top 痛點與 P0/P1 建議）
- ${MID_DIR}/journey-maps.md（找各 persona critical moment 與情緒低點）
- ${MID_DIR}/user-flows.md（找最高掉出風險與 IA 問題）

摘要回傳：validatedPersonas（已驗證人物名單）、topPainPoints（最該打磨的痛點，指向具體畫面）、
journeyLowPoints（情緒低點對應的畫面）、found（是否成功讀到 mid 成果）。讀不到就 found=false 且欄位留空，不要捏造。
`, {
  schema: {
    type: 'object',
    properties: {
      found: { type: 'boolean' },
      validatedPersonas: { type: 'array', items: { type: 'string' } },
      topPainPoints: { type: 'array', items: { type: 'string' } },
      journeyLowPoints: { type: 'array', items: { type: 'string' } },
    },
    required: ['found', 'topPainPoints'],
  },
  label: '繼承 mid 成果',
  phase: '選角與繼承',
  model: M_MECH,
})
log(midInheritance?.found ? `已繼承 mid：${(midInheritance.topPainPoints || []).length} 個待打磨痛點。` : '⚠️ 無 mid 成果可繼承，建議先跑 dawncast-ux-mid。後期打磨缺乏宏觀依據。')
const midNote = midInheritance?.found
  ? `\n【繼承自 mid 的待打磨重點（針對這些做微觀優化，不要翻案功能存廢）】\nTop 痛點：${JSON.stringify(midInheritance.topPainPoints)}\n情緒低點畫面：${JSON.stringify(midInheritance.journeyLowPoints || [])}`
  : '\n（無 mid 成果，以下打磨依產品現況通則進行）'

const RED_LINE = '【紅線】結構已凍結：禁止質疑「這個功能要不要存在」、禁止重做 persona/旅程地圖。只做微觀層面的優化（像素/文案/視覺/無障礙/轉換）。'

// ═══════════════════════════════════════════════════════════
// Phase 1：轉換漏斗 + A/B 計畫
// ═══════════════════════════════════════════════════════════

phase('轉換漏斗 + A/B 計畫')
log('Phase 1：定位漏斗流失點 + 設計 A/B 實驗...')

const funnelResult = await agent(`
你是成長/轉換 UX 設計師兼實驗設計師。
${projectBrief}
${midNote}
${RED_LINE}
【可信度】A/B 測試本質是真實流量行為實驗，你**無法產出贏家**。你的角色是「設計實驗」：找流失點、出假設、設計變體、定指標。

任務一：定位轉換漏斗各階段（著陸→註冊→首次聽完→首次查詞→升級 Pro），各標 hypothesizedDropReason，挑出 isBiggestLeak。
任務二：為 2-3 個最大流失點各設計一個 A/B 測試：hypothesis（若改 X 則 Y 提升，因為 Z）、controlVariant、treatmentVariant、primaryMetric、guardrailMetrics、minSampleNote。
任務三：用 Write 寫到 ${OUTPUT_DIR}/ab-test-plan.md，先寫檔再回傳 JSON。credibilityNote 固定註明「僅交付實驗設計，結果須真實流量」。
`, { schema: FUNNEL_SCHEMA, label: '漏斗+A/B 計畫', phase: '轉換漏斗 + A/B 計畫', model: M_REASON })

log(`A/B 計畫完成：${(funnelResult?.abTests || []).length} 個實驗設計。`)

// ═══════════════════════════════════════════════════════════
// Phase 2：文案 microcopy + 偏好測試
// ═══════════════════════════════════════════════════════════

phase('文案 microcopy')
log('Phase 2：microcopy 多版本 + 台灣正體語氣審查...')

const microcopyResult = await agent(`
你是 UX 文案（microcopy）專家，精通台灣正體中文語氣。
${projectBrief}
${midNote}
${RED_LINE}

針對下列高影響文案點，各給 2-3 個改寫版本 + 推薦版 + 語氣理由 + 台灣在地化審查：
- 首頁付費牆鎖頭提示
- 查詞 3 次用完的升級提示
- 單字本 50 字上限提示
- 單字本/進度頁的空狀態（empty state）
- 方案頁主 CTA 與年繳優惠說明
- 升級成功 / 一般錯誤訊息

【在地化要求】揪出任何大陸用詞或生硬翻譯，改成台灣正體自然說法；UI 文字全繁中、禁 emoji（改用 icon 描述）。
用 Write 寫到 ${OUTPUT_DIR}/microcopy-variants.md，先寫檔再回傳 JSON。
`, { schema: MICROCOPY_SCHEMA, label: 'microcopy', phase: '文案 microcopy', model: M_WORK })

log(`microcopy 完成：${(microcopyResult?.items || []).length} 個文案點。`)

// ═══════════════════════════════════════════════════════════
// Phase 3：WCAG 無障礙稽核（讀真實 frontend 程式碼）
// ═══════════════════════════════════════════════════════════

phase('WCAG 無障礙稽核')
log('Phase 3：讀 frontend 元件逐條稽核 WCAG 2.x AA...')

const wcagResult = await agent(`
你是無障礙（a11y）專家，依 WCAG 2.x AA 稽核 DawnCast 前端。

【務必先讀真實程式碼】用 Read/Grep/Glob 實際讀取 ${FRONTEND_SRC} 下的元件（播放器、字幕、查詞 popup、按鈕、表單、導航、付費牆等）。
filesReviewed 必須列出你真的讀過的檔案路徑——不要憑空臆測。

逐條稽核常見準則：1.4.3 色彩對比、1.1.1 非文字內容替代、2.1.1 鍵盤可操作、2.4.7 焦點可見、4.1.2 名稱/角色/值（ARIA）、1.4.4 文字縮放、字幕區可讀性等。
每個 finding：component / wcagCriterion / level(A/AA/AAA) / issue / evidence（程式碼依據）/ fix。
特別檢查 audioControlNote：播放/暫停/速度/進度條等音訊控制的鍵盤與 screen reader 可及性（Podcast 產品關鍵）。

${RED_LINE}
用 Write 寫到 ${OUTPUT_DIR}/wcag-audit.md（依 level A→AA 排序），先寫檔再回傳 JSON。
`, { schema: WCAG_SCHEMA, label: 'WCAG 稽核', phase: 'WCAG 無障礙稽核', model: M_AUDIT })

log(`WCAG 稽核完成：讀 ${(wcagResult?.filesReviewed || []).length} 檔、${(wcagResult?.findings || []).length} 個缺失。`)

// ═══════════════════════════════════════════════════════════
// Phase 4：視覺層級 / 5-second test / 設計系統一致性 / nitpick
// ═══════════════════════════════════════════════════════════

phase('視覺與細節')
log('Phase 4：視覺層級 + 5-second test + 設計系統一致性 + 細節 nitpick...')

const visualResult = await agent(`
你是視覺/互動設計師。可用 Read/Grep 查看 ${FRONTEND_SRC} 的 Tailwind class 與元件樣式，評估視覺與一致性。
${projectBrief}
${midNote}
${RED_LINE}

任務一：5-second test 啟發式——對 5 個關鍵畫面（首頁/播放器/單字本/方案頁/進度頁）評估 5 秒內最先抓注意力的元素、主要行動是否清楚、視覺層級問題（對照 F/Z 掃視、Gestalt）。
任務二：設計系統一致性稽核——找間距/色彩/字級/元件樣式不一致處（附 locations 與建議）。
任務三：細節 nitpick 清單——micro-interaction、動效、狀態回饋、間距等小問題（severity low/medium + fix）。
用 Write 寫到 ${OUTPUT_DIR}/visual-nitpick.md，先寫檔再回傳 JSON。
`, { schema: VISUAL_SCHEMA, label: '視覺+設計系統+nitpick', phase: '視覺與細節', model: M_WORK })

log(`視覺評估完成：${(visualResult?.nitpicks || []).length} 個 nitpick、${(visualResult?.designSystemConsistency || []).length} 個一致性問題。`)

// ═══════════════════════════════════════════════════════════
// Phase 5：設計交付規格（edge/error states）+ 打磨報告
// ═══════════════════════════════════════════════════════════

phase('交付規格與報告')
log('Phase 5：edge/error states 交付規格 + 打磨報告...')

const handoffResult = await agent(`
你是 UX 設計師，為工程交付補齊「邊界狀態與錯誤狀態」規格。可用 Read 查看 ${FRONTEND_SRC} 推斷現有處理。
${projectBrief}
${RED_LINE}
為各畫面列出 empty/loading/error/offline/max-limit 等狀態：currentHandling（讀碼推斷現有）、recommendedSpec（建議規格與文案）。
重點畫面：首頁(載入/無集數)、播放器(緩衝/載入失敗)、單字本(空/已滿)、查詞(查無此字/額度用完)、方案頁(付款失敗模擬)。
用 Write 寫到 ${OUTPUT_DIR}/handoff-edge-states.md，先寫檔再回傳 JSON。
`, { schema: HANDOFF_SCHEMA, label: '交付規格', phase: '交付規格與報告', model: M_WORK })

const finalReport = await agent(`
你是資深 UX 設計師，為 DawnCast 後期打磨撰寫總結報告。

【研究框架】後期細節打磨——只動微觀層面（像素/文案/視覺/無障礙/轉換），不翻案功能存廢、不重做 persona。
【繼承自 mid】${JSON.stringify(midInheritance)}
【漏斗+A/B】${JSON.stringify(funnelResult)}
【microcopy】${JSON.stringify(microcopyResult)}
【WCAG】${JSON.stringify(wcagResult)}
【視覺/設計系統/nitpick】${JSON.stringify(visualResult)}
【交付規格】${JSON.stringify(handoffResult)}

任務一：用 Write 寫到 ${OUTPUT_DIR}/polish-report.md，結構（繁體中文，台灣用詞）：

# DawnCast UX 打磨報告（後期 · 細節優化）

> ⚠️ 研究方法聲明：本報告由 AI agent 進行（WCAG/視覺/設計系統稽核直接讀真實程式碼，可信度較高；
> A/B 測試與 SUS 等需真實流量/真人驗證的部分僅交付「設計」與「假設」，不可當結果）。

## 執行摘要
## Quick Wins（低成本高影響，可立即做）
## 無障礙必修項（WCAG A/AA，附程式碼依據）
## 文案優化建議（含台灣在地化）
## 視覺與設計系統一致性
## A/B 測試 backlog（僅設計，待真實流量）
## 邊界/錯誤狀態交付規格
## 打磨檢查清單（可勾選）

任務二：回傳符合 schema 的 JSON。
`, { schema: LATE_REPORT_SCHEMA, label: '打磨報告', phase: '交付規格與報告', model: M_REASON })

log(`✅ 後期打磨研究完成！報告：${OUTPUT_DIR}/polish-report.md`)

return {
  stage: 'late',
  summary: finalReport,
  outputDirectory: OUTPUT_DIR,
  castPath: CAST_PATH,
  stats: {
    castReused: !!casting?.reused,
    inheritedFromMid: !!midInheritance?.found,
    abTests: (funnelResult?.abTests || []).length,
    microcopyItems: (microcopyResult?.items || []).length,
    wcagFilesReviewed: (wcagResult?.filesReviewed || []).length,
    wcagFindings: (wcagResult?.findings || []).length,
    nitpicks: (visualResult?.nitpicks || []).length,
  },
}
