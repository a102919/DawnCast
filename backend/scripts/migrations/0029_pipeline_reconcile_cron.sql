-- 新增 pipeline_reconcile cron：每 10 分鐘收斂卡死超過門檻的生成任務。
--
-- 根因：worker 用 asyncio.timeout 包住整條生成流程，逾時時 CancelledError
-- 會繼承 BaseException，run_pod 原本的 except Exception 接不住，導致
-- episode_pipeline_runs 那筆 status='running' 永遠沒人收斂、channel_topics
-- 也永遠卡在 scheduled。run_pod 端已補上 CancelledError handler 即時收斂，
-- 這支 cron 只是接漏網之魚（process 被 OOM-kill、未來程式碼路徑漏寫）的兜底，
-- 跟 dawncast-order-reconcile（0024）刻意分開排程——那支只服務 daily_orders
-- 個人點餐，跟頻道/選題生成是本專案一貫要求解耦的兩個子系統。
do $migration$
declare
    v_old_job_id bigint;
begin
    -- rerun-safe：先清掉同名 job 再 schedule，避免 migration 重跑產生多個 job。
    for v_old_job_id in
        select jobid from cron.job where jobname = 'dawncast-pipeline-reconcile'
    loop
        perform cron.unschedule(v_old_job_id);
    end loop;

    perform cron.schedule(
        'dawncast-pipeline-reconcile',
        '*/10 * * * *',
        $cron$ select pgmq.send('control', jsonb_build_object('task', 'pipeline_reconcile')) $cron$
    );
end
$migration$;
