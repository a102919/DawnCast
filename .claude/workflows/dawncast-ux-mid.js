export const meta = {
  name: 'dawncast-ux-mid',
  description: '中期 UX 評估性研究：付費用戶 3 個月後的黏著性研究 + 可用性摩擦點。動態選角 + Affinity Mapping + 任務型可用性測試 + Nielsen 啟發式 + SUS + 行為埋點建議。',
  phases: [
    { title: '動態選角', detail: '研究總監選角（沿用 early 的 _cast.json）+ 繼承 proto-persona' },
    { title: '利害關係人訪談', detail: 'PM/創辦人視角：留存目標與黏著力假設' },
    { title: '付費用戶訪談 + 競品互動', detail: '付費 Pro 3 個月訂閱者黏著性訪談 + 量化問卷 + 競品互動借鑑，並行' },
    { title: 'Affinity Mapping', detail: '跨用戶主題歸納：痛點叢集 + 功能需求訊號 + 黏著動因 + 流失風險' },
    { title: '可用性評估', detail: '任務型可用性測試 + Nielsen 啟發式 + first-click + SUS 預演' },
    { title: '綜合分析', detail: 'persona 升級 + 同理心 + 旅程 + 用戶流程 + IA 驗證' },
    { title: '最終規劃書', detail: '整合輸出 Markdown 報告 + 行為分析埋點清單' },
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
const OUTPUT_DIR = `${OUTPUT_BASE}/mid`
const CAST_PATH = `${OUTPUT_BASE}/_cast.json`
const EARLY_PERSONA_PATH = `${OUTPUT_BASE}/early/proto-personas.md`
const reuseCast = !(A.reuseCast === false)
const projectType = A.projectType || ''
const FRONTEND_SRC = A.frontendSrc || '/Users/alan/Desktop/code/DawnCast/frontend/src'
const BASE_URL = A.baseUrl || ''   // 給了就開真實瀏覽器實測；沒給則退回 cognitive walkthrough
const SHOT_DIR = `${OUTPUT_DIR}/usability-shots`

const DEFAULT_BRIEF = `
【DawnCast 產品現況 — 供所有 agent 使用】

定位：AI 生成英語 Podcast 訂閱 SaaS，台灣 CEFR B1 中級學習者。
主持人：AI 雙人對話（Alex & Sarah），每集約 3 分鐘。內容頻率：週更 2 集（週三/週六）。

━━ 畫面流程（具體 UI） ━━
【首頁】集數卡列表（封面 + 標題 + CEFR 標籤 + 時長）；Free 只見最新 1 集，其餘有鎖頭點擊進付費頁；卡片下顯示主題分類。
【播放器頁】標題 + CEFR；進度條；控制欄（←15s / 播放暫停 / →15s / 速度 0.75~1.5x）；字幕區（英文大字當前句高亮 + 中文小字）；點擊英文單字 → 查詞 popup（單字 + IPA + 詞性 + 中文釋義 + 加入單字本）。
【單字本頁】收藏列表（單字 + 詞性 + 釋義 + 來源集數）；搜尋欄；「開始閃卡複習」；Pro 可匯出 CSV/Anki。
【閃卡複習頁】SRS 間隔重複；正面英文單字，翻面顯示音標/詞性/釋義/例句；「認識/不認識」調整間隔。
【方案頁】月/年切換；Free vs Pro 比較卡；升級按鈕（目前模擬，點了直接變 Pro）。
【設定頁】字幕字型大小；播放速度預設；主題（淺/深/跟隨）；訂閱管理。
【底部導航】首頁 / 單字本 / 方案 / 設定（4 tab）。

━━ 訂閱方案 ━━
Free：每週最新 1 集、每集查詞 3 次、單字本上限 50。
Pro：NT$149/月 或 NT$1,299/年 → 完整曲庫、無限查詞、無限單字本、SRS 閃卡、AB 段落重複、Anki/CSV 匯出、離線下載。

━━ 技術現況 ━━
MVP 前端已建（React 19 + TypeScript + Tailwind），所有資料為 mock data，pre-launch，尚無真實付費用戶。
`

const projectBrief = A.projectBrief || DEFAULT_BRIEF

// ═══════════════════════════════════════════════════════════
// 模型分級
// ═══════════════════════════════════════════════════════════

const M_REASON = 'opus'     // 選角、Affinity Mapping、最終規劃書
const M_WORK = 'sonnet'     // 訪談/競品角色扮演、可用性走查、啟發式、persona/同理心/旅程/流程合成
const M_MECH = 'haiku'      // 讀檔摘要、SUS/問卷填表、格式化寫檔

// ═══════════════════════════════════════════════════════════
// 共用 Schema + 選角 helper
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
        properties: {
          id: { type: 'string' }, name: { type: 'string' }, description: { type: 'string' }, focusQuestion: { type: 'string' },
        },
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
你是 UX 研究總監。任務：為下列專案決定這次研究的「選角」——要訪談哪幾種用戶、分析哪些競品、訪談哪些利害關係人。

【專案說明】
${projectBrief}
${projectType ? `\n【指定專案類型】：${projectType}` : '\n【專案類型】：未指定，請自行判定。'}
【研究階段】：${stageHint}

步驟 1：${reuseCast
    ? `先用 Read 讀 ${CAST_PATH}。若存在且為含 personas 的合法 JSON → 原樣回傳並把 reused 設 true（確保 early→mid→late 用同一批人物）。否則進入步驟 2。`
    : '已指定不沿用，直接生成新選角。'}
步驟 2：判定專案類型；推導主要用戶區隔；依區隔差異**動態決定 persona 人數**（典型 3-6，非固定）；每個 persona 要有姓名/年齡/職業/背景/說話風格/使用情境/segmentRationale，且真的隨專案類型不同；挑 4-6 個相關競品（各一個 focusQuestion）；列利害關係人角色與 emphasizedDeliverables。
步驟 3：${reuseCast ? '若為新生成，' : ''}用 Write 把選角結果寫入 ${CAST_PATH}。

回傳符合 schema 的 JSON。
`, { schema: CASTING_SCHEMA, label: '研究總監選角', phase: '動態選角', model: M_REASON })
}

// ═══════════════════════════════════════════════════════════
// 黏著性訪談問題（付費 Pro 訂閱者 3 個月後）
// ═══════════════════════════════════════════════════════════

const INTERVIEW_QUESTIONS = `
前提：你已付費訂閱 DawnCast Pro 3 個月，今天還在用。現在接受 UX 研究訪談。

1. 你平均每週打開 DawnCast 幾次？在什麼時間點、什麼情境下打開？（通勤？睡前？）
2. 決定付費那一刻，最後是哪個體驗或時機「推」你按下去的？
3. 訂閱後你真的用到哪些 Pro 功能？（完整曲庫、無限查詞、SRS 閃卡、AB 段落重複、Anki/CSV 匯出、離線下載）哪些你完全沒碰過？
4. SRS 閃卡你現在還在用嗎？如果停了，大概哪一天停的、什麼原因讓你停？
5. 有沒有哪一集讓你「哦，這個很有幫助」——具體是什麼內容、操作、還是哪個功能？
6. 你覺得自己英文有變好嗎？你怎麼判斷自己有沒有進步？app 有給你進度感嗎？
7. 最近 2 週有沒有哪天「本來該打開但沒打開」？是什麼原因讓你跳過了？
8. 有沒有「差點想取消訂閱」的念頭？是什麼情況、最後為什麼沒取消？
9. 除了 DawnCast 你還在用什麼英語學習工具？時間分配怎麼樣？DawnCast 在你的學習組合裡是什麼角色？
10. 你有推薦過 DawnCast 給朋友嗎？用什麼理由推薦、或為什麼沒推薦？
11. 如果 DawnCast 下週關服，你最想念的是哪一個具體功能（不是「整個 app」）？
12. 現有功能裡最讓你挫折或覺得「怎麼這麼笨」的操作是哪一個？
13. 有沒有你很想要但 DawnCast 現在沒有的功能？（這裡可以說新功能，盡量具體）
14. 以現在的使用頻率和體驗，下個月你還會續訂嗎？什麼條件會讓你不續？
`

// ═══════════════════════════════════════════════════════════
// Schema：訪談 / 問卷 / 競品 / Affinity / 可用性 / SUS / 分析埋點 / 綜合 / 報告
// ═══════════════════════════════════════════════════════════

const INTERVIEW_SCHEMA = {
  type: 'object',
  properties: {
    personaId: { type: 'string' },
    usageFrequency: { type: 'string', description: '每週打開幾次、什麼情境' },
    payTrigger: { type: 'string', description: '最後促成付費的那個時機/體驗' },
    proFeaturesActuallyUsed: { type: 'array', items: { type: 'string' }, description: '真的在用的 Pro 功能' },
    proFeaturesNeverUsed: { type: 'array', items: { type: 'string' }, description: '付了錢但完全沒碰的功能' },
    srsStatus: { type: 'string', description: '閃卡現在還在用/停了/從沒用' },
    progressPerceptionGap: { type: 'string', description: '覺得自己有進步嗎、app 有沒有給進度感' },
    churnMoment: { type: 'string', description: '差點取消的時機與最後留下的原因' },
    renewalIntent: { type: 'string', description: '下個月會不會續訂、取消條件' },
    existingFeatureFrictions: { type: 'array', items: { type: 'string' }, description: '現有功能哪個操作步驟讓用戶挫折（指向畫面）' },
    emotionalLowPoints: { type: 'array', items: { type: 'string' } },
    surprisingDelights: { type: 'array', items: { type: 'string' } },
    featureRequests: { type: 'array', items: { type: 'string' }, description: '用戶明確說出的新功能需求（盡量具體，保留原話）' },
    representativeQuote: { type: 'string' },
    wouldRecommend: { type: 'boolean' },
    answers: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          questionIndex: { type: 'number' }, answer: { type: 'string' }, sentiment: { type: 'string' },
          uiSpecificInsight: { type: 'string', description: '指向哪個具體畫面或操作的問題' },
        },
        required: ['questionIndex', 'answer', 'sentiment', 'uiSpecificInsight'],
      },
    },
  },
  required: ['personaId', 'usageFrequency', 'payTrigger', 'proFeaturesActuallyUsed', 'proFeaturesNeverUsed', 'churnMoment', 'renewalIntent', 'existingFeatureFrictions', 'featureRequests', 'representativeQuote', 'wouldRecommend', 'answers'],
}

const SURVEY_SCHEMA = {
  type: 'object',
  properties: {
    personaId: { type: 'string' },
    quantitative: {
      type: 'object',
      properties: {
        weeklyOpenFrequency: { type: 'number', description: '每週開啟次數（實際數字）' },
        habitStrength: { type: 'number', description: '1-10，幾分能代表你「有習慣用」' },
        subtitleReadability: { type: 'number' },
        wordPopupUsefulness: { type: 'number' },
        vocabBookUsability: { type: 'number' },
        flashcardEffectiveness: { type: 'number' },
        navigationIntuitive: { type: 'number' },
        progressFeedbackSatisfaction: { type: 'number', description: '1-10，app 給你的進度感有多滿意' },
        overallUXSmoothness: { type: 'number' },
        priceFairness: { type: 'number', description: 'NT$149/月 值不值' },
        renewalLikelihood: { type: 'number', description: '1-10，下個月續訂可能性' },
        npsProxy: { type: 'number', description: '0-10 推薦可能性' },
      },
      required: ['weeklyOpenFrequency', 'habitStrength', 'subtitleReadability', 'wordPopupUsefulness', 'vocabBookUsability', 'flashcardEffectiveness', 'navigationIntuitive', 'progressFeedbackSatisfaction', 'overallUXSmoothness', 'priceFairness', 'renewalLikelihood', 'npsProxy'],
    },
    qualitative: {
      type: 'object',
      properties: {
        biggestFrictionPoint: { type: 'string' },
        mostStickyFeature: { type: 'string', description: '最讓你留下來繼續用的那個功能' },
        mostAnnoyingExistingDesign: { type: 'string', description: '現有設計最讓你想改掉的一個' },
        topFeatureRequest: { type: 'string', description: '最想要的新功能，盡量具體' },
        comparedTo: { type: 'string' },
        elevatorPitch: { type: 'string' },
      },
      required: ['biggestFrictionPoint', 'mostStickyFeature', 'mostAnnoyingExistingDesign', 'topFeatureRequest', 'elevatorPitch'],
    },
  },
  required: ['personaId', 'quantitative', 'qualitative'],
}

const COMPETITOR_SCHEMA = {
  type: 'object',
  properties: {
    competitorId: { type: 'string' },
    overallThreatLevel: { type: 'string' },
    taiwanFitScore: { type: 'number' },
    topStrengths: { type: 'array', items: { type: 'string' } },
    topWeaknesses: { type: 'array', items: { type: 'string' } },
    moat: { type: 'string' },
    retentionMechanism: { type: 'string', description: '這個競品靠什麼讓用戶持續回來（習慣迴圈/通知/社群/進度系統）' },
    concreteInteractionToAdapt: { type: 'string', description: '一個 DawnCast 可直接借鑑的具體 UI 互動（非功能）' },
    userBehaviorInsight: { type: 'string', description: '揭示本產品設計盲點的真實行為模式' },
    biggestLesson: { type: 'string' },
  },
  required: ['competitorId', 'overallThreatLevel', 'retentionMechanism', 'concreteInteractionToAdapt', 'userBehaviorInsight', 'biggestLesson'],
}

const AFFINITY_SCHEMA = {
  type: 'object',
  properties: {
    painPointClusters: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          theme: { type: 'string' },
          frequency: { type: 'number', description: '幾位用戶提到（分子）' },
          totalRespondents: { type: 'number', description: '總受訪人數（分母）' },
          representativeQuotes: { type: 'array', items: { type: 'string' } },
          affectedPersonaIds: { type: 'array', items: { type: 'string' } },
          uiLocation: { type: 'string', description: '指向哪個畫面或操作' },
          severity: { type: 'number', description: '0-4 嚴重度' },
        },
        required: ['theme', 'frequency', 'representativeQuotes', 'uiLocation', 'severity'],
      },
    },
    stickinessDrivers: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          driver: { type: 'string', description: '讓付費用戶回來的核心動因（具體功能/情境/心理）' },
          frequency: { type: 'number' },
          representativeQuotes: { type: 'array', items: { type: 'string' } },
          designImplication: { type: 'string', description: '這個動因對現有設計的啟示' },
        },
        required: ['driver', 'frequency', 'representativeQuotes', 'designImplication'],
      },
    },
    featureRequestClusters: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          request: { type: 'string', description: '功能需求主題（歸納多人說法）' },
          frequency: { type: 'number', description: '幾位用戶提到' },
          rationale: { type: 'string', description: '用戶為什麼想要這個（JTBD）' },
          representativeQuotes: { type: 'array', items: { type: 'string' } },
          affectedPersonaIds: { type: 'array', items: { type: 'string' } },
          buildComplexity: { type: 'string', description: '粗估實作難度：low/medium/high' },
        },
        required: ['request', 'frequency', 'rationale', 'representativeQuotes', 'buildComplexity'],
      },
    },
    churnRiskSignals: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          signal: { type: 'string', description: '哪個行為模式或說法暗示流失風險' },
          frequency: { type: 'number' },
          affectedPersonaIds: { type: 'array', items: { type: 'string' } },
          mitigationHint: { type: 'string', description: '針對現有設計可做的緩解方向' },
        },
        required: ['signal', 'frequency', 'mitigationHint'],
      },
    },
  },
  required: ['painPointClusters', 'stickinessDrivers', 'featureRequestClusters', 'churnRiskSignals'],
}

const USABILITY_SCHEMA = {
  type: 'object',
  properties: {
    testMode: { type: 'string', description: "'live-browser' 或 'cognitive-walkthrough'" },
    appReachable: { type: 'boolean' },
    tasks: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          taskName: { type: 'string' },
          goal: { type: 'string' },
          expectedHappyPath: { type: 'array', items: { type: 'string' } },
          actualObservations: { type: 'array', items: { type: 'string' } },
          predictedFrictionPoints: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                step: { type: 'string' }, issue: { type: 'string' },
                severity: { type: 'number' }, observed: { type: 'boolean' },
              },
              required: ['step', 'issue', 'severity'],
            },
          },
          firstClickPrediction: { type: 'string' },
          screenshotPath: { type: 'string' },
          completionStatus: { type: 'string' },
        },
        required: ['taskName', 'goal', 'expectedHappyPath', 'predictedFrictionPoints', 'firstClickPrediction'],
      },
    },
  },
  required: ['tasks', 'testMode'],
}

const HEURISTIC_SCHEMA = {
  type: 'object',
  properties: {
    heuristic: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          screen: { type: 'string' }, issue: { type: 'string' },
          severity: { type: 'number' }, recommendation: { type: 'string' },
        },
        required: ['screen', 'issue', 'severity', 'recommendation'],
      },
    },
  },
  required: ['heuristic', 'findings'],
}

const SUS_SCHEMA = {
  type: 'object',
  properties: {
    personaId: { type: 'string' },
    itemScores: { type: 'array', items: { type: 'number' } },
    susScore: { type: 'number' },
    caveat: { type: 'string' },
  },
  required: ['personaId', 'itemScores', 'susScore', 'caveat'],
}

const ANALYTICS_SCHEMA = {
  type: 'object',
  properties: {
    coreRetentionEvents: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          eventName: { type: 'string', description: 'snake_case 事件名（如 word_lookup_fired）' },
          trigger: { type: 'string', description: '什麼用戶操作觸發' },
          keyProperties: { type: 'array', items: { type: 'string' }, description: '要帶的屬性（如 episode_id, cefr_level）' },
          whyItMatters: { type: 'string', description: '這個事件能驗證哪個黏著性假設' },
        },
        required: ['eventName', 'trigger', 'keyProperties', 'whyItMatters'],
      },
    },
    retentionFunnels: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          funnelName: { type: 'string' },
          steps: { type: 'array', items: { type: 'string' }, description: '依序的事件名稱' },
          dropOffHypothesis: { type: 'string', description: '預期哪一步流失最多、原因' },
        },
        required: ['funnelName', 'steps', 'dropOffHypothesis'],
      },
    },
    dashboardKPIs: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          metric: { type: 'string', description: 'KPI 名稱（如 D7 retention、weekly_active_ratio）' },
          definition: { type: 'string' },
          targetHypothesis: { type: 'string', description: '沒有真實 baseline 前的暫定目標' },
        },
        required: ['metric', 'definition', 'targetHypothesis'],
      },
    },
  },
  required: ['coreRetentionEvents', 'retentionFunnels', 'dashboardKPIs'],
}

const PERSONAS_SCHEMA = {
  type: 'object',
  properties: {
    personas: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          personaId: { type: 'string' }, name: { type: 'string' }, tagline: { type: 'string' },
          dataInformed: { type: 'boolean' },
          upgradedFromProto: { type: 'string' },
          primaryMotivation: { type: 'string' },
          habitLoop: { type: 'string', description: '這個 persona 的使用習慣迴圈（觸發→行動→獎勵）' },
          goals: { type: 'array', items: { type: 'string' } },
          dawncastFrustrations: { type: 'array', items: { type: 'string' } },
          dawncastDelights: { type: 'array', items: { type: 'string' } },
          churnRisk: { type: 'string', description: '這個 persona 最可能流失的情境' },
          topFeatureRequest: { type: 'string' },
          willRenewIfCondition: { type: 'string' },
          representativeQuote: { type: 'string' },
          sourceUserIds: { type: 'array', items: { type: 'string' } },
        },
        required: ['personaId', 'name', 'tagline', 'dataInformed', 'primaryMotivation', 'habitLoop', 'dawncastFrustrations', 'churnRisk', 'representativeQuote', 'sourceUserIds'],
      },
    },
  },
  required: ['personas'],
}

const EMPATHY_MAP_SCHEMA = {
  type: 'object',
  properties: {
    empathyMaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          personaId: { type: 'string' }, personaName: { type: 'string' },
          sees: { type: 'array', items: { type: 'string' } }, hears: { type: 'array', items: { type: 'string' } },
          thinks: { type: 'array', items: { type: 'string' } }, does: { type: 'array', items: { type: 'string' } },
          painPoints: { type: 'array', items: { type: 'string' } }, gains: { type: 'array', items: { type: 'string' } },
        },
        required: ['personaId', 'personaName', 'sees', 'thinks', 'does', 'painPoints', 'gains'],
      },
    },
  },
  required: ['empathyMaps'],
}

const JOURNEY_MAP_SCHEMA = {
  type: 'object',
  properties: {
    journeyMaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          personaId: { type: 'string' }, personaName: { type: 'string' },
          criticalMoment: { type: 'string' }, overallArc: { type: 'string' },
          stages: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                stageName: { type: 'string' },
                userActions: { type: 'array', items: { type: 'string' } },
                thoughts: { type: 'string' },
                emotionScore: { type: 'number' }, emotionLabel: { type: 'string' },
                painPoints: { type: 'array', items: { type: 'string' } },
                opportunities: { type: 'array', items: { type: 'string' } },
              },
              required: ['stageName', 'userActions', 'emotionScore', 'painPoints', 'opportunities'],
            },
          },
        },
        required: ['personaId', 'personaName', 'criticalMoment', 'stages'],
      },
    },
  },
  required: ['journeyMaps'],
}

const FLOW_SCHEMA = {
  type: 'object',
  properties: {
    informationArchitectureReview: {
      type: 'object',
      properties: {
        currentNav: { type: 'array', items: { type: 'string' } },
        iaProblems: { type: 'array', items: { type: 'string' } },
      },
      required: ['currentNav', 'iaProblems'],
    },
    flows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          flowName: { type: 'string' }, trigger: { type: 'string' },
          estimatedCompletionRate: { type: 'string' },
          steps: { type: 'array', items: { type: 'string' } },
          dropOffRisks: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                step: { type: 'string' }, reason: { type: 'string' }, mitigation: { type: 'string' },
              },
              required: ['step', 'reason', 'mitigation'],
            },
          },
        },
        required: ['flowName', 'trigger', 'steps', 'dropOffRisks'],
      },
    },
  },
  required: ['flows'],
}

const REPORT_SCHEMA = {
  type: 'object',
  properties: {
    executiveSummary: { type: 'array', items: { type: 'string' } },
    stickinessInsights: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          insight: { type: 'string' }, evidence: { type: 'string' }, designAction: { type: 'string' },
        },
        required: ['insight', 'evidence', 'designAction'],
      },
    },
    topPainPoints: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          rank: { type: 'number' }, painPoint: { type: 'string' }, severity: { type: 'string' }, specificUILocation: { type: 'string' },
        },
        required: ['rank', 'painPoint', 'specificUILocation'],
      },
    },
    featureRequestsRanked: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          rank: { type: 'number' }, request: { type: 'string' }, frequency: { type: 'number' },
          jtbd: { type: 'string' }, buildComplexity: { type: 'string' },
        },
        required: ['rank', 'request', 'frequency', 'jtbd', 'buildComplexity'],
      },
    },
    p0Recommendations: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          item: { type: 'string' }, rationale: { type: 'string' }, effort: { type: 'string' }, isExistingFeatureImprovement: { type: 'boolean' },
        },
        required: ['item', 'rationale', 'isExistingFeatureImprovement'],
      },
    },
    p1Recommendations: {
      type: 'array',
      items: {
        type: 'object',
        properties: { item: { type: 'string' }, rationale: { type: 'string' }, effort: { type: 'string' } },
        required: ['item', 'rationale'],
      },
    },
    coreAssumptions: { type: 'array', items: { type: 'string' } },
    realUserResearchPlan: { type: 'string' },
  },
  required: ['executiveSummary', 'stickinessInsights', 'topPainPoints', 'featureRequestsRanked', 'p0Recommendations', 'coreAssumptions'],
}

// ═══════════════════════════════════════════════════════════
// Phase 0：動態選角 + 繼承 early proto-persona
// ═══════════════════════════════════════════════════════════

phase('動態選角')
log('Phase 0：選角（沿用 early 的 _cast.json）...')

const casting = await runCasting('mid（中期 · 評估性研究 · 黏著性）')
const personas = (casting?.personas || []).filter(Boolean)
const competitors = (casting?.competitors || []).filter(Boolean)
log(`選角完成（${casting?.reused ? '沿用 early' : '新生成'}）：${personas.length} persona、${competitors.length} 競品。`)

const earlyInheritance = await agent(`
你的任務：嘗試讀取上一階段（early 探索研究）的 proto-persona，作為本階段「升級」的依據。
用 Read 工具讀 ${EARLY_PERSONA_PATH}。
- 若存在：摘要其中每個 proto-persona 的 id/名稱/主要 JTBD/待驗證假設，回傳 found=true。
- 若不存在或讀取失敗：回傳 found=false，summary 留空字串。不要捏造。
`, {
  schema: {
    type: 'object',
    properties: {
      found: { type: 'boolean' },
      summary: { type: 'string' },
    },
    required: ['found', 'summary'],
  },
  label: '繼承 early proto-persona',
  phase: '動態選角',
  model: M_MECH,
})
log(earlyInheritance?.found ? '已繼承 early proto-persona，將升級為 validated persona。' : '無 early proto-persona，將以付費用戶訪談資料建立 persona。')
const protoInheritanceNote = earlyInheritance?.found
  ? `\n【繼承自 early 的 proto-persona（請升級為 validated，不要從零造）】\n${earlyInheritance.summary}`
  : ''

const trialCtx = (p) => p.context || p.trialContext || '日常通勤或睡前使用'

// ═══════════════════════════════════════════════════════════
// Phase 1：利害關係人訪談（加入留存/黏著力視角）
// ═══════════════════════════════════════════════════════════

phase('利害關係人訪談')
log('Phase 1：模擬 PM/創辦人利害關係人訪談（含留存目標）...')

const stakeholderResult = await agent(`
你是這個產品的 PM 兼創辦人（2 人團隊，下班後做，技術 POC 已完成、前端已建、尚無真實付費用戶）。

${projectBrief}

請誠實具體回答以下利害關係人訪談問題，像真實創辦人帶著自己的盲點、期待和擔憂。回傳放進 answers（questionIndex + answer）：
1. 產品最初要解決什麼問題？你自己是目標用戶嗎？
2. 哪個用戶族群最可能付費，又最可能持續訂閱？
3. 你對「健康的留存率」有沒有暫定目標？（M1 續訂率/週活比例）
4. 你認為哪個 Pro 功能最能讓用戶 3 個月後還在用——為什麼？
5. 目前的設計有沒有「習慣形成迴圈」？用戶下週還會回來的理由是什麼？
6. Free 轉 Pro 的主要障礙你認為是什麼？付費後流失的主要原因呢？
7. 雙語字幕中文放英文下方這個設計，是測過還是直覺決定？
8. 底部 4 tab（首頁/單字本/方案/設定）——方案 tab 對已訂閱用戶有什麼用？
9. 你最擔心的競爭對手靠什麼讓用戶黏著？DawnCast 有沒有同等機制？
10. 有沒有某個現有設計「你覺得超重要、但可能沒人用」？
11. 你自己覺得 DawnCast 目前最大的 UX 缺陷是什麼？
12. 接下來 3 個月，你最想加的一個功能是什麼？為什麼這個優先？
`, {
  schema: {
    type: 'object',
    properties: {
      productVision: { type: 'string' },
      retentionTargets: { type: 'string', description: '暫定留存目標（M1 續訂率、週活等）' },
      topBusinessRisks: { type: 'array', items: { type: 'string' } },
      hypothesizedStickyFeature: { type: 'string', description: '創辦人認為最能留住用戶的功能' },
      habitLoopDesign: { type: 'string', description: '目前有沒有設計習慣迴圈，以及是什麼' },
      freeToProMainFriction: { type: 'string' },
      biggestCompetitorFear: { type: 'string' },
      knownDesignWeaknesses: { type: 'array', items: { type: 'string' } },
      topPriorityNextFeature: { type: 'string' },
      answers: { type: 'array', items: { type: 'object', properties: { questionIndex: { type: 'number' }, answer: { type: 'string' } }, required: ['questionIndex', 'answer'] } },
    },
    required: ['productVision', 'retentionTargets', 'topBusinessRisks', 'hypothesizedStickyFeature', 'habitLoopDesign', 'knownDesignWeaknesses', 'answers'],
  },
  label: 'PM/創辦人訪談',
  phase: '利害關係人訪談',
  model: M_WORK,
})
log(`利害關係人訪談完成。留存目標：${stakeholderResult?.retentionTargets}`)

// ═══════════════════════════════════════════════════════════
// Phase 2：付費用戶訪談 + 問卷（pipeline）+ 競品互動（並行）
// ═══════════════════════════════════════════════════════════

phase('付費用戶訪談 + 競品互動')
log(`Phase 2：${personas.length} 位付費 Pro 用戶黏著性訪談（訪談→問卷）+ ${competitors.length} 競品互動借鑑，並行...`)

const [userResearchResults, competitorResults] = await parallel([

  () => pipeline(
    personas,

    (p) => agent(`
你是一位台灣英語學習者，已付費訂閱 DawnCast Pro 3 個月，今天還在用。現在接受 UX 研究訪談。

你的角色：${p.name}${p.age ? `，${p.age} 歲` : ''}，${p.occupation}
背景：${p.background}
說話風格：${p.speechStyle}
使用情境：${trialCtx(p)}

【重要前提】你已付費 3 個月，親手用過播放器、字幕、查詞 popup、單字本、閃卡、底部導航。你有具體的習慣與挫折，不是猜想。你已經是付費用戶，所以你知道 Pro 功能實際上值不值那個錢。

${projectBrief}

請以你的角色誠實回答以下 14 個黏著性訪談問題：說出你每週怎麼用、哪些功能讓你留下來、哪些讓你考慮不續訂；帶入你的語氣，別像「理想用戶」。
${INTERVIEW_QUESTIONS}
`, { schema: INTERVIEW_SCHEMA, label: `訪談-${p.name}`, phase: '付費用戶訪談 + 競品互動', model: M_WORK }),

    async (interview, p) => {
      const ctx = interview
        ? `你描述的使用頻率：${interview.usageFrequency}；主要挫折：${(interview.existingFeatureFrictions || []).slice(0, 2).join('、')}；真在用的 Pro 功能：${(interview.proFeaturesActuallyUsed || []).join('、')}；功能需求：${(interview.featureRequests || []).slice(0, 2).join('、')}`
        : '請依角色設定回答。'
      const survey = await agent(`
你是 ${p.name}（${p.occupation}）。背景：${p.background}。使用情境：${trialCtx(p)}。
你已付費 DawnCast Pro 3 個月，剛完成深度訪談。${ctx}

請填寫量化問卷（符合真實感受）與開放問題：
量化：weeklyOpenFrequency（每週開啟次數，實際數字）, habitStrength（1-10 習慣強度）, subtitleReadability, wordPopupUsefulness, vocabBookUsability, flashcardEffectiveness, navigationIntuitive, progressFeedbackSatisfaction（1-10 進度感滿意度）, overallUXSmoothness, priceFairness（NT$149/月 值不值，1-10）, renewalLikelihood（下個月續訂可能性 1-10）, npsProxy（0-10）。
開放：biggestFrictionPoint, mostStickyFeature（最讓你留下來的功能）, mostAnnoyingExistingDesign, topFeatureRequest（最想要的新功能，具體），comparedTo, elevatorPitch。
`, { schema: SURVEY_SCHEMA, label: `問卷-${p.name}`, phase: '付費用戶訪談 + 競品互動', model: M_MECH })
      return { personaId: p.id, name: p.name, interview, survey }
    }
  ),

  () => parallel(competitors.map(c => () => agent(`
你是深度使用過「${c.name}」的台灣英語學習者兼 UX 分析師。
${projectBrief}
分析對象：${c.name}（${c.description}）。核心問題：${c.focusQuestion}

聚焦兩個面向輸出：
1. retentionMechanism：這個競品靠什麼讓用戶持續回來（連勝系統/社群壓力/進度儀表板/通知節奏/內容更新頻率）——這對 DawnCast 的設計有什麼啟示？
2. concreteInteractionToAdapt：一個 DawnCast 可直接借鑑的具體 UI 互動（不是功能，是操作設計細節）
3. userBehaviorInsight：用戶用這個競品的某真實行為，揭示 DawnCast 現有設計盲點
4. 其他：overallThreatLevel、taiwanFitScore、topStrengths、topWeaknesses、moat、biggestLesson
`, { schema: COMPETITOR_SCHEMA, label: `競品-${c.name}`, phase: '付費用戶訪談 + 競品互動', model: M_WORK }))),

])

const validUserResearch = (userResearchResults || []).filter(Boolean)
const validCompetitors = (competitorResults || []).filter(Boolean)
const researchData = JSON.stringify(validUserResearch)
const competitorData = JSON.stringify(validCompetitors)
log(`用戶研究 ${validUserResearch.length}/${personas.length}、競品 ${validCompetitors.length}/${competitors.length} 完成。`)

// ═══════════════════════════════════════════════════════════
// Phase 2.5：Affinity Mapping（跨用戶主題歸納）
// ═══════════════════════════════════════════════════════════

phase('Affinity Mapping')
log('Phase 2.5：Affinity Mapping——跨用戶歸納痛點叢集、黏著動因、功能需求訊號、流失風險...')

const affinityResult = await agent(`
你是 UX 研究員，剛收到 ${validUserResearch.length} 位付費 Pro 用戶（訂閱 3 個月）的深度訪談與量化問卷。

【用戶研究資料】
${researchData}

【競品留存機制借鑑】
${competitorData}

任務：用 Affinity Mapping 方法跨用戶歸納 4 個維度：

1. **痛點叢集（painPointClusters）**：哪些現有 UI 問題出現在多位用戶口中？每個叢集標 frequency（幾/幾人）、uiLocation（具體畫面）、severity（0-4）、代表性引言。

2. **黏著動因（stickinessDrivers）**：什麼讓這些付費用戶 3 個月後還在用？找「習慣觸發點」「回報感來源」「最難割捨的功能」等具體動因，每個附 designImplication（對現有設計的啟示）。

3. **功能需求叢集（featureRequestClusters）**：把所有 featureRequests 去重歸類，找頻率 ≥2 的需求群，標 JTBD（用戶為什麼想要）、buildComplexity（low/medium/high）。頻率 1 的需求若具代表性也可列入，標明 frequency=1。

4. **流失風險訊號（churnRiskSignals）**：哪些行為模式或說法暗示「可能不續訂」？（如「SRS 閃卡停了就沒有理由繼續付」「進度感不足所以不知道有沒有在進步」）每個附 mitigationHint（現有設計可做什麼緩解）。

不要憑空創造，只從訪談資料中歸納。若某維度資料不足，據實回報。
最後用 Write 把 Affinity Map 整理成 ${OUTPUT_DIR}/affinity-map.md（繁體中文，台灣用詞），先寫檔再回傳 JSON。
`, { schema: AFFINITY_SCHEMA, label: 'Affinity Mapping', phase: 'Affinity Mapping', model: M_REASON })

const affinityData = JSON.stringify(affinityResult)
log(`Affinity Mapping 完成：${(affinityResult?.painPointClusters || []).length} 痛點叢集、${(affinityResult?.stickinessDrivers || []).length} 黏著動因、${(affinityResult?.featureRequestClusters || []).length} 功能需求叢集、${(affinityResult?.churnRiskSignals || []).length} 流失風險訊號。`)

// ═══════════════════════════════════════════════════════════
// Phase 3：可用性評估（任務型 + Nielsen 啟發式 + SUS）
// ═══════════════════════════════════════════════════════════

phase('可用性評估')
log(`Phase 3：${BASE_URL ? `真實瀏覽器可用性測試（連 ${BASE_URL}）` : '任務型可用性測試（專家走查）'} + Nielsen 10 啟發式 + SUS 預演，並行...`)

const NIELSEN = [
  '系統狀態可見性', '系統與真實世界相符', '使用者控制與自由', '一致性與標準', '錯誤預防',
  '辨識而非記憶', '彈性與使用效率', '美感與極簡設計', '協助用戶辨識/診斷/回復錯誤', '說明文件與幫助',
]

const [usabilityResult, heuristicResults, susResults] = await parallel([

  // 3A：任務型可用性測試
  () => agent(BASE_URL ? `
你是可用性測試主持人，現在要**真的在瀏覽器裡操作** DawnCast 並親眼記錄用戶會卡在哪。這不是想像，是實測。

DawnCast 已在本機跑起來：${BASE_URL}

【第一步：載入 playwright 工具】
先用 ToolSearch 載入瀏覽器工具：
ToolSearch query "select:mcp__playwright__browser_navigate,mcp__playwright__browser_snapshot,mcp__playwright__browser_click,mcp__playwright__browser_type,mcp__playwright__browser_take_screenshot,mcp__playwright__browser_console_messages,mcp__playwright__browser_navigate_back"

【第二步：實測 4 個任務】依序真的走，每一步先 browser_snapshot 看當前畫面有什麼，再決定點哪：
1. 聽完第一集並查 3 個生字（首頁 → 選集 → 播放 → 字幕點擊 → 查詞 popup → 加入單字本）
2. 從單字本進入閃卡複習做一輪（**確認路由是否真的存在**、按鈕是否可用、SRS 邏輯是否運作）
3. 找「學習進度」或「自己累積了什麼」——看 app 有沒有任何進度呈現（無則記錄「找不到」）
4. 觸發免費限制到完成升級 Pro（踩 Free 上限，看升級提示時機/形式）

【務必】每個任務至少截圖一張關鍵卡點存到 ${SHOT_DIR}/，screenshotPath 填路徑。actualObservations 寫**你實際看到的具體事實**。testMode 填 'live-browser'；若載不進來 appReachable=false 並退回走查。
` : `
你是可用性測試主持人。本機沒有起 server，改用「專家走查」（cognitive walkthrough）為 4 個任務預測用戶會卡在哪。

${projectBrief}

【紅線】聚焦「流程能不能完成、哪裡卡」，不糾結像素或文案顏色。testMode 填 'cognitive-walkthrough'，observed 一律 false。

4 個任務：
1. 聽完第一集並查 3 個生字（含發現字幕可點擊、加入單字本流程）
2. 從單字本進入閃卡複習做一輪（確認路由/按鈕/SRS 邏輯可用性）
3. 找「學習進度」或「自己累積了什麼」——app 有沒有進度呈現，用戶找不到會怎樣
4. 免費限制觸發到升級 Pro

每任務輸出：expectedHappyPath、predictedFrictionPoints（step/issue/severity 0-4）、firstClickPrediction、completionStatus。
`, { schema: USABILITY_SCHEMA, label: BASE_URL ? '真實瀏覽器可用性測試' : '任務型可用性測試（走查）', phase: '可用性評估', model: BASE_URL ? M_REASON : M_WORK }),

  // 3B：Nielsen 10 啟發式（5 agent 各 2 條）
  () => parallel([0, 1, 2, 3, 4].map(i => () => agent(`
你是可用性專家，負責用 Nielsen 啟發式評估中的這兩條原則檢視 DawnCast 現有 UI：
- 原則 A：${NIELSEN[i * 2]}
- 原則 B：${NIELSEN[i * 2 + 1]}

${projectBrief}

逐畫面（首頁/播放器/查詞 popup/單字本/閃卡/方案頁/設定/底部導航）檢視是否違反這兩條原則。
特別關注「黏著性相關」的問題——進度感缺失、獎勵回饋不足、習慣觸發缺位是否在這兩條原則下有違反。
每個 finding：screen / issue / severity（0-4）/ recommendation。heuristic 欄位填「${NIELSEN[i * 2]} + ${NIELSEN[i * 2 + 1]}」。
`, { schema: HEURISTIC_SCHEMA, label: `啟發式-${NIELSEN[i * 2]}`, phase: '可用性評估', model: M_WORK }))),

  // 3C：SUS 量表預演（付費 Pro 用戶視角）
  () => parallel(personas.map(p => () => agent(`
你是 ${p.name}（${p.occupation}），已付費訂閱 DawnCast Pro 3 個月。請依你真實的付費使用體驗填寫標準 SUS 量表 10 題（每題 1-5，奇數題正向、偶數題反向）：
1.我想繼續使用 2.覺得過於複雜 3.覺得容易使用 4.需要技術支援才會用 5.各功能整合良好 6.太多不一致 7.多數人很快能上手 8.用起來很笨拙 9.使用時很有信心 10.要先學很多才能上手
回傳 itemScores（10 個 1-5）、換算 susScore（0-100），caveat 固定填「AI 模擬分數，僅供預演，真實 baseline 須收真人」。
`, { schema: SUS_SCHEMA, label: `SUS-${p.name}`, phase: '可用性評估', model: M_MECH }))),

])

const validHeuristics = (heuristicResults || []).filter(Boolean)
const validSUS = (susResults || []).filter(Boolean)
const usabilityData = JSON.stringify({ usability: usabilityResult, heuristics: validHeuristics, sus: validSUS })

await agent(`
你是 UX 研究員。請把以下可用性評估結果整理成 3 份 Markdown（繁體中文，台灣用詞）。
資料：${usabilityData}

用 Write 寫入：
- ${OUTPUT_DIR}/usability-test.md（4 個任務的卡點 + 嚴重度。依 testMode 標可信度）
- ${OUTPUT_DIR}/heuristic-eval.md（Nielsen 10 問題清單，依 severity 由高到低排序，附修復建議）
- ${OUTPUT_DIR}/sus-baseline.md（各 persona SUS 分數表 + 平均，醒目標註「⚠️ AI 模擬，不可當真實 baseline」）
寫完回傳一句確認字串。
`, { label: '可用性產物寫檔', phase: '可用性評估', model: M_MECH })

log(`可用性評估完成：啟發式 ${validHeuristics.length} 組、SUS ${validSUS.length} 份。`)

// ═══════════════════════════════════════════════════════════
// Phase 4：綜合分析（含 Affinity 資料）
// ═══════════════════════════════════════════════════════════

phase('綜合分析')
log('Phase 4：persona 升級 + 同理心 + 旅程 + 用戶流程/IA，並行合成...')

const [personasResult, empathyMapsResult, journeyMapsResult, flowResult] = await parallel([

  () => agent(`
你是 UX 研究員。以下是 ${validUserResearch.length} 位付費 Pro 用戶（3 個月訂閱）的訪談+問卷：
${researchData}

【Affinity Mapping 結果】
${affinityData}
${protoInheritanceNote}

【框架】找「付費用戶的習慣迴圈、黏著動因、流失風險」——比 early 階段更具體，有真實使用 3 個月的行為。
任務一：${earlyInheritance?.found ? '把 early 的 proto-persona 升級為 data-informed validated persona（標 dataInformed=true、upgradedFromProto）' : '由付費用戶訪談資料合理合併建立 validated persona（dataInformed=true）'}，產出 3-4 張。
每張必須包含 habitLoop（觸發→行動→獎勵，具體到哪個 UI 操作）、churnRisk（什麼情況會不續訂）、topFeatureRequest（這個 persona 最想要的新功能）。
任務二：用 Write 寫到 ${OUTPUT_DIR}/personas/（每張一檔 P{n}-persona.md），先寫檔再回傳 JSON。
`, { schema: PERSONAS_SCHEMA, label: 'persona 升級', phase: '綜合分析', model: M_WORK }),

  () => agent(`
你是 UX 研究員。付費用戶訪談資料：${researchData}
Affinity Mapping：${affinityData}
為 validated persona（3-4 張）各建同理心地圖（sees/hears/thinks/does/painPoints/gains），聚焦付費使用 3 個月的具體心理與行為——特別是「為什麼繼續付」和「什麼讓他猶豫」。
用 Write 寫到 ${OUTPUT_DIR}/empathy-maps.md，先寫檔再回傳 JSON。
`, { schema: EMPATHY_MAP_SCHEMA, label: '同理心地圖', phase: '綜合分析', model: M_WORK }),

  () => agent(`
你是 UX 研究員。付費用戶訪談+問卷：${researchData}
Affinity Mapping：${affinityData}
為 validated persona（3-4 張）各建旅程地圖，6 階段（emotionScore 1-5）：awareness/consideration/first_use/habit_formation/renewal_decision/churn_risk。
每階段 userActions/thoughts/emotionScore/emotionLabel/painPoints（指向畫面）/opportunities（改善現有設計，非新功能）。criticalMoment 要指向「最決定性的留存或流失時刻」。
用 Write 寫到 ${OUTPUT_DIR}/journey-maps.md，先寫檔再回傳 JSON。
`, { schema: JOURNEY_MAP_SCHEMA, label: '旅程地圖', phase: '綜合分析', model: M_WORK }),

  () => agent(`
你是 UX 設計師，熟悉 DawnCast 真實 UI 結構：
${projectBrief}
用戶洞察：${researchData}
Affinity Mapping：${affinityData}

【紅線】分析現有流程「哪裡斷掉或讓用戶跳出習慣迴圈」，不是設計理想流程。
任務一：資訊架構檢視（currentNav + iaProblems，特別關注已付費用戶的導覽體驗）。
任務二：分析 4 個關鍵流程（first_listen / save_vocab / srs_review / renewal_decision），每個列 steps、estimatedCompletionRate、dropOffRisks（mitigation 為改善現有設計）。
任務三：用 Write 寫到 ${OUTPUT_DIR}/user-flows.md，先寫檔再回傳 JSON。
`, { schema: FLOW_SCHEMA, label: '用戶流程+IA', phase: '綜合分析', model: M_WORK }),

])

log('綜合分析完成，生成最終規劃書 + 行為分析埋點清單...')

// ═══════════════════════════════════════════════════════════
// Phase 5：最終規劃書 + 行為分析埋點（並行）
// ═══════════════════════════════════════════════════════════

phase('最終規劃書')

const [finalReport, analyticsResult] = await parallel([

  () => agent(`
你是資深 UX 策略師，為 DawnCast（2 人團隊，pre-launch）撰寫中期評估性研究的最終規劃書。

【研究框架】評估性研究——以付費 Pro 用戶 3 個月使用為視角，洞察黏著性機制與 UX 品質問題。
【選角】${JSON.stringify({ type: casting?.projectTypeAssessment, emphasized: casting?.emphasizedDeliverables })}
【利害關係人】${JSON.stringify(stakeholderResult)}
【付費用戶研究（${validUserResearch.length} 位）】${researchData}
【Affinity Mapping】${affinityData}
【競品留存機制】${competitorData}
【可用性評估（任務卡點/Nielsen/SUS）】${usabilityData}
【Personas】${JSON.stringify(personasResult)}
【旅程關鍵時刻】${JSON.stringify((journeyMapsResult?.journeyMaps || []).map(j => ({ persona: j.personaName, moment: j.criticalMoment })))}
【流程風險 + IA 問題】${JSON.stringify(flowResult)}

任務一：用 Write 寫入 ${OUTPUT_DIR}/ux-research-report.md，結構（繁體中文，台灣用詞）：

# DawnCast UX 研究報告（中期 · 評估性研究）

> ⚠️ 研究方法聲明：本報告由 AI agent 角色扮演模擬「付費 Pro 訂閱 3 個月」的使用者研究，並非真實用戶研究。
> 所有洞察為假設性，須以真人研究驗證。可用性完成率與 SUS 分數尤其不可當真實 baseline。

## 執行摘要（5 條，每條指向具體 UI 問題或黏著性發現）
## 黏著性分析：誰留下來、為什麼、靠什麼（stickinessInsights，附 designAction）
## 付費用戶功能採用率矩陣（Pro 功能 × persona，哪些功能沒人用）
## 功能需求排行（featureRequestsRanked，附頻率/JTBD/buildComplexity）
## 用戶痛點排行 Top 5（每條附 specificUILocation）
## 可用性評估摘要（任務卡點 + Nielsen 高嚴重度問題）
## 競品留存機制借鑑（每競品 1 條 retentionMechanism → DawnCast 現有設計啟示）
## 資訊架構與用戶流程風險
## 產品建議（P0 立即修復 / P1 三個月內，標 isExistingFeatureImprovement）
## 下一步真人研究計畫

任務二：回傳符合 schema 的 JSON 摘要。
`, { schema: REPORT_SCHEMA, label: '最終規劃書', phase: '最終規劃書', model: M_REASON }),

  () => agent(`
你是產品分析工程師，負責為 DawnCast 設計行為分析埋點清單，讓真人研究上線後能驗證 AI 模擬的黏著性假設。

【產品結構】
${projectBrief}

【Affinity Mapping 中的黏著動因與流失風險】
${affinityData}

【利害關係人的留存假設】
${JSON.stringify({ retentionTargets: stakeholderResult?.retentionTargets, hypothesizedStickyFeature: stakeholderResult?.hypothesizedStickyFeature })}

任務：設計一份最小可行的行為分析埋點清單（不是「全埋」，是「最能驗證假設的 15-20 個事件」）：

1. **coreRetentionEvents**：每個事件 eventName（snake_case）、trigger（哪個用戶操作）、keyProperties（要帶的屬性，如 episode_id/cefr_level/session_length）、whyItMatters（能驗證哪個黏著性假設）。

2. **retentionFunnels**：設計 3-4 個關鍵漏斗（如 first_listen_funnel: app_open → episode_select → playback_start → 80_pct_complete → word_lookup_fired），每個附 dropOffHypothesis。

3. **dashboardKPIs**：5-8 個建議追蹤的留存 KPI（如 D7/D30 retention、weekly_active_ratio、srs_session_per_week），每個附 definition 和暫定目標假設。

最後用 Write 寫入 ${OUTPUT_DIR}/analytics-instrumentation.md（繁體中文，台灣用詞），先寫檔再回傳 JSON。
`, { schema: ANALYTICS_SCHEMA, label: '行為分析埋點清單', phase: '最終規劃書', model: M_WORK }),

])

log(`✅ 中期評估研究完成！報告：${OUTPUT_DIR}/ux-research-report.md | 埋點：${OUTPUT_DIR}/analytics-instrumentation.md`)

return {
  stage: 'mid',
  summary: finalReport,
  outputDirectory: OUTPUT_DIR,
  castPath: CAST_PATH,
  stats: {
    castReused: !!casting?.reused,
    inheritedFromEarly: !!earlyInheritance?.found,
    personaCount: personas.length,
    validUserResearch: validUserResearch.length,
    validCompetitors: validCompetitors.length,
    heuristicSets: validHeuristics.length,
    susSamples: validSUS.length,
    affinityPainClusters: (affinityResult?.painPointClusters || []).length,
    affinityStickinessDrivers: (affinityResult?.stickinessDrivers || []).length,
    featureRequestClusters: (affinityResult?.featureRequestClusters || []).length,
    churnRiskSignals: (affinityResult?.churnRiskSignals || []).length,
    analyticsEvents: (analyticsResult?.coreRetentionEvents || []).length,
  },
}
