-- ============================================================
-- hammieboye-bot Supabase 스키마
-- CLAUDE.md의 사용자별 호감도/기억 시스템 스펙을 반영한 DDL.
-- Supabase 프로젝트의 SQL Editor에 그대로 붙여넣어 실행한다.
-- ============================================================

-- ------------------------------------------------------------
-- 0. 공통 유틸
-- ------------------------------------------------------------

-- 모든 "하루" 기준은 한국시간(KST, UTC+9) 자정이다.
-- Postgres의 now()/CURRENT_DATE는 UTC 기준이므로, 날짜 판단은
-- 반드시 이 함수를 통해서 하거나 애플리케이션에서 KST로 변환해서 넘겨야 한다.
CREATE OR REPLACE FUNCTION kst_today()
RETURNS date
LANGUAGE sql
STABLE
AS $$
  SELECT (now() AT TIME ZONE 'Asia/Seoul')::date;
$$;

-- updated_at 자동 갱신 트리거용 함수
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

-- ------------------------------------------------------------
-- 1. 감정 ENUM (CLAUDE.md 확정본, 긍정 10 + 부정 10)
-- ------------------------------------------------------------

CREATE TYPE emotion AS ENUM (
  -- 긍정
  '신남', '행복함', '반가움', '호기심', '뿌듯함',
  '편안함', '장난스러움', '기대됨', '애정 느낌', '황홀함',
  -- 부정
  '화남', '짜증남', '경계함', '무서움', '슬픔',
  '서운함', '당황함', '지루함', '의심스러움', '절망함'
);

-- ------------------------------------------------------------
-- 2. users — 영구 수집 항목 (CLAUDE.md 섹션 1-1)
--    Discord user ID 기준, 서버 무관 통합 레코드.
-- ------------------------------------------------------------

