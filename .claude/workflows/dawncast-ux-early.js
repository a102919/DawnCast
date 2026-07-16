export const meta = {
  name: 'dawncast-ux-early',
  description: '初期 UX 探索/生成性研究：產品還沒在跑時，發現痛點與 JTBD、驗證值不值得做。動態選角，不評估任何具體 UI。',
  phases: [
    { title: '動態選角', detail: '研究總監依專案類型生成 persona/競品/利害關係人' },
    { title: '假設與利害關係人', detail: '商業假設盤點 + 最高風險假設(RAT) + 利害關係人訪談' },
    { title: '問題訪談 + 競品機會', detail: '生成性訪談(JTBD 四力) + 競品機會縫隙，並行' },
    { title: '架構構思', detail: 'IA 提案 + 卡片分類模擬 + 理想 User Flow + 低保真線框規格' },
    { title: '綜合與報告', detail: 'proto-persona + 同理心 + 旅程概念 + VPC + 探索報告' },
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
const OUTPUT_DIR = `${OUTPUT_BASE}/early`
const CAST_PATH = `${OUTPUT_BASE}/_cast.json`
const reuseCast = !(A.reuseCast === false)
const projectType = A.projectType || ''        // 空 = 由研究總監自動判定
const targetModule = A.targetModule || ''       // 選配：聚焦某個未建模組

// 初期＝產品還不存在，brief 只描述「概念與假設」，不含任何畫面/UI
const DEFAULT_CONCEPT = `
【DawnCast 產品概念（pre-product，尚無可用產品）】

一句話概念：AI 生成的英語學習 Podcast 訂閱服務，主打「每天 3 分鐘，雙人對話帶你熟一個英語知識點」。
目標市場：台灣 CEFR B1 中級英語學習者。
內容設定（構想）：AI 雙人對話（非真人錄音），週更 2 集，主題涵蓋科技/商業/文化/科學。
商業模式（構想）：Free + Pro 月費訂閱（約 NT$149/月）。
團隊：2 人創業（工程 + 內容），下班後做。

【尚未驗證的核心假設（待本研究挑戰）】
- B1 用戶願意聽「AI 生成、非真人」的內容，且不覺得品質廉價。
- 「通勤零碎時間學英語」是真實且夠痛的需求，不是偽需求。
- 雙語字幕 + 點擊查詞是用戶真正缺、且願意付費的價值點。
- 訂閱制（而非單次購買/免費廣告）是這個市場能接受的收費方式。

注意：此階段產品「還沒做出來」。本研究是探索 problem space，不是評估任何介面。
${targetModule ? `\n【本輪聚焦的未建模組】：${targetModule}（針對這個還沒實作的構想做探索性研究）` : ''}
`

const projectBrief = A.projectBrief || DEFAULT_CONCEPT

// ═══════════════════════════════════════════════════════════
// 模型分級：預設用便宜模型，只有需要跨資料綜合/策略判斷的才用 opus
// ═══════════════════════════════════════════════════════════

const M_REASON = 'opus'     // 思考：選角、假設風險(RAT)、架構設計、最終報告
const M_WORK = 'sonnet'     // 中階：角色扮演訪談、競品、persona/同理心/VPC 合成
const M_MECH = 'haiku'      // 機械：純讀檔摘要、填表、格式化寫檔（本檔暫未用到）

// ═══════════════════════════════════════════════════════════
// 共用 Schema：動態選角（三支 workflow 一致）
// ═══════════════════════════════════════════════════════════

const CASTING_SCHEMA = {
  type: 'object',
  properties: {
    reused: { type: 'boolean', description: '是否沿用既有 _cast.json（true）或新生成（false）' },
    projectTypeAssessment: { type: 'string', description: '判定的專案類型 + 理由' },
    targetSegments: { type: 'array', items: { type: 'string' }, description: '推導的主要用戶區隔' },
    personas: {
      type: 'array',
      description: '依用戶區隔數動態決定人數（典型 3-6），不是固定值',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          name: { type: 'string' },
          age: { type: 'number' },
          occupation: { type: 'string' },
          background: { type: 'string' },
          speechStyle: { type: 'string' },
          context: { type: 'string', description: '這個 persona 的使用/生活情境' },
          segmentRationale: { type: 'string', description: '為什麼這個專案需要這個 persona' },
        },
        required: ['id', 'name', 'occupation', 'background', 'speechStyle', 'segmentRationale'],
      },
    },
    competitors: {
      type: 'array',
      description: '依專案類型挑相關競品（典型 4-6）',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          name: { type: 'string' },
          description: { type: 'string' },
          focusQuestion: { type: 'string', description: '針對這個競品最該問的一個分析問題' },
        },
        required: ['id', 'name', 'description', 'focusQuestion'],
      },
    },
    stakeholderRoles: {
      type: 'array',
      description: '這個專案相關的利害關係人角色（PM/創辦人/客服/工程/行銷…依專案）',
      items: { type: 'string' },
    },
    emphasizedDeliverables: {
      type: 'array',
      items: { type: 'string' },
      description: '依專案類型，本研究最該重視的交付物（驅動報告優先序）',
    },
    rationale: { type: 'string', description: '整體選角理由' },
  },
  required: ['reused', 'projectTypeAssessment', 'targetSegments', 'personas', 'competitors', 'stakeholderRoles', 'rationale'],
}

