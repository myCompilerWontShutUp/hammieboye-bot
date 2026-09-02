-- ============================================================
-- hammieboye-bot Supabase 스키마 (2026-08-25 대규모 재설계, 전체 리셋판)
-- CLAUDE.md 섹션 1~13의 최신 스펙을 반영한 DDL.
-- 기존 프로젝트를 초기화하는 스크립트이므로, 위쪽 DROP 구문이 기존
-- 테이블/함수/타입을 전부 지운다. Supabase 프로젝트의 SQL Editor에
-- 그대로 붙여넣어 실행한다 (기존 데이터는 전부 사라짐).
-- ============================================================

-- ------------------------------------------------------------
-- 0. 기존 객체 전체 삭제 (전체 리셋)
-- ------------------------------------------------------------

DROP TABLE IF EXISTS user_emoji_tags CASCADE;
DROP TABLE IF EXISTS admin_chat_history CASCADE;
DROP TABLE IF EXISTS admin_sessions CASCADE;
DROP TABLE IF EXISTS admin_ops CASCADE;
DROP TABLE IF EXISTS user_achievements CASCADE;
DROP TABLE IF EXISTS withdrawn_users CASCADE;
DROP TABLE IF EXISTS admin_command_log CASCADE;
DROP TABLE IF EXISTS guild_sleep_state CASCADE;
DROP TABLE IF EXISTS guild_channels CASCADE;
DROP TABLE IF EXISTS global_call_events CASCADE;
DROP TABLE IF EXISTS affection_log CASCADE;
DROP TABLE IF EXISTS chat_history CASCADE;
DROP TABLE IF EXISTS daily_stats CASCADE;
DROP TABLE IF EXISTS users CASCADE;

DROP FUNCTION IF EXISTS increment_help_count(bigint);
DROP FUNCTION IF EXISTS refresh_daily_conversation_caps();
DROP FUNCTION IF EXISTS register_sleep_mention(bigint);
DROP FUNCTION IF EXISTS add_affection_uncapped(bigint, integer, text);
DROP FUNCTION IF EXISTS apply_global_penalty(integer);
DROP FUNCTION IF EXISTS claim_call_event(bigint, bigint, integer);
DROP FUNCTION IF EXISTS increment_messages_today(bigint);
DROP FUNCTION IF EXISTS increment_chat_count(bigint);
DROP FUNCTION IF EXISTS add_affection(bigint, integer, text);
DROP FUNCTION IF EXISTS set_affection(bigint, bigint);
DROP FUNCTION IF EXISTS set_updated_at();
DROP FUNCTION IF EXISTS kst_today();

DROP TYPE IF EXISTS emotion;

-- ------------------------------------------------------------
-- 1. 공통 유틸
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
-- 2. 감정 ENUM (긍정 10 + 부정 10)
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
-- 3. users — 영구 수집 항목 (CLAUDE.md 섹션 1-1)
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
-- 4. daily_stats — 구간(세션) 수집 항목 (CLAUDE.md 섹션 1-2)
--    사용자 x 날짜(KST) 조합마다 한 행. "리셋"은 별도 삭제 없이
--    날짜가 바뀌면 새 행을 만드는 방식으로 자연스럽게 처리한다.
-- ------------------------------------------------------------

