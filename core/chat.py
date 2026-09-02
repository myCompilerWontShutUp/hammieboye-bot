import asyncio
import random
from datetime import datetime, timedelta, timezone

import discord

import achievements
import documents
import documents.admin_commands as admin_commands_doc
from admin import console as admin_console
from core.base import normalize
from core import intent
from events import call_event
from db.achievements import award as award_achievement
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_nl_cap, update_daily_stats
from db.history import get_recent, get_recent_turns, log, set_detected_emotion
from responses.engine import get_admin_command_response, get_response

# CLAUDE.md 섹션 4-2: 히스토리(30분/최대 50개) 내 누적 3번 반복되면 그다음부터 -1
_HISTORY_WINDOW = timedelta(minutes=30)
_REPEAT_THRESHOLD = 3

# 반복 발화 대응: 정확히 3번째(아직 페널티 전)엔 전조 반응, 4번째(페널티 시점)부턴 화난 반응으로
# 자연어 생성을 건너뛰고 고정 문구로만 답한다 — 페널티가 실제로 적용되는데 태연한 답이 나오면 어색함.
_REPEAT_WARNING_PHRASES = (
    "같은말 계속하지마! 화낼구야... _(짜증)_",
    "어? 방금도 똑같은 말 했잖아... 자꾸 그러면 삐질 거야. _(삐짐)_",
    "또 똑같은 말이야?? 그만해줘, 진짜로. _(경고)_",
    "잠깐, 그 말 아까도 했잖아!! 이제 그만해줘. _(당황)_",
    "어라?? 벌써 세 번째야... 조금만 다르게 말해줄래. _(갸웃)_",
    "똑같은 말만 하면 햄미 지루해!! 이제 그만. _(지루)_",
    "이러다 진짜 삐질 것 같아... 그만 반복해줘. _(불안)_",
    "같은 말 자꾸 하면 햄미도 힘들어!! _(피곤)_",
    "음... 이거 몇 번째야?? 슬슬 신경 쓰여. _(신경)_",
    "계속 똑같으면 재미없어!! 다른 얘기 해줘. _(시무룩)_",
    "어? 또야?? 이제 진짜 그만했으면 조겠어. _(답답)_",
    "같은 말 세 번째면 좀 그래... 조심해줘. _(걱정)_",
    "자꾸 반복하면 햄미 마음이 쪼그라들어. _(움츠림)_",
    "이번이 딱 세 번째야!! 더는 안 대. _(단호)_",
    "똑같은 말 계속하면 삐질 준비 할 거야. _(경계)_",
    "슬슬 이상해!! 벌써 세 번이나 같은 말이야. _(의아)_",
    "그만 좀!! 계속 같은 말은 재미없어. _(투정)_",
    "이거 반복이지?? 이제 다르게 말해줘. _(눈치)_",
    "세 번째 똑같은 말이야... 조심해줘, 진짜로. _(진지)_",
    "자꾸 같은 말 하면 햄미 삐질 각이야. _(삐죽)_",
)
_REPEAT_ANGRY_PHRASES = (
    "하지말라니깐!! _(화남)_",
    "그만하라고 했잖아!! 진짜 화났어!! _(화남)_",
    "몇 번을 말해야 알아들어!! 그만해!! _(짜증)_",
    "결국 화나버려써!! 같은 말 좀 그만!! _(화남)_",
    "말했잖아!! 이제 진짜 삐졌어!! _(삐짐)_",
    "몇 번째야 이게!! 더는 못 참아!! _(짜증)_",
    "경고했는데도 또 그래!! 실망이야!! _(실망)_",
    "그만하라고 몇 번을 말해!! _(답답)_",
    "이제 완전히 삐져버렸어!! 그만!! _(삐짐)_",
    "진짜 화났다구!! 같은 말 좀 그만해!! _(화남)_",
    "계속 무시하니까 화나잖아!! _(짜증)_",
    "결국 이렇게 되네... 좀 다르게 말해주지!! _(실망)_",
    "말 안 들으면 이렇게 되는 거야!! _(단호)_",
    "햄미 인내심 바닥나써!! 진짜 화났어!! _(화남)_",
    "몇 번째 경고를 무시하는 거야!! _(단호)_",
    "너무해!! 계속 똑같은 말만 하고!! _(서운)_",
    "이제 그만 좀!! 햄미 완전 삐졌어!! _(삐짐)_",
    "같은 말 좀 그만하라고 했잖아!! _(짜증)_",
    "진짜 이럴 거야?? 화나려 그래!! _(화남)_",
    "햄미 삐진 거 안 보여?? 그만해줘!! _(삐짐)_",
)

