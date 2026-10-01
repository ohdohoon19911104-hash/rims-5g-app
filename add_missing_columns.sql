-- =====================================================================
-- RIMS 필수 마이그레이션 (반드시 실행!)
-- 실행 위치: Supabase Dashboard > SQL Editor 에서 전체 실행
--
-- 이 SQL은 이제 "필수"입니다.
--  1. 簡易版日報 업로드 시 엑셀 원본(A~AG열 전체 33개 항목)이 그대로
--     Supabase에 저장되도록 부족한 컬럼을 추가합니다.
--     (tat_service.py가 업로드 시점에 실존 컬럼을 자동 판별하므로
--      실행 직후 코드 수정/재배포 없이 전체 항목 저장이 시작됩니다)
--  2. 대장관리-수리TAT 연동 컬럼을 동일 이름으로 양쪽 테이블에 만듭니다.
--     - 期間内返却可否: period_return_can (tat_data에는 이미 있음)
--     - 遅延理由: delay_reason (ledger_data에는 이미 있음)
--  3. 대장관리의 進行中/出荷完了 화면 분리에 쓰이는 is_outbound 컬럼을
--     복구합니다 (현재 테이블에서 조회되지 않음 - 수동 작업 중 삭제된 것으로 보임)
--
-- 이미 있는 컬럼은 건드리지 않고, 없는 컬럼만 안전하게 추가합니다.
-- (몇 번을 다시 실행해도 안전합니다)
-- =====================================================================

-- ===== 簡易版日보 A~AG열 전체 저장용 (tat_data) =====
alter table public.tat_data add column if not exists kddi_uq_tat text;
alter table public.tat_data add column if not exists kddi_uq_must text;
alter table public.tat_data add column if not exists req_receive_date text;
alter table public.tat_data add column if not exists req_repair_in_date text;
alter table public.tat_data add column if not exists ret_repaired_out_date text;
alter table public.tat_data add column if not exists center_tat text;
alter table public.tat_data add column if not exists center_must text;
alter table public.tat_data add column if not exists countermeasure2 text;
alter table public.tat_data add column if not exists du_ru_type text;
alter table public.tat_data add column if not exists pba_name text;
alter table public.tat_data add column if not exists pba_recv_date text;
alter table public.tat_data add column if not exists pba_re_recv_date text;
alter table public.tat_data add column if not exists repair_pos text;
alter table public.tat_data add column if not exists ship_status text;
alter table public.tat_data add column if not exists remark text;
alter table public.tat_data add column if not exists pba_in text;
alter table public.tat_data add column if not exists pba_out text;
alter table public.tat_data add column if not exists pba_open_close text;
alter table public.tat_data add column if not exists sub_alarm text;
alter table public.tat_data add column if not exists sub_date text;
alter table public.tat_data add column if not exists summary_use text;
alter table public.tat_data add column if not exists delay_reason text;

-- ===== 대장관리 기능 정상화 + 수리TAT 연동용 (ledger_data) =====
alter table public.ledger_data add column if not exists is_outbound boolean default false;
alter table public.ledger_data add column if not exists period_return_can boolean default false;