CREATE TABLE users (
  user_id                 bigint PRIMARY KEY,           -- Discord user ID (snowflake)
  chat_count               bigint NOT NULL DEFAULT 0,    -- 채팅 횟수 (누적)
  help_count                bigint NOT NULL DEFAULT 0,    -- 도와준 횟수 (누적)
  affection                 bigint NOT NULL DEFAULT 10,   -- 호감도 (하한 없음, 시작값 10)

  consent_given              boolean NOT NULL DEFAULT false,
  consent_at                 timestamptz,

  -- 병 던지기(3-1) 쿨타임은 날짜 경계를 넘나들 수 있어 daily_stats가 아닌
  -- 여기(영구 테이블)에 둔다. 실패했을 때만 값이 채워진다.
  plastic_cooldown_until     timestamptz,

  -- 동의 전에도 저장되는 최소 식별 기록 (고지 불필요, CLAUDE.md 1-1 참고)
  first_seen_at              timestamptz NOT NULL DEFAULT now(),

  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

-- ------------------------------------------------------------
-- 3. daily_stats — 구간(세션) 수집 항목 (CLAUDE.md 섹션 1-2)
--    사용자 x 날짜(KST) 조합마다 한 행. "리셋"은 별도 삭제 없이
--    날짜가 바뀌면 새 행을 만드는 방식으로 자연스럽게 처리한다.
-- ------------------------------------------------------------

CREATE TABLE daily_stats (
  user_id                        bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  stat_date                       date NOT NULL DEFAULT kst_today(),

  -- 당일 획득 호감도. daily_gain은 "얻은 양"만 누적(+20 상한 계산용, 항상 0 이상).
  -- daily_net은 하락분까지 반영한 순증감(음수 가능, 3-5의 "당일 획득 호감도 음수" 제외 판정용).
  daily_gain                       integer NOT NULL DEFAULT 0,
  daily_net                         integer NOT NULL DEFAULT 0,
  gain_methods                      jsonb NOT NULL DEFAULT '[]'::jsonb, -- 당일 획득 방법 목록 (예: ["plastic_bottle", "call_event"])

  messages_today                    integer NOT NULL DEFAULT 0,     -- 오늘 나눈 대화 수 (3-5 최다 대화자 판정용)

  -- 병 던지기(3-1) 관련 카운터
  plastic_streak                    integer NOT NULL DEFAULT 0,     -- 연속 성공 횟수 (실패 시 0으로 리셋)
  plastic_success_claimed           boolean NOT NULL DEFAULT false, -- 오늘 성공 보상(+1) 획득 여부
  plastic_streak_bonus_claimed      boolean NOT NULL DEFAULT false, -- 오늘 연속 3회 보너스(+3) 획득 여부

  -- 감정 반응(3-3, 4-3) 관련 카운터
  happy_emotion_claimed             boolean NOT NULL DEFAULT false, -- 오늘 행복 감정 보상(+1) 획득 여부
  negative_emotion_streak           integer NOT NULL DEFAULT 0,     -- 부정 감정 연속 발생 횟수
  negative_emotion_daily_count      integer NOT NULL DEFAULT 0,     -- 부정 감정 당일 누적 발생 횟수

  -- 쿨타임 남용(4-5) 카운터 — 이벤트별로 집계 (예: {"plastic_bottle": 2})
  cooldown_abuse_counts              jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at                         timestamptz NOT NULL DEFAULT now(),
  updated_at                         timestamptz NOT NULL DEFAULT now(),

  PRIMARY KEY (user_id, stat_date)
);

CREATE TRIGGER trg_daily_stats_updated_at
  BEFORE UPDATE ON daily_stats
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_daily_stats_date ON daily_stats (stat_date);

-- ------------------------------------------------------------
-- 4. chat_history — 히스토리 대화 내용 (CLAUDE.md 섹션 1-2, 30분/최대 50개)
--    보관 기간·개수 제한은 애플리케이션에서 조회 시점에
--    (created_at > now() - interval '30 minutes') 조건 + LIMIT 50 으로 강제한다.
--    오래된 행은 주기적으로 정리(cron)하거나, 조회 조건으로만 걸러도 무방하다.
-- ------------------------------------------------------------

CREATE TABLE chat_history (
  id                  bigserial PRIMARY KEY,
  user_id              bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  guild_id              bigint,                 -- 참고용 (판정 로직 자체는 user_id 기준, 서버 무관)
  content                text NOT NULL,
  role                   text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'assistant')),
  detected_emotion       emotion,                -- 감정 판정 결과 (판정 안 됐으면 NULL)
  created_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_chat_history_user_recent ON chat_history (user_id, created_at DESC);

-- ------------------------------------------------------------
-- 5. global_call_events — 글로벌 부름 이벤트 (CLAUDE.md 섹션 3-2)
--    "가장 먼저 반응한 1명"을 원자적으로 판정하기 위한 클레임 테이블.
-- ------------------------------------------------------------

