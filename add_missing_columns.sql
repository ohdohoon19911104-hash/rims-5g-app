-- 不足している日報カラムだけを追加します。
-- 既存のテーブル・データ・アカウントは削除しません。
BEGIN;
ALTER TABLE public.tat_data
    ADD COLUMN IF NOT EXISTS kddi_uq_tat TEXT,
    ADD COLUMN IF NOT EXISTS kddi_uq_must TEXT,
    ADD COLUMN IF NOT EXISTS req_receive_date TEXT,
    ADD COLUMN IF NOT EXISTS req_repair_in_date TEXT,
    ADD COLUMN IF NOT EXISTS ret_repaired_out_date TEXT,
    ADD COLUMN IF NOT EXISTS center_tat TEXT,
    ADD COLUMN IF NOT EXISTS center_must TEXT,
    ADD COLUMN IF NOT EXISTS countermeasure2 TEXT,
    ADD COLUMN IF NOT EXISTS du_ru_type TEXT,
    ADD COLUMN IF NOT EXISTS pba_name TEXT,
    ADD COLUMN IF NOT EXISTS pba_recv_date TEXT,
    ADD COLUMN IF NOT EXISTS pba_re_recv_date TEXT,
    ADD COLUMN IF NOT EXISTS period_return_can BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS repair_can TEXT,
    ADD COLUMN IF NOT EXISTS repair_pos TEXT,
    ADD COLUMN IF NOT EXISTS ship_status TEXT,
    ADD COLUMN IF NOT EXISTS remark TEXT,
    ADD COLUMN IF NOT EXISTS pba_in TEXT,
    ADD COLUMN IF NOT EXISTS pba_out TEXT,
    ADD COLUMN IF NOT EXISTS pba_open_close TEXT,
    ADD COLUMN IF NOT EXISTS sub_alarm TEXT,
    ADD COLUMN IF NOT EXISTS sub_date TEXT,
    ADD COLUMN IF NOT EXISTS summary_use TEXT,
    ADD COLUMN IF NOT EXISTS delay_reason TEXT DEFAULT '';

ALTER TABLE public.ledger_data
    ADD COLUMN IF NOT EXISTS is_outbound BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS period_return_can BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS delay_reason TEXT DEFAULT '';

NOTIFY pgrst, 'reload schema';
COMMIT;

-- W列の原本保存用カラムを確認します。
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'tat_data'
  AND column_name = 'repair_can';