# 자연어 생성 시 직전 맥락으로 같이 넣어줄 최근 대화 턴 수 (유저+햄미 답장 합산)
_CONTEXT_TURN_LIMIT = 5

# "말풍선 한가득" 업적(§21 재배정): 하루 이 횟수 이상 자연어로 대화하면 얻는다.
_SPEECH_BUBBLE_THRESHOLD = 20

# 자연어 대화 일일 상한(신규): 상한 도달 시 API 호출 없이 고정 문구로만 응답한다.
# 상한 소진 후 1~4번째 추가 시도는 풀 A(아래) 재사용, 5번째는 마지막 경고(풀 B),
# 6번째부터는 완전히 무시하며 매번 호감도 -1.
_OVER_CAP_FREE_ATTEMPTS = 4
_OVER_CAP_WARNING_ATTEMPT = 5
_OVER_CAP_IGNORE_RESPONSE = "_(무시)_"

# 상한에 정확히 도달하는 마지막 메시지의 생성 답변 뒤에 이어붙이는 문구 +
# 상한 소진 후 1~4번째 추가 시도에 재사용하는 고정 문구 풀.
_DAILY_LIMIT_PHRASES = (
    "오늘은 너랑 많이 대화해써. 다른 칭구랑 놀고시퍼! 내일바~ _(찡긋)_",
    "햄미 오늘 할 말 다 써버려써!! 낼 또 이야기하자!! _(방긋)_",
    "오늘분 수다는 여기까지야!! 내일 다시 만나조!! _(뿌듯)_",
    "헤헤, 오늘은 이만큼만!! 낼 더 놀아줄게!! _(졸림)_",
    "오늘 얘기 진짜 마니 해써!! 이제 쉬어야 대!! _(피곤)_",
    "햄미 오늘 수다 끝!! 낼 아침에 또 불러조!! _(안녕)_",
    "오늘치 대화는 다 썼어!! 딴 칭구도 만나보고 시퍼!! _(호기심)_",
    "오늘은 여기까지!! 낼 다시 놀아주라!! _(약속)_",
    "햄미 입이 아파써!! 오늘은 이만 자야게써!! _(쉼)_",
    "오늘 대화 한도 끝!! 내일 또 불러줄 거지?? _(기대)_",
    "헥헥, 오늘 진짜 마니 얘기해써!! 낼 보자!! _(숨참)_",
    "오늘은 그만!! 딴 친구랑도 놀고 시퍼!! _(삐죽)_",
    "오늘 몫 다 채워써!! 낼 다시 챗바퀴 돌리고 올게!! _(신남)_",
    "이제 오늘은 조용히 잘래!! 낼 인사하자!! _(꾸벅)_",
    "햄미 오늘 대화 다 써버렸어!! 내일 또 놀아조!! _(찡긋)_",
    "오늘은 여까지 하고 시퍼!! 낼 또 만나조!! _(방실)_",
    "오늘 수다 배 터지게 해써!! 낼 또 오자!! _(배부름)_",
    "이제 다른 칭구도 챙겨야게써!! 낼 다시 와조!! _(바쁨)_",
    "오늘은 여기까지가 딱 조아!! 낼 봐!! _(만족)_",
    "오늘 얘기는 여기서 끝!! 낼 아침에 또 놀자!! _(안녕)_",
)
_DAILY_LIMIT_WARNING_PHRASES = (
    "진짜 마지막이야!! 오늘은 더 이상 말 안 할 거야!! _(단호)_",
    "이게 진짜진짜 마지막 경고야!! 그만 불러줘!! _(경고)_",
    "한 번만 더 부르면 삐질 거야!! 오늘은 끝났다구!! _(삐짐)_",
    "마지막으로 말하는 거야!! 오늘 대화는 다 써버려써!! _(단단)_",
    "이제 진짜 그만!! 낼 다시 놀자니깐!! _(경고)_",
    "마지막 경고야!! 더 부르면 화낼 거야!! _(짜증)_",
    "오늘은 끝이라고 몇 번을 말해!! 이게 마지막이야!! _(답답)_",
    "진짜진짜 마지막이야!! 낼 다시 만나조!! _(경고)_",
    "더 부르면 삐질 거야!! 이게 마지막 기회야!! _(삐죽)_",
    "이번이 진짜 마지막 대답이야!! 그만해줘!! _(단호)_",
    "마지막으로 알려주는 거야!! 오늘은 끝났다구!! _(경고)_",
    "한 번만 더 그러면 진짜 화낼 거야!! 마지막이야!! _(화남)_",
    "이게 마지막 대답이야!! 낼 다시 놀아조!! _(진지)_",
    "더는 못 참아!! 이번이 진짜 마지막이야!! _(경고)_",
    "마지막으로 말할게!! 오늘은 여기까지야!! _(단단)_",
    "한 번 더 부르면 삐질 거니까 마지막이야!! _(삐짐)_",
    "진짜 이게 끝이야!! 더 부르지 마조!! _(단호)_",
    "마지막 기회야!! 이제 그만 불러조!! _(경고)_",
    "이번이 정말 마지막이야!! 낼 아침에 만나조!! _(진지)_",
    "더 부르면 화낼 거야!! 이게 진짜 마지막이야!! _(경고)_",
)

