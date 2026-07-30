// EpisodesPage 與 GenerationSheet 共用的顯示工具（獨立檔案是 react-refresh 規則要求：
// 元件檔只能 export 元件）。

// LangGraph node 名稱 → 繁體中文 label。
// 來源：backend/engine/pipeline/langgraph_pod/graph.py:add_node() 的 16 個字串字面值。
// 未列在 map 裡的 node（未來新增）會 fallback 回原 snake_case，符合 CLAUDE.md
// 技術識別碼不受翻譯規則限制的條款；同時方便工程師對回 backend metric。
export const PIPELINE_NODE_LABELS: Readonly<Record<string, string>> = {
  decompose_research: '研究拆解',
  gather_evidence: '蒐集證據',
  cross_verify: '交叉驗證',
  tone_selector: '語氣選角',
  write_script: '撰寫腳本',
  failover_write_script: '改寫腳本',
  verify_script_claims: '查證內容',
  quality_judge: '品質評分',
  rewrite_iter_bump: '重寫進位',
  upsert_episode: '寫入集數',
  render_episode: '合成音檔',
  upload_artifacts: '上傳產出',
  dead_letter: '死信終止',
  update_episode_keys: '更新金鑰',
  insert_deliveries: '寫入推播',
  backfill_dict: '回填詞庫',
}

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const totalSec = Math.round(ms / 1000)
  const min = Math.floor(totalSec / 60)
  const sec = totalSec % 60
  return min > 0 ? `${min}分${sec}秒` : `${sec}秒`
}