// ═══════════════════════════════════════════════════════════
// 共用：研究總監動態選角 agent（三支 workflow 一致的樣板）
// ═══════════════════════════════════════════════════════════

async function runCasting(stageHint) {
  return await agent(`
你是 UX 研究總監。任務：為下列專案決定這次研究的「選角」——要訪談哪幾種用戶、分析哪些競品、訪談哪些利害關係人。

【專案說明】
${projectBrief}
${projectType ? `\n【指定專案類型】：${projectType}` : '\n【專案類型】：未指定，請你依說明自行判定。'}
【研究階段】：${stageHint}

═══ 步驟 1：嘗試沿用既有選角 ═══
${reuseCast
    ? `先用 Read 工具讀取 ${CAST_PATH}。
- 若檔案存在、是合法 JSON 且含 personas 陣列 → 直接原樣回傳該內容，並把 reused 設為 true。不要重新生成（確保 early→mid→late 用同一批人物）。
- 若檔案不存在或無效 → 進入步驟 2 生成新選角。`
    : `本輪已指定不沿用（reuseCast=false），請直接進入步驟 2 生成新選角。`}

═══ 步驟 2：動態生成選角（僅在需要時） ═══
- 先判定專案類型（消費級 app / B2B SaaS / 內容訂閱 / 工具 / 電商 / 平台…），寫進 projectTypeAssessment。
- 推導這個專案真正的主要用戶區隔（targetSegments）。
- 依「區隔數量與差異」**動態決定 persona 人數**（典型 3-6，不要固定 5 個）：相似區隔合一，差異大的分開。
  每個 persona 要有具體姓名、年齡、職業、背景、說話風格、使用情境，以及「為什麼這個專案需要他」(segmentRationale)。
  persona 的人數與背景必須真的隨這個專案的類型而不同，不是套模板。
- 依專案類型挑 4-6 個**真正相關**的競品（含直接、間接、替代方案），每個給一個最該問的 focusQuestion。
- 列出這個專案相關的利害關係人角色。
- 列出這個專案最該重視的交付物（emphasizedDeliverables）。

═══ 步驟 3：寫檔 ═══
${reuseCast ? '若你是新生成（reused=false），' : ''}用 Write 工具把最終選角結果（與你回傳的 JSON 同構）寫入 ${CAST_PATH}，方便後續階段沿用。

最後回傳符合 schema 的 JSON。
`, { schema: CASTING_SCHEMA, label: '研究總監選角', phase: '動態選角', model: M_REASON })
}

// ═══════════════════════════════════════════════════════════
// 其他 Schema
// ═══════════════════════════════════════════════════════════