# CLAUDE.md 섹션 2: 음수 호감도 구간표. 완전 무응답이 아니라 짧은 행동 텍스트로 반응한다.
_BITE_THRESHOLD = -20
_IGNORE_RESPONSE = "(무시)"
_BITE_RESPONSE = "(콱 깨묾)"

# CLAUDE.md 섹션 3-3(행복 감정 보상)
_HAPPY_EMOTION = "행복함"
_HAPPY_METHOD = "happy_emotion"

# 2026-09-01 재정정: 기존 "부정 감정 연속/누적 판정" 기반 -1은 폐지 — 20개 감정 중 하나를
# 강제로 고르는 구조라 애매한 메시지도 부정으로 분류되면 쌓여서 억울하게 깎이는 문제가
# 있었다(사용자 확정). 이제는 감정 분류 자체(emotion)는 그대로 계속하되(다른 용도로
# 저장·활용될 수 있어 유지), 호감도 하락은 오직 새로 추가된 심각한 유해 표현 감지
# (has_severe_abuse — 심각한 욕설/비방/타인 모욕/성희롱/패드립)에만 연동한다.
_SEVERE_ABUSE_PENALTY = -1


async def handle_natural_language(
    user_id: int, guild_id: int, text: str, affection: int
) -> str | discord.Embed | tuple[str, discord.Embed]:
    now = datetime.now(timezone.utc)

    # get_recent(4-1/4-2 판정용)과 ensure_nl_cap(일일 상한 조회/동결)은 서로 독립적이라
    # 동시에 가져온다 (지연시간 최적화).
    recent, stats = await asyncio.gather(
        get_recent(user_id, since=now - _HISTORY_WINDOW),
        ensure_nl_cap(user_id, affection),
    )

    total_delta = 0
    current_affection = affection

    def _record(result: dict) -> None:
        nonlocal total_delta, current_affection
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]

    nl_cap = stats["nl_cap"]
    over_cap = stats["nl_count"] >= nl_cap

    # 4-2: 동일 발화 반복 — 정규화 후 비교, 히스토리 내 누적 3번이면 그다음부터 페널티.
    # 상한을 넘긴 뒤로는 완전히 비활성화한다 (사용자 확정). recent는 get_recent()의 기본값
    # role="user"로 조회돼 있어(db/history.py) 유저 본인의 발화만 비교 대상이다 — 햄미
    # 자신의 답장(role="assistant")은 애초에 여기 안 들어오므로 반복 판정에 안 섞인다
    # (2026-09-01 재확인, 기존에도 이미 올바르게 동작하고 있었음).
    normalized_text = normalize(text)
    repeat_count = sum(1 for row in recent if normalize(row["content"]) == normalized_text)
    is_repeat_penalty = not over_cap and repeat_count >= _REPEAT_THRESHOLD  # 4번째부터: 실제 페널티
    is_repeat_warning = not over_cap and repeat_count == _REPEAT_THRESHOLD - 1  # 정확히 3번째: 전조

    # is_repeat_penalty의 -1은 반드시 event_delta를 계산하기 '전에' 적용해야 한다 — add_affection의
    # 반환값(new_affection)은 그 순간의 실제 DB 절대값이라, event_delta(델타값)를 나중에 로컬에서
    # 더할 때 순서가 뒤바뀌면 이미 반영된 값을 또 더해 이중 계산되는 문제가 생긴다.
    if is_repeat_penalty:
        _record(await add_affection(user_id, -1))

    # 이후 실제 생성까지 이어질지는 이미 다 결정됐다 — 생성 경로에서만 직전 맥락(히스토리)이
    # 필요하다. **이번 메시지를 로그에 남기기 전에** 미리 떠 와야 방금 온 메시지가 히스토리에
    # 중복으로 안 들어간다(생성 프롬프트에 같은 메시지가 두 번 들어가는 걸 방지).
    will_generate = (
        affection >= 0 and not over_cap and not is_repeat_penalty and not is_repeat_warning
    )

    # §42(2026-08-30, 사용자 확정): "답변중..." 플레이스홀더는 이제 호출 단어가 확인되는
    # 즉시(자고 있을 때 제외) `core/dispatcher.py`가 이 함수를 부르기도 전에 띄운다 — 여기서는
    # 더 이상 플레이스홀더를 관리하지 않는다(§41 당시엔 이 함수 안에서 관리했지만, dispatcher의
    # touch_channel/ensure_user/카운터 증가 등 여러 DB 왕복이 이 함수 호출 자체보다도 먼저
    # 일어나서 그마저도 늦게 뜨는 문제가 있었다).
    if will_generate:
        # 부름 이벤트 응답 판정도 독립적이라 같이 가져온다.
        context_turns, (event_delta, event_achievement, was_event_response, event_override) = await asyncio.gather(
            get_recent_turns(user_id, since=now - _HISTORY_WINDOW, limit=_CONTEXT_TURN_LIMIT),
            call_event.handle_potential_response(user_id, guild_id, text),
        )
    else:
        context_turns = None
        # 3-2 부름 이벤트 응답 판정은 호감도가 음수여도 예외적으로 항상 시도한다 (섹션 2 예외 규정).
        event_delta, event_achievement, was_event_response, event_override = await call_event.handle_potential_response(
            user_id, guild_id, text
        )

    logged_row = await log(user_id, guild_id, text)

    if event_delta:
        total_delta += event_delta
        current_affection += event_delta

    # 부름 이벤트로 얻은 업적 알림은 이후 어떤 분기로 빠지든(음수 호감도/상한/반복 페널티 등)
    # 최종 응답에 항상 붙어야 한다 — 이 리스트를 모든 _finalize 호출에 그대로 넘긴다.
    achievement_notices = [event_achievement] if event_achievement else []

    # 음수 호감도면 분류/생성 등 OpenAI API를 아예 호출하지 않고 고정 문구로만 답한다 (섹션 2).
    if affection < 0:
        base = _BITE_RESPONSE if affection <= _BITE_THRESHOLD else _IGNORE_RESPONSE
        return _finalize(base, total_delta, current_affection, achievement_notices)

    # 오늘의 자연어 대화 상한을 이미 다 썼으면, 분류/생성 등 API를 아예 호출하지 않고
    # 고정 문구로만 답한다 (신규).
    if over_cap:
        return await _handle_over_cap(
            user_id, stats, total_delta, current_affection, achievement_notices, was_event_response
        )

    # 반복 발화 전조/페널티 시점엔 자연어 생성 없이 톤이 맞는 고정 반응으로 답한다 —
    # 태연하게 생성된 답변에 호감도 하락 알림만 붙이면 어색하다 (사용자 피드백).
    if is_repeat_penalty:
        return _finalize(
            random.choice(_REPEAT_ANGRY_PHRASES), total_delta, current_affection, achievement_notices
        )
    if is_repeat_warning:
        return _finalize(
            random.choice(_REPEAT_WARNING_PHRASES), total_delta, current_affection, achievement_notices
        )

    # §35-3: 부름 이벤트가 활성 상태인데 관련 없는 잡담이면(-1은 이미 위에서 total_delta에
    # 반영됨), 정상 생성을 아예 하지 않고(API 호출 없음, nl_count 미증가) 이 고정 문구로
    # 완전히 대체한다(사용자 확정) — 여기까지 왔다는 건 호감도<0/상한/반복 페널티 등 다른
    # 고정 응답 분기에 걸리지 않았다는 뜻이라, "정상 생성 답변"을 대체하는 게 정확히 맞다.
    if event_override is not None:
        return _finalize(event_override, total_delta, current_affection, achievement_notices)

    # 여기서부터 실제 OpenAI API 호출(분류+생성) 구간.
    # RAG 카테고리 분류 + 감정 판정을 한 번의 호출로 처리 (judge 제거, §13-B/C)
    classification = await intent.classify(text)

    if classification.emotion is not None:
        # 방금 남긴 유저 발화 행에 판정된 감정을 채워 넣는다 (기존엔 컬럼만 있고 아무
        # 코드도 여기 쓰질 않아서 항상 NULL이었던 버그) — 감정 반영(affection)과는
        # 서로 독립적인 쓰기라 동시에 처리한다.
        _, (message_delta, message_achievement) = await asyncio.gather(
            set_detected_emotion(logged_row["id"], classification.emotion),
            _apply_message_effects(
                user_id, classification.emotion, classification.has_severe_abuse, stats
            ),
        )
        total_delta += message_delta
        if message_delta:
            current_affection += message_delta
        if message_achievement:
            achievement_notices.append(message_achievement)

    # 관리자 콘솔 명령어 자연어 설명(신규, §6): 권한자(prime/op)에게만 답하고, 그 외엔
    # 생성 호출 자체를 안 해서 정보가 새지 않는다. 다른 카테고리(profile 등)와 섞이면
    # 완화된 지침과 일반 페르소나 지침이 뒤섞이므로, 이 카테고리가 걸리면 단독으로 처리하고
    # 아래의 일반 RAG 문서/생성 흐름은 타지 않는다. nl_count 증가/첫대화·말풍선 업적
    # 체크도 건너뛴다(관리 목적 문의는 "대화 횟수"로 안 치는 게 자연스럽다는 판단).
    if "admin_commands" in classification.categories:
        if not admin_console.is_authorized(user_id):
            return _finalize(
                "너한테는 알려줄 수 없어!!", total_delta, current_affection, achievement_notices
            )
        admin_response = await get_admin_command_response(text, admin_commands_doc.get_text())
        return _finalize(admin_response, total_delta, current_affection, achievement_notices)

    context_note = documents.build_context_note(classification.categories)
    response_text = await get_response(text, history=context_turns, context_note=context_note)

    # "위대하고 귀여운 대화의 시작"(처음으로 햄미와 대화) — 실제 생성까지 도달한 경우에만
    # 확인한다. §21 재배정: 기존 "말풍선 한가득"의 옛 트리거(첫 자연어 대화)를 인계받았다.
    if await award_achievement(user_id, achievements.first_chat.ID):
        achievement_notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.first_chat)}!!")

    # nl_count는 실제로 생성까지 도달한 메시지만 증가시킨다. 오늘의 마지막 메시지(상한에
    # 정확히 도달)라면 생성된 답변 뒤에 고정 문구를 이어붙인다 (사용자 예시: "일어나써! + 오늘은...").
    new_nl_count = stats["nl_count"] + 1
    if new_nl_count >= nl_cap:
        response_text = f"{response_text}\n\n{random.choice(_DAILY_LIMIT_PHRASES)}"

    # "말풍선 한가득"(§21 재배정: 하루 20회 이상 자연어 대화) — 오늘 새로 20회에 도달한
    # 시점에 최초 1회만 확인한다(award()가 전체 기간 기준 멱등이라 다음 날부턴 다시 안 뜬다).
    if new_nl_count >= _SPEECH_BUBBLE_THRESHOLD and await award_achievement(
        user_id, achievements.speech_bubble.ID
    ):
        achievement_notices.append(f"🏆 업적 달성: {achievements.format_name(achievements.speech_bubble)}!!")

    # 서로 독립적인 마무리 작업(오늘 대화 횟수 갱신 + 봇 답장 로그)은 동시에 처리한다.
    await asyncio.gather(
        update_daily_stats(user_id, {"nl_count": new_nl_count}),
        log(user_id, guild_id, response_text, role="assistant"),
    )

    return _finalize(response_text, total_delta, current_affection, achievement_notices)


