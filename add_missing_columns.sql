-- =====================================================================
-- RIMS 추가 컬럼 마이그레이션 (선택 사항 / 옵션)
-- 실행 위치: Supabase Dashboard > SQL Editor
--
-- 이 SQL은 "선택 사항"입니다. 실행하지 않아도 현재 수정본 코드는
-- 정상 동작합니다 (실존 컬럼만 자동 판별해서 저장).
--
-- 실행하면 하는 일:
--  1. 簡易版日報 업로드 시 현재 DB에 없는 확장 필드(입고일, KDDI/UQ MUST,
--     PBA 정보 등 21개)까지 자동으로 저장 대상에 포함됩니다.
--     (tat_service.py가 업로드 시점에 실존 컬럼을 자동 판별하므로
--      코드 수정/재배포 없이 즉시 반영됩니다)
--  2. 대장관리의 期間内返却可否 체크박스 연동이 복원됩니다.
--
-- 이미 있는 컬럼은 건드리지 않고, 없는 컬럼만 안전하게 추가합니다.
-- =====================================================================

alter table public.tat_data
  add column if not exists kddi_uq_must text;
alter table public.tat_data
  add column if not exists req_receive_date text;
alter table public.tat_data
  add column if not exists req_repair_in_date text;
alter table public.tat_data
  add column if not exists ret_repaired_out_date text;
alter table public.tat_data
  add column if not exists center_tat text;
alter table public.tat_data
  add column if not exists center_must text;
alter table public.tat_data
  add column if not exists countermeasure2 text;
alter table public.tat_data
  add column if not exists du_ru_type text;
alter table public.tat_data
  add column if not exists pba_name text;
alter table public.tat_data
  add column if not exists pba_recv_date text;
alter table public.tat_data
  add column if not exists pba_re_recv_date text;
alter table public.tat_data
  add column if not exists repair_can text;
alter table public.tat_data
  add column if not exists repair_pos text;
alter table public.tat_data
  add column if not exists ship_status text;
alter table public.tat_data
  add column if not exists remark text;
alter table public.tat_data
  add column if not exists pba_in text;
alter table public.tat_data
  add column if not exists pba_out text;
alter table public.tat_data
  add column if not exists pba_open_close text;
alter table public.tat_data
  add column if not exists sub_alarm text;
alter table public.tat_data
  add column if not exists sub_date text;
alter table public.tat_data
  add column if not exists summary_use text;

alter table public.ledger_data
  add column if not exists period_return_can boolean default false;