CREATE TABLE daily_stats (
  user_id                        bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  stat_date                       date NOT NULL DEFAULT kst_today(),

  -- 당일 획득 호감도. daily_gain은 "얻은 양"만 누적(+20 상한 계산용, 항상 0 이상).
  -- daily_net은 하락분까지 반영한 순증감(음수 가능, 3-5의 "당일 획득 호감도 음수" 제외 판정용).
  -- daily_gain_natural은 daily_gain의 부분집합으로, 명령어(예: 플라스틱 병)로 얻은 몫은 제외한
  -- "자연어로 얻은" 몫만 누적한다 (/내정보 "오늘 획득한 호감도" 표시 전용, 신규).
  daily_gain                       integer NOT NULL DEFAULT 0,
  daily_gain_natural                integer NOT NULL DEFAULT 0,
  daily_net                         integer NOT NULL DEFAULT 0,
  gain_methods                      jsonb NOT NULL DEFAULT '[]'::jsonb, -- 당일 획득 방법 목록

  messages_today                    integer NOT NULL DEFAULT 0,     -- 오늘 나눈 대화 수 (3-5 최다 대화자 판정용)
  messages_today_reached_at          timestamptz,                     -- messages_today가 마지막으로 갱신된 시각
                                                                       -- (=오늘의 최종 횟수에 도달한 시각, 동점자
                                                                       -- 타이브레이크용, 신규)

  -- 자연어 대화 일일 상한 (신규). nl_cap은 매일 06:30에 그 순간 호감도로 동결되며(NULL=아직 미계산,
  -- 첫 사용 시점에 즉석 계산해 고정), nl_count는 실제로 생성까지 도달한 자연어 메시지 수,
  -- over_cap_attempts는 상한 소진 후 추가로 시도한 자연어 횟수(1~4 고정문구/5 경고/6+ 무시+페널티).
  nl_cap                            integer,
  nl_count                           integer NOT NULL DEFAULT 0,
  over_cap_attempts                  integer NOT NULL DEFAULT 0,

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
-- 5. chat_history — 히스토리 대화 내용 (CLAUDE.md 섹션 1-2, 30분/최대 50개)
--    role='user'는 실제 사용자 발화, role='assistant'는 햄미 자신의
--    답장(대화 기억용, UX 개선 10). 보관 기간·개수 제한은 애플리케이션이
--    조회 시점에 (created_at > now() - interval '30 minutes') + LIMIT으로 강제한다.
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
-- 6. affection_log — 호감도 변경 이력 (신규, §13-D 글로벌 랭킹 동점 판정용)
--    호감도가 바뀔 때마다 한 줄씩 남긴다. add_affection/add_affection_uncapped
--    RPC 안에서 같이 기록되며, la-set/la-reset(관리자, set_affection RPC)은
--    여기 기록되지 않는다 (§13-F 확정: daily_stats/affection_log 둘 다 미접촉).
-- ------------------------------------------------------------

CREATE TABLE affection_log (
  id           bigserial PRIMARY KEY,
  user_id       bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  delta          integer NOT NULL,     -- 이번에 실제 적용된 증감분
  new_value      bigint NOT NULL,       -- 적용 후 호감도
  method          text,                   -- 획득/하락 방법 식별자 (있으면)
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_affection_log_user_value ON affection_log (user_id, new_value, delta, created_at DESC);

-- ------------------------------------------------------------
-- 6-1. admin_command_log — 관리자 콘솔("주인님-가라사대") 명령어 실행 이력 (신규)
--    상태를 바꾸는 명령어만 기록한다(변경 전/후 값 포함). 조회 전용 명령어는 기록하지 않는다.
-- ------------------------------------------------------------

CREATE TABLE admin_command_log (
  id             bigserial PRIMARY KEY,
  command          text NOT NULL,
  args              text,
  before_value       text,
  after_value         text,
  created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_admin_command_log_created ON admin_command_log (created_at DESC);

-- ------------------------------------------------------------
-- 7. global_call_events — 글로벌 부름 이벤트 (CLAUDE.md 섹션 3-2)
--    "가장 먼저 반응한 1명"을 원자적으로 판정하기 위한 클레임 테이블.
--    scheduled_at(예정 시각)에 맞춰 게시하고 posted_at/expires_at을 채운다.
-- ------------------------------------------------------------

CREATE TABLE global_call_events (
  id               bigserial PRIMARY KEY,
  prompt_text        text NOT NULL,             -- 게시된 메시지 원문
  scheduled_at        timestamptz NOT NULL DEFAULT now(),
  posted_at            timestamptz,
  expires_at            timestamptz,             -- posted_at + 10분

  -- 가장 먼저 반응한 사용자 (NULL = 아직 없음). ON DELETE SET NULL: 탈퇴(/탈퇴)로 유저 행이
  -- 삭제돼도 과거 이벤트 기록 자체는 남기고 참조만 NULL로 끊는다 (탈퇴가 FK 위반으로 실패하면 안 됨).
  claimed_by           bigint REFERENCES users(user_id) ON DELETE SET NULL,
  claimed_at            timestamptz,
  reward_amount          integer,                   -- 1~10 랜덤 지급량 (클레임 시 확정)
  penalty_applied         boolean NOT NULL DEFAULT false, -- 무응답 페널티 중복 적용 방지

  -- 서버별로 게시된 메시지 위치. 다른 서버 메시지를 수정할 때 사용.
  messages               jsonb NOT NULL DEFAULT '{}'::jsonb,

  created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_global_call_events_active ON global_call_events (expires_at) WHERE claimed_by IS NULL;

-- ------------------------------------------------------------
-- 8. guild_channels — 서버별 "마지막 활동 채널"
--    부름/취침 이벤트처럼 봇이 먼저 말을 거는 기능이 어느 채널에
--    올릴지 결정할 때 쓴다. 매 메시지마다 최신값으로 덮어쓴다.
-- ------------------------------------------------------------

CREATE TABLE guild_channels (
  guild_id         bigint PRIMARY KEY,
  last_channel_id   bigint NOT NULL,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 9. guild_sleep_state — 서버별 "취침 중 맨션 깨움" 이벤트 상태
--    취침 시간대(00:00~06:30)에 봇을 맨션하면 그 서버 한정으로 카운트가
--    쌓이고, 그날 서버마다 새로 뽑힌 랜덤 임계치(1~10)에 도달하면 깨움
--    이벤트가 1회 발생한다.
-- ------------------------------------------------------------

CREATE TABLE guild_sleep_state (
  guild_id         bigint PRIMARY KEY,
  sleep_date        date NOT NULL,
  threshold          integer NOT NULL,
  mention_count       integer NOT NULL DEFAULT 0,
  triggered            boolean NOT NULL DEFAULT false,
  updated_at            timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 9-1. withdrawn_users — 탈퇴(/탈퇴) 시각 최소 기록 (신규)
--    탈퇴 시 users(및 CASCADE로 daily_stats/chat_history/affection_log)는 즉시 삭제하지만,
--    24시간 재가입 금지 판정을 위한 최소 타임스탬프 하나만 별도로 남긴다. 실질적인 수집
--    데이터(호감도·대화내용 등)가 아니라 순수 운영 판정용 기록이라 §1-1의 "동의 이전 최소
--    식별 기록은 고지 대상 수집이 아니다" 원칙과 같은 선상이다. users를 참조하지 않는다
--    (탈퇴한 유저의 users 행은 이미 삭제됐으므로).
-- ------------------------------------------------------------

CREATE TABLE withdrawn_users (
  user_id         bigint PRIMARY KEY,
  withdrawn_at      timestamptz NOT NULL
);

-- ------------------------------------------------------------
-- 9-2. user_achievements — 업적 획득 기록 (신규)
--    achievement_id는 애플리케이션 쪽 achievements/ 패키지의 고정 문자열 ID와 대응한다
--    (자유 텍스트, ENUM 아님 — 새 업적 추가 시 마이그레이션 불필요). 유저당 같은 업적은
--    한 번만 기록되고(PK), earned_at으로 획득 순서를 판단한다. 탈퇴 시 CASCADE 삭제.
-- ------------------------------------------------------------

CREATE TABLE user_achievements (
  user_id         bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  achievement_id    text NOT NULL,
  earned_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, achievement_id)
);

-- ------------------------------------------------------------
-- 9-3. admin_ops — 관리자 콘솔("주인님 가라사대") 권한 (신규)
--    최초 명령어 제공자(prime=true)는 부팅 시 항상 시드된다. 그 외 행은 prime이 op grant로
--    부여한 권한자다 — op 명령어(grant/revoke/list) 자체를 뺀 모든 관리자 명령어에서 prime과
--    동일한 권한을 갖는다. users를 참조하지 않는다(등록 여부는 grant 시점에만 확인하고,
--    이후 유저가 탈퇴해도 권한 기록 자체는 별개로 남아도 무방한 순수 운영 데이터라서).
-- ------------------------------------------------------------

CREATE TABLE admin_ops (
  user_id       bigint PRIMARY KEY,
  prime         boolean NOT NULL DEFAULT false,
  granted_at    timestamptz NOT NULL DEFAULT now()
);

INSERT INTO admin_ops (user_id, prime) VALUES (691254112339230720, true);

-- ------------------------------------------------------------
-- 9-4. admin_sessions — 관리자 콘솔 세션 상태 거울 (신규)
--    유저(주인/권한자)마다 독립적인 60초 세션 쿨타임을 인메모리 타이머(asyncio.Task)로
--    관리하는데, 이 테이블은 그 상태를 조회/감사 가능하게 남기는 거울일 뿐 만료 판정의
--    근거로 쓰지 않는다(메시지마다 이 테이블을 읽으면 성능이 나빠지므로).
-- ------------------------------------------------------------

CREATE TABLE admin_sessions (
  user_id       bigint PRIMARY KEY,
  channel_id    bigint NOT NULL,
  expires_at    timestamptz NOT NULL,
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 9-5. admin_chat_history — "주인님 가라사대" 자연어 전용 히스토리 (신규)
--    최대 5턴/30분 창으로 core/chat.py의 일반 자연어 히스토리와 동일하게 동작하지만,
--    chat_history와는 완전히 별개 테이블이라 두 히스토리가 서로 섞이지 않는다. admin_ops와
--    동일한 이유로 users를 참조하지 않는다.
-- ------------------------------------------------------------

CREATE TABLE admin_chat_history (
  id            bigserial PRIMARY KEY,
  user_id       bigint NOT NULL,
  content       text NOT NULL,
  role          text NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'assistant')),
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_admin_chat_history_user_recent ON admin_chat_history (user_id, created_at DESC);

-- ------------------------------------------------------------
-- 9-6. user_emoji_tags — "bt set"/"bt stop" 관리자 명령어 전용 (신규)
--    관리자가 특정 유저에게 이모지 태그를 걸면, 그 유저가 어느 서버에서 무슨 말을 하든
--    (호출 단어/명령어 여부 무관) 그 이모지들을 순서대로 반응으로 단다. admin_ops와
--    동일한 이유로 users를 참조하지 않는다.
-- ------------------------------------------------------------

CREATE TABLE user_emoji_tags (
  user_id       bigint PRIMARY KEY,
  emojis        jsonb NOT NULL,          -- 순서 있는 이모지 문자 배열, 예: ["🔥", "👍"]
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 10. 원자적 호감도 증감 RPC (일일 +20 상한 적용, affection_log 기록)
--     상승/하락 이벤트 발생 시 애플리케이션은 UPDATE를 직접 하지 말고
--     이 함수를 호출한다. 행 잠금(FOR UPDATE)으로 동시 요청이 들어와도
--     +20 일일 상한이 절대 뚫리지 않는다.
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
  v_new_affection bigint;
BEGIN
  INSERT INTO daily_stats (user_id, stat_date)
  VALUES (p_user_id, v_stat_date)
  ON CONFLICT (user_id, stat_date) DO NOTHING;

  SELECT daily_gain INTO v_current_gain
  FROM daily_stats
  WHERE user_id = p_user_id AND stat_date = v_stat_date
  FOR UPDATE;

  IF p_amount > 0 THEN
    v_applied := LEAST(p_amount, GREATEST(20 - v_current_gain, 0));
  ELSE
    v_applied := p_amount;
  END IF;

  UPDATE daily_stats
  SET daily_gain = daily_gain + GREATEST(v_applied, 0),
      daily_gain_natural = daily_gain_natural + CASE
        WHEN v_applied > 0 AND p_method IS DISTINCT FROM 'plastic_bottle'
          THEN v_applied
        ELSE 0
      END,
      daily_net = daily_net + v_applied,
      gain_methods = CASE
        WHEN v_applied > 0 AND p_method IS NOT NULL
          THEN gain_methods || to_jsonb(p_method)
        ELSE gain_methods
      END
  WHERE user_id = p_user_id AND stat_date = v_stat_date;

  UPDATE users
  SET affection = affection + v_applied
  WHERE user_id = p_user_id
  RETURNING affection INTO v_new_affection;

  IF v_applied <> 0 THEN
    INSERT INTO affection_log (user_id, delta, new_value, method)
    VALUES (p_user_id, v_applied, v_new_affection, p_method);
  END IF;

  RETURN QUERY
  SELECT v_applied,
         v_new_affection,
         (SELECT daily_gain FROM daily_stats WHERE user_id = p_user_id AND stat_date = v_stat_date);
END;
$$;

-- ------------------------------------------------------------
-- 11. 일일 상한 미적용 호감도 증감 RPC (affection_log 기록)
--     daily_gain은 건드리지 않고 users.affection과 daily_net만 갱신한다
--     (취침 깨움 이벤트 악몽 감사 +5, §13-F la-up/la-down 등).
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
  v_new_affection bigint;
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
  WHERE user_id = p_user_id
  RETURNING affection INTO v_new_affection;

  IF p_amount <> 0 THEN
    INSERT INTO affection_log (user_id, delta, new_value, method)
    VALUES (p_user_id, p_amount, v_new_affection, p_method);
  END IF;

  RETURN QUERY SELECT v_new_affection;
END;
$$;

-- ------------------------------------------------------------
-- 12. 절대값 호감도 설정 RPC (신규, §13-F la-set/la-reset 전용)
--     daily_stats/affection_log를 전혀 건드리지 않고 users.affection만
--     직접 SET한다 (델타가 아니라 절대값 지정이라 "획득/상승" 개념 자체가
--     적용되지 않음 — 확정 사항).
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_affection(p_user_id bigint, p_value bigint)
RETURNS bigint
LANGUAGE sql
AS $$
  UPDATE users
  SET affection = p_value
  WHERE user_id = p_user_id
  RETURNING affection;
$$;

-- ------------------------------------------------------------
-- 13. 글로벌 부름 이벤트 원자적 클레임 RPC
--     여러 서버에서 동시에 응답이 들어와도 단 한 번의 UPDATE만 성공한다.
--     반환값 true = 이 호출이 "가장 먼저"로 인정됨.
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
-- 14. 채팅 횟수 원자적 증가 RPC
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
-- 14-1. 도와준 횟수 원자적 증가 RPC (부름 이벤트에 relevant하게 반응해서 클레임 성공한 경우)
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION increment_help_count(p_user_id bigint)
RETURNS bigint
LANGUAGE sql
AS $$
  UPDATE users
  SET help_count = help_count + 1
  WHERE user_id = p_user_id
  RETURNING help_count;
$$;

-- ------------------------------------------------------------
-- 15. 오늘 대화 횟수 원자적 증가 RPC (3-5 최다 대화자 판정용)
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION increment_messages_today(p_user_id bigint)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  v_stat_date date := kst_today();
  v_count integer;
BEGIN
  INSERT INTO daily_stats (user_id, stat_date, messages_today, messages_today_reached_at)
  VALUES (p_user_id, v_stat_date, 1, now())
  ON CONFLICT (user_id, stat_date)
  DO UPDATE SET messages_today = daily_stats.messages_today + 1, messages_today_reached_at = now()
  RETURNING messages_today INTO v_count;
  RETURN v_count;
END;
$$;

-- ------------------------------------------------------------
-- 16. 전체 사용자 일괄 호감도 증감 RPC (3-2: 무응답 시 전원 -1)
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

  INSERT INTO affection_log (user_id, delta, new_value, method)
  SELECT user_id, p_amount, affection, 'global_penalty' FROM users;
END;
$$;

-- ------------------------------------------------------------
-- 16-1. 자연어 대화 일일 상한 갱신 RPC (신규)
--     매일 06:30(기상 시각)에 등록된 모든 유저의 그날 daily_stats 행을 만들고
--     nl_cap을 그 순간 호감도 기준(호감도x2, 최대 500, 음수 호감도는 0으로 클램프)으로
--     동결한다. nl_count/over_cap_attempts도 새 날짜 기준으로 0으로 리셋한다.
-- ------------------------------------------------------------

CREATE OR REPLACE FUNCTION refresh_daily_conversation_caps()
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_stat_date date := kst_today();
BEGIN
  -- 최솟값 20은 호감도가 음수인 사용자에게도 동일하게 적용된다(사용자 확정) — 자연어 자체는
  -- 음수 호감도 게이트가 먼저 막지만, /내정보 등에 표시되는 "오늘 대화 상한" 수치가 0으로
  -- 보이면 혼란스러우므로 표시값 자체를 20 밑으로 내려가지 않게 한다.
  INSERT INTO daily_stats (user_id, stat_date, nl_cap, nl_count, over_cap_attempts)
  SELECT user_id, v_stat_date, LEAST(GREATEST(affection * 2, 20), 500), 0, 0 FROM users
  ON CONFLICT (user_id, stat_date)
  DO UPDATE SET
    nl_cap = EXCLUDED.nl_cap,
    nl_count = 0,
    over_cap_attempts = 0;
END;
$$;

-- ------------------------------------------------------------
-- 17. 취침 중 맨션 깨움 이벤트: 원자적 카운트/판정 RPC
--     FOR UPDATE 행 잠금으로 한 번의 호출 안에서 "밤이 바뀌었으면 리셋
--     → +1 → 임계치 도달 시 1회만 발동"을 전부 직렬화한다.
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
-- 18. RLS (Row Level Security)
--     봇은 service_role 키로 접속하므로 RLS를 우회하고 정상 동작한다.
--     RLS만 켜고 별도 정책을 추가하지 않으면 anon 키로는 아무 것도
--     조회/수정할 수 없어 안전하다 (기본 거부).
-- ------------------------------------------------------------

ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE chat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE affection_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_command_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE global_call_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE guild_channels ENABLE ROW LEVEL SECURITY;
ALTER TABLE guild_sleep_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE withdrawn_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_achievements ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_ops ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_chat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_emoji_tags ENABLE ROW LEVEL SECURITY;