const ASSUMPTIONS_SCHEMA = {
  type: 'object',
  properties: {
    assumptions: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          statement: { type: 'string', description: '一條商業/用戶/技術前提假設' },
          category: { type: 'string', description: 'desirability / viability / feasibility' },
          ifWrongImpact: { type: 'string', description: '若這條錯了，對整個點子的衝擊' },
          uncertainty: { type: 'number', description: '不確定性 1-5（5=最不確定）' },
          riskScore: { type: 'number', description: '衝擊 × 不確定性的綜合風險排序分' },
        },
        required: ['id', 'statement', 'category', 'ifWrongImpact', 'uncertainty', 'riskScore'],
      },
    },
    riskiestAssumption: { type: 'string', description: '錯了就全盤皆輸、最該先驗證的那一條' },
    cheapestTestForRiskiest: { type: 'string', description: '驗證最高風險假設的最省成本方法' },
    stakeholderAnswers: {
      type: 'array',
      items: {
        type: 'object',
        properties: { question: { type: 'string' }, answer: { type: 'string' } },
        required: ['question', 'answer'],
      },
    },
  },
  required: ['assumptions', 'riskiestAssumption', 'cheapestTestForRiskiest'],
}

const PROBLEM_INTERVIEW_SCHEMA = {
  type: 'object',
  properties: {
    personaId: { type: 'string' },
    pastBehaviorStories: {
      type: 'array',
      items: { type: 'string' },
      description: '受訪者描述的「過去真實做過的事」（非未來意向）',
    },
    currentWorkarounds: { type: 'array', items: { type: 'string' }, description: '現在怎麼土法煉鋼解決這個需求' },
    realPainPoints: { type: 'array', items: { type: 'string' } },
    jtbd: {
      type: 'object',
      properties: {
        jobStatement: { type: 'string', description: '當我__，我想要__，所以我能__' },
        push: { type: 'string', description: '推力：現況的不滿' },
        pull: { type: 'string', description: '拉力：新方案的吸引' },
        anxiety: { type: 'string', description: '焦慮：採用新方案的擔憂' },
        habit: { type: 'string', description: '慣性：留在現況的力量' },
      },
      required: ['jobStatement', 'push', 'pull', 'anxiety', 'habit'],
    },
    willingnessSignal: { type: 'string', description: '是否願意改變的真實訊號（行為證據，非口頭承諾）' },
    representativeQuote: { type: 'string' },
  },
  required: ['personaId', 'pastBehaviorStories', 'currentWorkarounds', 'realPainPoints', 'jtbd', 'representativeQuote'],
}

const OPPORTUNITY_SCHEMA = {
  type: 'object',
  properties: {
    competitorId: { type: 'string' },
    whatUsersHire: { type: 'string', description: '用戶實際雇用這個競品完成什麼任務' },
    strengths: { type: 'array', items: { type: 'string' } },
    unmetGaps: { type: 'array', items: { type: 'string' }, description: '這個競品沒解決好的縫隙（＝機會）' },
    pricingModel: { type: 'string', description: '收費方式（標：價格/最新功能需真人查證）' },
    needsHumanVerification: { type: 'array', items: { type: 'string' }, description: '哪些陳述是 agent 知識、可能過期、需真人查證' },
    opportunityForUs: { type: 'string', description: '對本專案的機會切入點' },
  },
  required: ['competitorId', 'whatUsersHire', 'strengths', 'unmetGaps', 'opportunityForUs', 'needsHumanVerification'],
}

const ARCHITECTURE_SCHEMA = {
  type: 'object',
  properties: {
    informationArchitecture: {
      type: 'object',
      properties: {
        topLevelNav: { type: 'array', items: { type: 'string' }, description: '建議的頂層導覽分類' },
        cardSortingRationale: { type: 'string', description: '模擬卡片分類：為什麼這樣分群（依用戶心智模型）' },
        contentGroups: {
          type: 'array',
          items: {
            type: 'object',
            properties: { group: { type: 'string' }, items: { type: 'array', items: { type: 'string' } } },
            required: ['group', 'items'],
          },
        },
      },
      required: ['topLevelNav', 'cardSortingRationale'],
    },
    idealUserFlows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          flowName: { type: 'string' },
          goal: { type: 'string' },
          steps: { type: 'array', items: { type: 'string' } },
          criticalDecisionPoints: { type: 'array', items: { type: 'string' } },
        },
        required: ['flowName', 'goal', 'steps'],
      },
    },
    lowFiWireframes: {
      type: 'array',
      description: '低保真線框「文字規格」（版面/區塊/層級，不含視覺風格）',
      items: {
        type: 'object',
        properties: {
          screenName: { type: 'string' },
          layoutSpec: { type: 'string', description: '由上到下的區塊與資訊層級描述' },
          primaryAction: { type: 'string' },
        },
        required: ['screenName', 'layoutSpec', 'primaryAction'],
      },
    },
  },
  required: ['informationArchitecture', 'idealUserFlows', 'lowFiWireframes'],
}