async def _handle_over_cap(
    user_id: int,
    stats: dict,
    total_delta: int,
    current_affection: int,
    achievement_notices: list[str],
    was_event_response: bool = False,
) -> str | discord.Embed | tuple[str, discord.Embed]:
    # §32: 오늘 대화 상한을 넘긴 상태여도, 이 메시지가 실제로 부름 이벤트에 대한 반응이었다면
    # (긍정이든 부정이든) 남용 카운터를 건드리지 않는다 — 발견된 버그: 예전엔 이 카운터가
    # 무조건 올라가서, 6회 이상 누적된 상태에서 부름 이벤트에 반응하면 이벤트 자체의 호감도
    # 변화(예: 부정 -5)가 적용된 뒤에도 응답이 "_(무시)_"로 덮이고 추가로 -1까지 겹쳐 붙었다.
    if was_event_response:
        return _finalize(
            random.choice(_DAILY_LIMIT_PHRASES), total_delta, current_affection, achievement_notices
        )

    attempts = stats["over_cap_attempts"] + 1
    await update_daily_stats(user_id, {"over_cap_attempts": attempts})

    if attempts <= _OVER_CAP_FREE_ATTEMPTS:
        return _finalize(
            random.choice(_DAILY_LIMIT_PHRASES), total_delta, current_affection, achievement_notices
        )
    if attempts == _OVER_CAP_WARNING_ATTEMPT:
        return _finalize(
            random.choice(_DAILY_LIMIT_WARNING_PHRASES),
            total_delta,
            current_affection,
            achievement_notices,
        )

    result = await add_affection(user_id, -1)
    total_delta += result["applied_amount"]
    current_affection = result["new_affection"]
    return _finalize(_OVER_CAP_IGNORE_RESPONSE, total_delta, current_affection, achievement_notices)