CREATE TABLE global_call_events (
  id               bigserial PRIMARY KEY,
  prompt_text        text NOT NULL,             -- 게시된 메시지 원문
  posted_at           timestamptz NOT NULL DEFAULT now(),
  expires_at           timestamptz NOT NULL,      -- posted_at + 10분

  claimed_by           bigint REFERENCES users(user_id), -- 가장 먼저 반응한 사용자 (NULL = 아직 없음)
  claimed_at            timestamptz,
  reward_amount          integer,                   -- 1~10 랜덤 지급량 (클레임 시 확정)

  -- 서버별로 게시된 메시지 위치. 다른 서버 메시지를 수정할 때 사용.
  -- 예: {"111111111111111111": {"channel_id": "222...", "message_id": "333..."}}
  messages               jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_global_call_events_active ON global_call_events (expires_at) WHERE claimed_by IS NULL;

-- ------------------------------------------------------------
-- 6. 원자적 호감도 증감 RPC (CLAUDE.md 섹션 9-1-1 대응)
--    상승/하락 이벤트 발생 시 애플리케이션은 UPDATE를 직접 하지 말고
--    이 함수를 호출한다. 행 잠금(FOR UPDATE)으로 동시 요청이 들어와도
--    +20 일일 상한이 절대 뚫리지 않는다.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION add_affection(
  p_user_id bigint,
  p_amount  integer,           -- 양수(획득) 또는 음수(하락)
  p_method  text DEFAULT NULL  -- 획득 방법 식별자 (하락일 땐 NULL이어도 됨)
)
RETURNS TABLE (applied_amount integer, new_affection bigint, new_daily_gain integer)
LANGUAGE plpgsql
AS $$
DECLARE
  v_stat_date date := kst_today();
  v_current_gain integer;
  v_applied integer;
BEGIN
  -- 오늘자 daily_stats 행이 없으면 생성
  INSERT INTO daily_stats (user_id, stat_date)
  VALUES (p_user_id, v_stat_date)
  ON CONFLICT (user_id, stat_date) DO NOTHING;

  -- 동시 요청 직렬화를 위한 행 잠금
  SELECT daily_gain INTO v_current_gain
  FROM daily_stats
  WHERE user_id = p_user_id AND stat_date = v_stat_date
  FOR UPDATE;

  IF p_amount > 0 THEN
    -- 오늘 이미 채운 만큼을 빼고 남은 여유분만큼만 적용 (부분 지급)
    v_applied := LEAST(p_amount, GREATEST(20 - v_current_gain, 0));
  ELSE
    -- 하락에는 상한이 없다
    v_applied := p_amount;
  END IF;

  UPDATE daily_stats
  SET daily_gain = daily_gain + GREATEST(v_applied, 0),
      daily_net = daily_net + v_applied,
      gain_methods = CASE
        WHEN v_applied > 0 AND p_method IS NOT NULL
          THEN gain_methods || to_jsonb(p_method)
        ELSE gain_methods
      END
  WHERE user_id = p_user_id AND stat_date = v_stat_date;

  UPDATE users
  SET affection = affection + v_applied
  WHERE user_id = p_user_id;

  RETURN QUERY
  SELECT v_applied,
         (SELECT affection FROM users WHERE user_id = p_user_id),
         (SELECT daily_gain FROM daily_stats WHERE user_id = p_user_id AND stat_date = v_stat_date);
END;
$$;

-- ------------------------------------------------------------
-- 7. 글로벌 부름 이벤트 원자적 클레임 RPC (CLAUDE.md 섹션 9-1-2 대응)
--    여러 서버에서 동시에 응답이 들어와도 단 한 번의 UPDATE만 성공한다.
--    반환값 true = 이 호출이 "가장 먼저"로 인정됨.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION claim_call_event(
  p_event_id bigint,
  p_user_id  bigint,
  p_reward   integer
)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
  v_rows integer;
BEGIN
  UPDATE global_call_events
  SET claimed_by = p_user_id,
      claimed_at = now(),
      reward_amount = p_reward
  WHERE id = p_event_id
    AND claimed_by IS NULL
    AND expires_at > now();

  GET DIAGNOSTICS v_rows = ROW_COUNT;
  RETURN v_rows > 0;
END;
$$;

-- ------------------------------------------------------------
-- 8. 채팅 횟수 원자적 증가 RPC (Phase 0-1 대응)
--    읽고-더하고-쓰는 방식 대신 DB에서 한 번에 +1 해서 동시 요청에도 안전하다.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION increment_chat_count(p_user_id bigint)
RETURNS bigint
LANGUAGE sql
AS $$
  UPDATE users
  SET chat_count = chat_count + 1
  WHERE user_id = p_user_id
  RETURNING chat_count;
$$;

-- ------------------------------------------------------------
-- 10. 오늘 대화 횟수 원자적 증가 RPC (Phase 5, 3-5 최다 대화자 판정용)
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION increment_messages_today(p_user_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_stat_date date := kst_today();
  v_count integer;
BEGIN
  INSERT INTO daily_stats (user_id, stat_date, messages_today)
  VALUES (p_user_id, v_stat_date, 1)
  ON CONFLICT (user_id, stat_date)
  DO UPDATE SET messages_today = daily_stats.messages_today + 1
  RETURNING messages_today INTO v_count;
  RETURN v_count;
END;
$$;

-- ------------------------------------------------------------
-- 11. guild_channels — 서버별 "마지막 활동 채널" (Phase 5)
--     부름/취침 이벤트처럼 봇이 먼저 말을 거는 기능이 어느 채널에
--     올릴지 결정할 때 쓴다. 매 메시지마다 최신값으로 덮어쓴다.
-- ------------------------------------------------------------

CREATE TABLE guild_channels (
  guild_id         bigint PRIMARY KEY,
  last_channel_id   bigint NOT NULL,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 12. global_call_events 확장 (Phase 5)
--     기존에는 "생성 = 즉시 게시"를 가정했지만, 이제 08:00에 그날
--     보낼 5개 시각을 미리 정해두고 그 시각이 될 때까지 기다렸다가
--     게시한다. scheduled_at(예정 시각)과 posted_at(실제 게시 시각)을
--     분리하고, penalty_applied로 무응답 페널티 중복 적용을 막는다.
-- ------------------------------------------------------------

ALTER TABLE global_call_events
  ADD COLUMN scheduled_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN penalty_applied boolean NOT NULL DEFAULT false,
  ALTER COLUMN posted_at DROP DEFAULT,
  ALTER COLUMN posted_at DROP NOT NULL,
  ALTER COLUMN expires_at DROP NOT NULL;

-- ------------------------------------------------------------
-- 13. 전체 사용자 일괄 호감도 증감 RPC (3-2: 무응답 시 전원 -1)
--     UPDATE 한 번으로 처리해서 부분 실패/레이스 걱정이 없다.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION apply_global_penalty(p_amount integer)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_stat_date date := kst_today();
BEGIN
  -- Supabase 프로젝트에 "WHERE절 없는 UPDATE 금지" 안전장치가 걸려있어 WHERE true를 붙인다.
  UPDATE users SET affection = affection + p_amount WHERE true;

  INSERT INTO daily_stats (user_id, stat_date, daily_net)
  SELECT user_id, v_stat_date, p_amount FROM users
  ON CONFLICT (user_id, stat_date)
  DO UPDATE SET daily_net = daily_stats.daily_net + EXCLUDED.daily_net;
END;
$$;

ALTER TABLE guild_channels ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 9. RLS (Row Level Security)
--    봇은 service_role 키로 접속하므로 RLS를 우회하고 정상 동작한다.
--    Supabase는 테이블을 기본적으로 REST API(anon/public)에 노출하므로,
--    아래처럼 RLS만 켜고 별도 정책을 추가하지 않으면 anon 키로는
--    아무 것도 조회/수정할 수 없어 안전하다 (기본 거부).
-- ------------------------------------------------------------

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE global_call_events ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 14. guild_sleep_state — 서버별 "취침 중 맨션 깨움" 이벤트 상태 (UX 개선 8)
--     취침 시간대(00:00~06:30)에 봇을 맨션하면 그 서버 한정으로 카운트가
--     쌓이고, 그날 서버마다 새로 뽑힌 랜덤 임계치(1~10)에 도달하면 깨움
--     이벤트가 1회 발생한다. sleep_date가 오늘(KST)이 아니면 다음 맨션
--     때 자동으로 새 밤으로 취급해 임계치를 다시 뽑고 리셋한다.
-- ------------------------------------------------------------

CREATE TABLE guild_sleep_state (
  guild_id         bigint PRIMARY KEY,
  sleep_date        date NOT NULL,                 -- 이 상태가 유효한 밤의 기준 날짜(KST)
  threshold          integer NOT NULL,               -- 오늘 밤 깨움에 필요한 맨션 횟수(1~10, 매일 재추첨)
  mention_count       integer NOT NULL DEFAULT 0,     -- 오늘 밤 누적된 맨션 횟수(메시지 1개 = 1회, 연속 맨션 포함)
  triggered            boolean NOT NULL DEFAULT false, -- 오늘 밤 이미 깨움 이벤트가 발생했는지(= 방해금지 모드)
  updated_at            timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE guild_sleep_state ENABLE ROW LEVEL SECURITY;

-- ------------------------------------------------------------
-- 15. 일일 상한 미적용 호감도 증감 RPC (UX 개선 8)
--     취침 중 깨움 이벤트의 악몽 감사(+5)는 CLAUDE.md 규정상 하루 +20
--     획득 상한에 포함되지 않아야 하므로, daily_gain은 건드리지 않고
--     users.affection과 daily_net(3-5 순증감 판정용)만 갱신한다.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION add_affection_uncapped(
  p_user_id bigint,
  p_amount  integer,
  p_method  text DEFAULT NULL
)
RETURNS TABLE (new_affection bigint)
LANGUAGE plpgsql
AS $$
DECLARE
  v_stat_date date := kst_today();
BEGIN
  INSERT INTO daily_stats (user_id, stat_date)
  VALUES (p_user_id, v_stat_date)
  ON CONFLICT (user_id, stat_date) DO NOTHING;

  UPDATE daily_stats
  SET daily_net = daily_net + p_amount,
      gain_methods = CASE
        WHEN p_amount > 0 AND p_method IS NOT NULL
          THEN gain_methods || to_jsonb(p_method)
        ELSE gain_methods
      END
  WHERE user_id = p_user_id AND stat_date = v_stat_date;

  UPDATE users
  SET affection = affection + p_amount
  WHERE user_id = p_user_id;

  RETURN QUERY SELECT affection FROM users WHERE user_id = p_user_id;
END;
$$;

-- ------------------------------------------------------------
-- 16. 취침 중 맨션 깨움 이벤트: 원자적 카운트/판정 RPC (디버깅 보완, CLAUDE.md 9-1 대응)
--     읽고-더하고-쓰는 방식(Python에서 get_or_reset_state → +1 → update)은 같은
--     서버에서 서로 다른 유저가 거의 동시에 맨션하면 레이스 컨디션(카운트 유실,
--     이중 발동)에 취약하다. FOR UPDATE 행 잠금으로 한 번의 호출 안에서
--     "밤이 바뀌었으면 리셋 → +1 → 임계치 도달 시 1회만 발동"을 전부 직렬화한다.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION register_sleep_mention(p_guild_id bigint)
RETURNS TABLE (just_triggered boolean)
LANGUAGE plpgsql
AS $$
DECLARE
  v_today date := kst_today();
  v_row_date date;
  v_threshold integer;
  v_count integer;
  v_triggered boolean;
BEGIN
  INSERT INTO guild_sleep_state (guild_id, sleep_date, threshold, mention_count, triggered)
  VALUES (p_guild_id, v_today, 1 + floor(random() * 10)::integer, 0, false)
  ON CONFLICT (guild_id) DO NOTHING;

  SELECT sleep_date, threshold, mention_count, triggered
  INTO v_row_date, v_threshold, v_count, v_triggered
  FROM guild_sleep_state
  WHERE guild_id = p_guild_id
  FOR UPDATE;

  -- 밤이 바뀌었으면(오늘 처음 맨션이면) 임계치를 새로 뽑고 리셋한다.
  IF v_row_date <> v_today THEN
    v_threshold := 1 + floor(random() * 10)::integer;
    v_count := 0;
    v_triggered := false;
  END IF;

  IF v_triggered THEN
    UPDATE guild_sleep_state
    SET sleep_date = v_today, threshold = v_threshold, mention_count = v_count, triggered = v_triggered
    WHERE guild_id = p_guild_id;
    RETURN QUERY SELECT false;
    RETURN;
  END IF;

  v_count := v_count + 1;
  v_triggered := v_count >= v_threshold;

  UPDATE guild_sleep_state
  SET sleep_date = v_today, threshold = v_threshold, mention_count = v_count, triggered = v_triggered
  WHERE guild_id = p_guild_id;

  RETURN QUERY SELECT v_triggered;
END;
$$;

-- ------------------------------------------------------------
-- 17. chat_history에 role 컬럼 추가 (대화 기억 기능)
--     자연어 생성 시 최근 최대 5턴(유저 발화 + 햄미 답장)을 같이 모델 입력에
--     넣어주기 위해, 이제 햄미 자신의 답장도 role='assistant'로 같이 저장한다.
--     기존 행은 전부 유저 발화였으므로 DEFAULT 'user'로 자동 채워진다.
-- ------------------------------------------------------------

ALTER TABLE chat_history
  ADD COLUMN role text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'assistant'));