const PROTO_PERSONA_SCHEMA = {
  type: 'object',
  properties: {
    personas: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          personaId: { type: 'string' },
          name: { type: 'string' },
          tagline: { type: 'string' },
          isProvisional: { type: 'boolean', description: '一律 true：proto-persona 為假設性、未經真人驗證' },
          primaryJob: { type: 'string', description: 'JTBD 主任務' },
          motivations: { type: 'array', items: { type: 'string' } },
          frustrationsWithStatusQuo: { type: 'array', items: { type: 'string' }, description: '對「現況替代方案」的挫折，非對本產品 UI 的挫折' },
          currentAlternatives: { type: 'array', items: { type: 'string' } },
          assumptionsToValidate: { type: 'array', items: { type: 'string' } },
          representativeQuote: { type: 'string' },
          sourceUserIds: { type: 'array', items: { type: 'string' } },
        },
        required: ['personaId', 'name', 'tagline', 'isProvisional', 'primaryJob', 'motivations', 'representativeQuote', 'sourceUserIds'],
      },
    },
  },
  required: ['personas'],
}

const EMPATHY_CONCEPT_SCHEMA = {
  type: 'object',
  properties: {
    empathyMaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          personaId: { type: 'string' },
          personaName: { type: 'string' },
          says: { type: 'array', items: { type: 'string' } },
          thinks: { type: 'array', items: { type: 'string' } },
          does: { type: 'array', items: { type: 'string' } },
          feels: { type: 'array', items: { type: 'string' } },
          pains: { type: 'array', items: { type: 'string' } },
          gains: { type: 'array', items: { type: 'string' } },
        },
        required: ['personaId', 'personaName', 'says', 'thinks', 'does', 'feels', 'pains', 'gains'],
      },
    },
  },
  required: ['empathyMaps'],
}

const VPC_SCHEMA = {
  type: 'object',
  properties: {
    canvases: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          personaId: { type: 'string' },
          customerJobs: { type: 'array', items: { type: 'string' } },
          pains: { type: 'array', items: { type: 'string' } },
          gains: { type: 'array', items: { type: 'string' } },
          painRelievers: { type: 'array', items: { type: 'string' }, description: '產品如何止痛' },
          gainCreators: { type: 'array', items: { type: 'string' }, description: '產品如何創造獲益' },
          productServices: { type: 'array', items: { type: 'string' } },
          fitHypothesis: { type: 'string', description: 'problem-solution fit 假設（待驗證）' },
        },
        required: ['personaId', 'customerJobs', 'pains', 'gains', 'painRelievers', 'gainCreators', 'fitHypothesis'],
      },
    },
  },
  required: ['canvases'],
}

const EARLY_REPORT_SCHEMA = {
  type: 'object',
  properties: {
    executiveSummary: { type: 'array', items: { type: 'string' } },
    validatedProblems: { type: 'array', items: { type: 'string' }, description: '研究支持「值得做」的問題' },
    riskiestAssumptions: { type: 'array', items: { type: 'string' } },
    goNoGoRecommendation: { type: 'string', description: '繼續/轉向/停止 的建議 + 理由' },
    nextValidationSteps: { type: 'array', items: { type: 'string' }, description: '下一步該用真人驗證什麼' },
    opportunityTreeSkeleton: { type: 'string', description: '機會解決方案樹骨架（期望成果→機會→構想）' },
  },
  required: ['executiveSummary', 'validatedProblems', 'riskiestAssumptions', 'goNoGoRecommendation', 'nextValidationSteps'],
}

// ═══════════════════════════════════════════════════════════
// Phase 0：動態選角
// ═══════════════════════════════════════════════════════════

phase('動態選角')
log('Phase 0：研究總監依專案類型動態生成 persona / 競品 / 利害關係人...')