def _finalize(
    response: str | discord.Embed | tuple[str, discord.Embed],
    delta: int,
    current: int,
    achievement_notices: list[str] | None = None,
) -> str | discord.Embed | tuple[str, discord.Embed]:
    # embed(또는 embed를 포함한 tuple) 응답에는 이미 호감도가 필드로 보이므로 알림을 따로 안 붙인다.
    if isinstance(response, (discord.Embed, tuple)):
        return response
    text = response
    if delta != 0:
        text += format_affection_notice(delta, current)
    for notice in achievement_notices or ():
        text += f"\n{notice}"
    return text


async def _apply_message_effects(
    user_id: int, emotion: str, has_severe_abuse: bool, stats: dict
) -> tuple[int, str | None]:
    # stats는 handle_natural_language 초반에 이미 조회해둔 오늘 daily_stats 스냅샷을
    # 그대로 재사용한다 (중복 조회 제거). 이 사이에 happy_emotion_claimed 필드를 건드리는
    # 다른 호출은 없어 안전하다.
    #
    # 2026-09-01 재정정: 기존 "부정 감정 연속/누적" 기반 -1은 폐지했다 — emotion은 20개 중
    # 하나를 강제로 고르는 구조라, 애매한 메시지가 부정으로 잘못 분류되기만 해도 누적돼서
    # 억울하게 깎이는 문제가 있었다(사용자 확정). emotion 판정 자체(chat_history.
    # detected_emotion 기록)는 그대로 유지하되, 호감도 하락은 이제 오직 심각한 유해 표현
    # 감지(has_severe_abuse)에만 연동한다.
    updates = {}
    delta = 0
    achievement_notice = None

    if has_severe_abuse:
        result = await add_affection(user_id, _SEVERE_ABUSE_PENALTY)
        delta += result["applied_amount"]

    if emotion == _HAPPY_EMOTION and not stats["happy_emotion_claimed"]:
        result = await add_affection(user_id, 1, _HAPPY_METHOD)
        delta += result["applied_amount"]
        updates["happy_emotion_claimed"] = True
        achievement_notice = result["achievement_notice"]

    if updates:
        await update_daily_stats(user_id, updates)

    return delta, achievement_notice
