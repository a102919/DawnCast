#!/bin/bash
# 翻譯回填例句工作流啟動器 (Sharded Parallel Backfill Launcher)

SCRIPT="/Users/alan/.claude/projects/-Users-alan-Desktop-code-DawnCast/4340adb2-1340-4abe-b4a6-d8905a50dc4b/workflows/scripts/dict-kaikki-translate-backfill.js"
TOTAL_WORKERS=${1:-5}  # 預設為 5，可傳參調整

run_worker() {
  local i=$1
  local total_workers=$2
  local worker_id=$((i - 1))
  local nonce="$(date +%s)_w${i}"
  local args_json="{\"n\": 1500, \"chunk\": 100, \"write_db\": true, \"nonce\": \"$nonce\", \"workers\": $total_workers, \"worker_id\": $worker_id}"
  
  echo "[Worker $i/$total_workers] nonce=$nonce (worker_id=$worker_id) 已在背景啟動..."
  
  # CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 確保執行超過 10 分鐘時，不會被 claude 強殺
  CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 claude --dangerously-skip-permissions -p "請執行 workflow \"$SCRIPT\"，引數為 '$args_json'" > "/tmp/backfill_${nonce}.log" 2>&1 &
}

echo "開始平行跑 $TOTAL_WORKERS 個分片 Worker 進行翻譯回填..."
echo "翻譯日誌將寫入到 /tmp/backfill_<nonce>.log"
echo "--------------------------------------------------"

for ((i=1; i<=TOTAL_WORKERS; i++)); do
  run_worker $i $TOTAL_WORKERS
  sleep 2
done

echo "正在等待所有 Worker 執行完畢（這可能需要 10 ~ 15 分鐘）..."
wait
echo "所有 Worker 執行結束！"