const casting = await runCasting('early（初期 · 探索/生成性研究）')
const personas = (casting?.personas || []).filter(Boolean)
const competitors = (casting?.competitors || []).filter(Boolean)
log(`選角完成（${casting?.reused ? '沿用既有' : '新生成'}）：${personas.length} 位 persona、${competitors.length} 個競品。專案判定：${casting?.projectTypeAssessment || 'N/A'}`)

if (personas.length === 0) {
  log('⚠️ 選角未產出 persona，後續階段將缺乏輸入。請檢查 projectBrief。')
}

// ═══════════════════════════════════════════════════════════
// Phase 1：商業假設盤點 + 最高風險假設 + 利害關係人訪談
// ═══════════════════════════════════════════════════════════

phase('假設與利害關係人')
log('Phase 1：盤點商業假設、排序最高風險假設、訪談利害關係人...')

const assumptionsResult = await agent(`
你是這個產品的 PM 兼創辦人，同時具備精實創業（Lean Startup）的紀律。

【專案說明】
${projectBrief}

【相關利害關係人角色】：${(casting?.stakeholderRoles || []).join('、') || 'PM / 創辦人'}

【紅線】此階段產品「還沒做出來」。你不能評論任何具體畫面或 UI 設計——那些都還不存在。
你的任務是盤點「這個點子要成立的前提假設」，不是評估介面。

任務一：盤點 8-12 條核心前提假設，分類為：
- desirability（用戶想不想要 / 痛點真不真）
- viability（商業上行不行得通 / 願不願付費）
- feasibility（技術/團隊做不做得出來）
每條給 ifWrongImpact（錯了的衝擊）與 uncertainty（1-5），算出 riskScore。

任務二：挑出 riskiestAssumption（錯了就全盤皆輸、最該先驗證的那一條），
並給 cheapestTestForRiskiest（最省成本的驗證方法，如假著陸頁、預購、5 人問題訪談）。

任務三：以創辦人身分誠實回答（放進 stakeholderAnswers）：
1. 這個產品最初想解決什麼問題？你自己是目標用戶嗎？
2. 你憑什麼相信這個問題夠痛、值得做？有什麼證據還是只是直覺？
3. 哪個用戶族群最可能先付費？為什麼？
4. 最大的商業風險是什麼？
5. 如果只能先驗證一件事再決定要不要投入，你會驗證什麼？
`, { schema: ASSUMPTIONS_SCHEMA, label: '假設盤點+RAT', phase: '假設與利害關係人', model: M_REASON })

log(`最高風險假設：${assumptionsResult?.riskiestAssumption || 'N/A'}`)

// ═══════════════════════════════════════════════════════════
// Phase 2：問題訪談（生成性）+ 競品機會地圖（並行）
// ═══════════════════════════════════════════════════════════

phase('問題訪談 + 競品機會')
log(`Phase 2：${personas.length} 場生成性問題訪談 + ${competitors.length} 個競品機會分析，並行...`)

const [interviewResults, opportunityResults] = await parallel([

  () => parallel(personas.map(p => () => agent(`
你是一位真實的潛在用戶，正在接受「問題探索訪談」（problem interview）。

你的角色：
姓名：${p.name}${p.age ? `，${p.age} 歲` : ''}，${p.occupation}
背景：${p.background}
說話風格：${p.speechStyle}
${p.context ? `情境：${p.context}` : ''}

【專案概念（訪談員心中的點子，但先別讓它主導你）】
${projectBrief}

【關鍵訪談原則 — 你必須遵守】
- 只談「你過去真實做過的事」，不要承諾「未來會不會用」。研究顯示口頭意向不可信。
- 當被問到需求時，講具體事件：「上一次我想練英語聽力，我打開了 X，結果 Y」。
- 不要當「理想配合的受訪者」。如果你其實沒那麼在乎，就誠實說沒那麼在乎。
- 講出你現在怎麼土法煉鋼解決這件事（current workarounds）。

請以你的角色回答這場問題訪談，輸出：
- pastBehaviorStories：2-4 個過去真實行為的故事
- currentWorkarounds：你現在如何拼湊解決這個需求
- realPainPoints：真正的痛點（不是「我想要功能 X」）
- jtbd：用 Jobs-to-be-Done 四力框架剖析你的動機（jobStatement / push / pull / anxiety / habit）
- willingnessSignal：是否願意改變的「行為證據」（例如你已經為類似東西付過錢 / 花過時間）
- representativeQuote：一句最能代表你的話
`, { schema: PROBLEM_INTERVIEW_SCHEMA, label: `問題訪談-${p.name}`, phase: '問題訪談 + 競品機會', model: M_WORK }))),

  () => parallel(competitors.map(c => () => agent(`
你是熟悉「${c.name}」的產業/UX 分析師。

【本專案概念】
${projectBrief}

【分析對象】${c.name}：${c.description}
【核心問題】${c.focusQuestion}

請從「機會發現」角度分析（不是功能比較清單）：
- whatUsersHire：用戶實際雇用這個競品完成什麼任務（JTBD 視角）
- strengths：它真正做得好的地方
- unmetGaps：它沒解決好的縫隙（這就是本專案的機會）
- pricingModel：收費方式概述
- opportunityForUs：對本專案的機會切入點
- needsHumanVerification：把你「不完全確定、可能過期」的陳述（尤其價格、最新功能）列出來，標記需真人查證

誠實標記不確定的部分——你的競品知識可能不是最新的。
`, { schema: OPPORTUNITY_SCHEMA, label: `競品機會-${c.name}`, phase: '問題訪談 + 競品機會', model: M_WORK }))),

])

const validInterviews = (interviewResults || []).filter(Boolean)
const validOpportunities = (opportunityResults || []).filter(Boolean)
log(`問題訪談 ${validInterviews.length}/${personas.length}、競品機會 ${validOpportunities.length}/${competitors.length} 完成。`)

const interviewData = JSON.stringify(validInterviews)
const opportunityData = JSON.stringify(validOpportunities)

// ═══════════════════════════════════════════════════════════
// Phase 3：架構構思（IA + 卡片分類 + 理想 User Flow + 低保真線框）
// ═══════════════════════════════════════════════════════════

phase('架構構思')
log('Phase 3：資訊架構提案 + 卡片分類模擬 + 理想 User Flow + 低保真線框規格...')

const architectureResult = await agent(`
你是資訊架構師 / UX 設計師。基於問題訪談洞察，為這個「還沒做出來」的產品提出架構藍圖。

【專案概念】
${projectBrief}

【問題訪談洞察】
${interviewData}

【紅線】這是「提案」與「構想」，不是評估既有產品。請從用戶心智模型出發設計。

任務一：資訊架構（IA）
- topLevelNav：建議的頂層導覽分類（依用戶心智模型，不是依內部功能切）
- cardSortingRationale：模擬一次開放式卡片分類——說明你為何這樣把功能分群，反映用戶怎麼想
- contentGroups：分群結果

任務二：3-4 個理想 User Flow（理想路徑，標出關鍵決策點）

任務三：低保真線框「文字規格」（不畫圖、不談顏色/字體，只談版面區塊與資訊層級）
- 為 3-4 個關鍵畫面各寫 layoutSpec（由上到下的區塊）與 primaryAction

任務四：用 Write 工具把以上寫成 Markdown 至：
${OUTPUT_DIR}/architecture/information-architecture.md
${OUTPUT_DIR}/architecture/user-flows.md
${OUTPUT_DIR}/architecture/lowfi-wireframes.md
請先寫檔，再回傳 JSON 摘要。
`, { schema: ARCHITECTURE_SCHEMA, label: 'IA+UserFlow+線框', phase: '架構構思', model: M_REASON })

log('架構構思完成。')

// ═══════════════════════════════════════════════════════════
// Phase 4：綜合與報告（proto-persona / 同理心 / VPC 並行 → 報告）
// ═══════════════════════════════════════════════════════════

phase('綜合與報告')
log('Phase 4：proto-persona + 同理心地圖 + VPC 並行合成...')

const [protoPersonas, empathyMaps, vpcResult] = await parallel([

  () => agent(`
你是 UX 研究員。以下是 ${validInterviews.length} 場問題訪談結果：
${interviewData}

任務一：合成 proto-persona（假設性人物誌，未經真人驗證）。
- 依訪談合理合併，產出 3-4 張，每張 isProvisional 一律 true。
- 重點放在 JTBD 主任務、動機、對「現況替代方案」的挫折（不是對本產品 UI 的挫折，因為產品還不存在）、
  現有替代方案、待驗證假設、代表性引言、sourceUserIds。

任務二：用 Write 寫到 ${OUTPUT_DIR}/proto-personas.md（每張一節，明確標「假設性、待驗證」）。
請先寫檔，再回傳 JSON。
`, { schema: PROTO_PERSONA_SCHEMA, label: 'proto-persona', phase: '綜合與報告', model: M_WORK }),

  () => agent(`
你是 UX 研究員。以下是問題訪談結果：
${interviewData}

為 3-4 個合理推導的 persona 各建一張同理心地圖（says / thinks / does / feels / pains / gains）。
注意：此階段聚焦用戶在「現況」下的心理，不是使用本產品（產品還不存在）。

用 Write 寫到 ${OUTPUT_DIR}/empathy-maps.md，先寫檔再回傳 JSON。
`, { schema: EMPATHY_CONCEPT_SCHEMA, label: '同理心地圖', phase: '綜合與報告', model: M_WORK }),

  () => agent(`
你是精實創業教練。基於問題訪談與競品機會，為 3-4 個 persona 各做一張 Value Proposition Canvas。
【訪談】${interviewData}
【競品機會】${opportunityData}

每張含：customerJobs / pains / gains / painRelievers / gainCreators / productServices / fitHypothesis（problem-solution fit 假設，待驗證）。
用 Write 寫到 ${OUTPUT_DIR}/value-proposition-canvas.md，先寫檔再回傳 JSON。
`, { schema: VPC_SCHEMA, label: 'VPC', phase: '綜合與報告', model: M_WORK }),

])

log('綜合完成，生成探索報告...')

const finalReport = await agent(`
你是資深 UX 策略師，為這個 pre-product 階段的專案撰寫「探索性研究報告」。

【研究框架】本研究是 problem space 探索（generative research），目的是回答「這個點子值不值得做」，
不是評估任何介面（產品還不存在）。

【選角】${JSON.stringify({ projectType: casting?.projectTypeAssessment, segments: casting?.targetSegments, emphasized: casting?.emphasizedDeliverables })}
【商業假設 + 最高風險假設】${JSON.stringify(assumptionsResult)}
【問題訪談（${validInterviews.length} 場）】${interviewData}
【競品機會（${validOpportunities.length} 個）】${opportunityData}
【架構構想】${JSON.stringify(architectureResult)}
【proto-persona】${JSON.stringify(protoPersonas)}
【VPC】${JSON.stringify(vpcResult)}

任務一：用 Write 把完整報告寫入 ${OUTPUT_DIR}/exploratory-report.md，結構（繁體中文，台灣用詞）：

# DawnCast UX 探索性研究報告（初期 · 概念階段）

> ⚠️ 研究方法聲明：本報告由 AI agent 角色扮演模擬生成性訪談與競品分析，並非真實用戶研究。
> 所有洞察為「待驗證假設」，須以真人研究驗證。本階段不評估任何 UI（產品尚未實作）。

## 執行摘要（5 條）
## 已被研究支持「值得做」的問題
## 最高風險假設與最省成本驗證法（RAT）
## JTBD 與 proto-persona 速覽（標假設性）
## 競品機會縫隙（每條標「需真人查證」的部分）
## 資訊架構與理想流程構想
## Go / Pivot / Stop 建議（明確給出方向 + 理由）
## 下一步真人驗證計畫

任務二：回傳符合 schema 的 JSON 摘要（含 opportunityTreeSkeleton：機會解決方案樹骨架）。
`, { schema: EARLY_REPORT_SCHEMA, label: '探索報告', phase: '綜合與報告', model: M_REASON })

log(`✅ 初期探索研究完成！報告：${OUTPUT_DIR}/exploratory-report.md`)

return {
  stage: 'early',
  summary: finalReport,
  outputDirectory: OUTPUT_DIR,
  castPath: CAST_PATH,
  stats: {
    castReused: !!casting?.reused,
    personaCount: personas.length,
    competitorCount: competitors.length,
    interviewsCompleted: validInterviews.length,
    riskiestAssumption: assumptionsResult?.riskiestAssumption,
  },
}
