import random
from datetime import datetime, timedelta, timezone

import achievements
from db.achievements import award as award_achievement
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.users import get_user, set_plastic_cooldown

# CLAUDE.md 섹션 3-1 (2026-08-27: 10분 -> 3분으로 단축, 사용자 확정)
_COOLDOWN = timedelta(minutes=3)
_METHOD = "plastic_bottle"
_STREAK_TARGET = 3
_SUCCESS_RATE = 0.5

# 오늘의 성공(+1)/연속 3회 보너스(+3) 중 하나라도 이미 받은 상태에서 그 조건을 또
# 달성하면(호감도는 더 안 오름) 왜 안 올랐는지 알 수 있게 이 문구를 한 번만 덧붙인다
# (사용자 확정, 둘 다 이미 받은 상태여도 문구는 중복 없이 한 줄만).
_ALREADY_CLAIMED_TODAY_NOTE = "(오늘은 이미 '페트병'으로 호감도를 획득했습니다)"

# 섹션 4-5: 쿨타임 고정 메시지를 이 횟수까지는 그냥 보여주고, 그다음부터 남용 페널티.
# count 1~2: 평범한 쿨타임 안내, count == 3(_COOLDOWN_ABUSE_FREE_COUNT): 마지막 경고,
# count 4+: 실제 페널티(-1) 적용.
_COOLDOWN_ABUSE_FREE_COUNT = 3

# 상황별 고정 문구 풀 (API로 생성 후 검수해서 고정, 사용자 요청 — 최소 20개씩).
_COOLDOWN_MESSAGES = (
    "앗, 방금 던져써!! 아직 쿨타임이 안 끝나서 또 못 던져!! _(당황)_",
    "페트병아 쪼금만 기다려줘!! 햄미 손이 아직 쉬는 중이야!! _(초조)_",
    "어어, 버튼 눌렀는데 업써!! 쿨타임이 꼬옥 붙잡고 이써!! _(황당)_",
    "한 번 던졌더니 힘이 쏙 빠져써!! 다시 던지려면 기다려야 해!! _(기운)_",
    "지금은 던지기 금지 시간이래!! 햄미 억울해!! _(억울)_",
    "페트병이 아직 하늘에서 쉬고 있나 봐!! 쿨타임 끝나야 돌아와!! _(멍함)_",
    "또 던지고 싶은데 손가락이 안 움직여!! 잠깐만 기다리자!! _(답답)_",
    "앗차차, 연속 투척은 안 된대!! 햄미가 너무 빨랐나 봐!! _(실수)_",
    "쿨타임이 아직 꼬물꼬물 남아 이써!! 조금만 더 기다려줘!! _(기다림)_",
    "던지기 버튼이 잠들어써!! 깨워도 꿈쩍도 안 해!! _(졸림)_",
    "방금 던진 힘을 충전하는 중이야!! 금방 다시 흔들 수 이써!! _(충전)_",
    "페트병 또 던질랬는데 막혀써!! 이건 햄미 잘못 업써!! _(삐짐)_",
    "아직 재사용 시간이 남았대!! 햄미는 얌전히 기다릴게!! _(체념)_",
    "던지기 준비 완료인 줄 알았는데 아니래!! 쿨타임 너무 길어!! _(실망)_",
    "한 번 던졌으니 잠깐 쉬어야 한대!! 햄미도 숨 고를게!! _(휴식)_",
    "왜 또 안 던져져?? 아하, 쿨타임이 아직 안 끝났구나!! _(깨달음)_",
    "페트병을 다시 잡았는데 발사가 안 돼!! 잠시 봉인된 모양이야!! _(봉인)_",
    "햄미 손은 멀쩡한데 던지기가 업써!! 시간아 빨리 가라!! _(애원)_",
    "조아, 기다릴게!! 쿨타임 끝나면 바로 힘껏 던질 거야!! _(의지)_",
    "앗, 또 던지기는 아직이네!! 햄미는 페트병 옆에서 대기할게!! _(대기)_",
)
_COOLDOWN_WARNING_MESSAGES = (
    "아직 준비 안됬다고!! 경고야, 시간 되면 그때 던져!! _(화남)_",
    "자꾸 이러면 진짜 삐질 거야!! 이게 마지막 경고야!! _(경고)_",
    "쿨타임 몰라?? 계속 이러면 화낼 거야!! _(짜증)_",
    "한 번만 더 그러면 진짜 화날 거야!! 기다려!! _(경고)_",
    "이제 진짜 마지막이야!! 쿨타임 좀 지켜줘!! _(단호)_",
    "자꾸 재촉하면 햄미도 화낼 거야!! 이게 경고야!! _(화남)_",
    "그만 좀 눌러!! 이번이 마지막 봐주는 거야!! _(짜증)_",
    "쿨타임 무시하지 마!! 다음엔 진짜 화낼 거야!! _(경고)_",
    "몇 번째야 진짜!! 이제 정말 마지막 경고야!! _(답답)_",
    "햄미 슬슬 삐지려 그래!! 조금만 기다려!! _(삐짐)_",
    "이게 진짜진짜 마지막 기회야!! 쿨타임 지켜!! _(경고)_",
    "계속 이러면 화낼 준비 돼써!! 마지막 경고야!! _(화남)_",
    "쿨타임인 거 알잖아!! 이번이 진짜 끝이야!! _(단호)_",
    "한번만 더 누르면 삐질 거야!! 진짜야!! _(삐짐)_",
    "자꾸 그러면 나 화낼 거야!! 마지막으로 말해!! _(경고)_",
    "슬슬 인내심이 바닥나!! 이게 마지막이야!! _(짜증)_",
    "이번이 마지막 경고라구!! 쿨타임 좀!! _(단호)_",
    "계속하면 진짜 삐질 거야!! 마지막 기회야!! _(경고)_",
    "햄미 화나기 직전이야!! 제발 좀 기다려줘!! _(짜증)_",
    "마지막으로 경고할게!! 더 누르면 화낼 거야!! _(화남)_",
)
_COOLDOWN_PENALTY_MESSAGES = (
    "몇 번을 말해야 알아들어!! 이제 진짜 화났어!! _(화남)_",
    "결국 화나버려써!! 쿨타임 좀 지켜달랬잖아!! _(짜증)_",
    "하지 말라고 했잖아!! 이제 삐졌어!! _(삐짐)_",
    "진짜 화났어!! 쿨타임 무시하지 마!! _(화남)_",
    "이제 완전히 삐져버렸어!! 그만해줘!! _(삐짐)_",
    "말을 안 들으니까 이렇게 대는 거야!! _(단호)_",
    "햄미 인내심 바닥나써!! 진짜 화났어!! _(화남)_",
    "계속 이러면 곤란해!! 이제 화날 거야!! _(짜증)_",
    "경고했는데도 또 그래!! 실망이야!! _(실망)_",
    "이제 그만 좀!! 햄미 완전 삐졌어!! _(삐짐)_",
    "쿨타임 지키라고 몇 번을 말해!! _(답답)_",
    "진짜 이럴 거야?? 화나려 그래!! _(화남)_",
    "말 안 들으면 이렇게 되는 거야!! _(단호)_",
    "결국 삐지고 말았어!! 조심해줘!! _(삐짐)_",
    "너무해!! 경고했잖아!! _(서운)_",
    "이제 진짜 화났다구!! 그만해!! _(화남)_",
    "계속 무시하니까 화나잖아!! _(짜증)_",
    "햄미 삐진 거 안 보여?? 그만해줘!! _(삐짐)_",
    "몇 번째 경고를 무시하는 거야!! _(단호)_",
    "결국 이렇게 되네... 조금만 기다려주지!! _(실망)_",
)
_FAIL_MESSAGES = (
    "앗, 빗나가버려써!! 아쉽지만 다음엔 꼭 성공할 거야!! _(아쉬움)_",
    "으악, 페트병이 휙 도망갔어!! 다시 힘내서 던질래!! _(당황)_",
    "이번엔 실패해써… 그래도 다음 도전은 햄미 차례야!! _(투지)_",
    "어라, 손에서 미끄러졌네?? 다음엔 더 꼭 잡을 거야!! _(아깝)_",
    "쳇, 조금 모자랐어!! 다시 던지면 분명 잘될 거야!! _(오기)_",
    "페트병아 기다려!! 다음엔 안 놓칠 거야!! _(분함)_",
    "앗차차, 실패해써!! 그래도 햄미는 포기 안 해!! _(의욕)_",
    "아쉽다아… 다음번엔 더 멋지게 날려볼 거야!! _(섭섭)_",
    "이번 건 연습이었어!! 다음 도전은 진짜 성공할 거야!! _(자신)_",
    "휙 날렸는데 엉뚱한 데 갔어!! 다시 해보자!! _(민망)_",
    "아이고, 살짝 빗나갔네!! 다음엔 가운데로 던질 거야!! _(아쉬움)_",
    "실패해써도 괜차나!! 햄미가 한 번 더 도전할래!! _(용기)_",
    "페트병이 너무 빨랐어!! 다음엔 햄미가 더 빠를 거야!! _(승부)_",
    "으으, 아깝게 실패했어!! 다시 힘 모아서 던져볼게!! _(집중)_",
    "이번엔 꽝이네?? 그래도 다음엔 꼭 해낼 거야!! _(희망)_",
    "손끝이 삐끗해써!! 다음번엔 제대로 겨냥할래!! _(반성)_",
    "앗, 놓쳐버렸어!! 하지만 햄미의 도전은 아직 안 끝났어!! _(끈기)_",
    "조금 아쉽지만 괜찮아!! 다음 던지기는 더 조아질 거야!! _(낙관)_",
    "페트병 던지기 실패!! 그래도 다시 도전하면 되지!! _(씩씩)_",
    "이번엔 졌지만 다음엔 이길 거야!! 햄미, 재도전 간다!! _(신남)_",
)
# 연속 성공 1회차 — 쿨타임 없이 바로 또 던질 수 있음을 언급.
_SUCCESS_STREAK1_MESSAGES = (
    "성공해써!! 바로 또 던질 수 이써!! _(신남)_",
    "우와, 성공!! 쿨타임 업시 바로 또 던져도 대!! _(들뜸)_",
    "던지기 성공!! 지금 바로 또 도전할 수 이써!! _(기대)_",
    "성공했어!! 이번엔 쿨타임 업써서 바로 또 던질래!! _(신남)_",
    "야호, 성공!! 연속으로 또 던져볼까?? _(호기심)_",
    "첫 성공!! 지금 이 기세로 또 던져보자!! _(의욕)_",
    "성공이야!! 바로 다시 도전할 수 이써!! _(자신)_",
    "던지기 성공!! 쉬지 않고 또 던질 거야!! _(열정)_",
    "해써!! 쿨타임 없이 바로 또 시도 가능해!! _(신남)_",
    "성공!! 이 기분으로 한 번 더 던져볼래!! _(들뜸)_",
    "우와!! 성공했으니까 바로 또 던져야지!! _(신남)_",
    "성공이다!! 지금 연속으로 도전 가능해!! _(기대)_",
    "던졌더니 성공!! 바로 또 던질 수 이써!! _(방긋)_",
    "야호, 첫 판 성공!! 계속 던져보자!! _(흥분)_",
    "성공했어!! 쉬지 않고 바로 또 갈게!! _(의지)_",
    "던지기 성공!! 연속 도전 시작이야!! _(호승심)_",
    "해냈다!! 지금 바로 또 던질 수 이써!! _(뿌듯)_",
    "성공!! 이 흐름 타고 한 번 더!! _(신남)_",
    "첫 성공 완료!! 바로 이어서 또 던질래!! _(들뜸)_",
    "성공했어!! 쿨타임 없으니까 계속 가보자!! _(자신감)_",
)
# 연속 성공 2회차 — 한 번만 더 하면 호감도가 많이 오를 것 같다는 기대감 언급.
_SUCCESS_STREAK2_MESSAGES = (
    "두 번 연속 성공!! 한번만 더 하면 호감도 많이 오를 것 같아!! _(기대)_",
    "우와, 두 번째 성공!! 다음이 진짜 중요해!! _(두근)_",
    "연속 두 번!! 한 번만 더 성공하면 대박일 것 같아!! _(설렘)_",
    "두 번 연속이야!! 다음 성공이 기대돼!! _(기대)_",
    "성공 두 번째!! 이러다 큰 보너스 나올 것 같아!! _(흥분)_",
    "연속 성공 두 번!! 느낌이 조아!! _(두근)_",
    "두 번째 성공!! 한 번만 더 하면 뭔가 터질 것 같아!! _(긴장)_",
    "우와아, 연속 두 번!! 이대로면 대박 예감!! _(신남)_",
    "성공 두 번 연속!! 다음이 진짜 승부야!! _(진지)_",
    "두 번째!! 한 번만 더 성공하면 호감도 팍 오를 것 같아!! _(기대)_",
    "연속 두 번 성공!! 손이 안 떨려!! _(자신)_",
    "두 번째 성공했어!! 다음 판이 기대돼!! _(설렘)_",
    "성공 두 번!! 이 흐름 계속 가보자!! _(의욕)_",
    "우와, 벌써 두 번째!! 다음이 진짜 중요해!! _(긴장)_",
    "연속 두 번 성공!! 느낌 완전 조아!! _(들뜸)_",
    "두 번째 성공!! 한 번만 더면 큰 거 온다!! _(기대)_",
    "성공 두 번째야!! 다음 던지기가 관건이야!! _(진지)_",
    "연속 두 번!! 이러다 진짜 대박날 것 같아!! _(흥분)_",
    "두 번 연속 성공!! 다음 한 번이 승부처야!! _(긴장)_",
    "성공 두 번!! 한 번 더 하면 호감도 많이 오를 거야!! _(기대)_",
)
# 연속 성공 3회차 — 보너스(+3)가 실제로 지급되는 순간. "세 번"/"보너스"를 정확히 언급한다.
_SUCCESS_STREAK_MESSAGES = (
    "페트병 던지기 세 번 연속 성공이야!! 보너스까지 받았어!! _(폭발)_",
    "햄미 완전 천재 햄스터 같아!! 보너스 냠냠이다!! _(신남)_",
    "세 번이나 맞혔어!! 내 발이 오늘 아주 날쌔!! _(뿌듯)_",
    "우와아!! 보너스가 떨어졌어!! 햄미 최고다!! _(환호)_",
    "페트병이 착착 들어갔어!! 이건 기적이야!! _(황홀)_",
    "햄미 손맛 미쳤다!! 세 번 성공하고 간식까지 얻었어!! _(대박)_",
    "나 지금 하늘을 날아갈 것 같아!! 보너스 조아!! _(들뜸)_",
    "연속 성공이라니!! 햄미의 전설이 시작됐어!! _(전율)_",
    "페트병아 고마워!! 햄미한테 보너스를 줬구나!! _(감격)_",
    "세 번 성공 완료!! 햄미 꼬리가 빙글빙글 돌아!! _(흥분)_",
    "보너스 받았어!! 오늘 햄미 운빨 완전 최고야!! _(행복)_",
    "던지고 맞히고 또 맞혔어!! 나 진짜 멋지다!! _(자랑)_",
    "와장창 성공 세 번!! 간식 파티 열자아!! _(축제)_",
    "햄미의 작은 발이 큰일 해냈다!! 보너스 최고!! _(기쁨)_",
    "연속 성공 기록 세웠어!! 아무도 햄미 못 이겨!! _(우쭐)_",
    "페트병이 햄미 말을 다 들었어!! 보너스도 착해!! _(희열)_",
    "세 번 성공한 햄미, 오늘은 슈퍼스타야!! _(영광)_",
    "보너스까지 챙겼다!! 햄미 심장이 콩콩 뛰어!! _(두근)_",
    "이렇게 잘할 줄 몰라써!! 나 완전 뿌듯해!! _(자신감)_",
    "햄미 최고 기록 갱신!! 페트병 던지기 왕이다!! _(승리)_",
)
# 연속 성공 4회차 이상 — 보너스는 이미 다 받았지만(하루 최대 4) 매우 행복한 톤은 계속 유지.
# "세 번"/"보너스" 같은 사실과 다른 표현은 쓰지 않는다 (환각 방지 원칙과 일관성 유지).
_SUCCESS_STREAK_CONTINUED_MESSAGES = (
    "또 성공했어!! 오늘 완전 물올랐어!! _(황홀)_",
    "연속 성공 계속된다!! 이 기세 최고야!! _(신남)_",
    "역시 오늘 햄미 컨디션 최고야!! _(뿌듯)_",
    "또또 성공!! 멈출 줄을 몰라!! _(흥분)_",
    "계속 성공하니까 너무 행복해!! _(행복)_",
    "오늘 던지기는 다 성공하는 날인가 봐!! _(신남)_",
    "연속 기록이 계속 늘어나!! 최고 기분!! _(들뜸)_",
    "또 성공했어!! 오늘 진짜 잘 되는 날이야!! _(황홀)_",
    "성공이 성공을 부른다!! 완전 신나!! _(흥분)_",
    "계속 잘 던져지네!! 오늘 진짜 최고!! _(행복)_",
    "연속 성공 기록 갱신 중!! 너무 조아!! _(신남)_",
    "또 해냈어!! 이 흐름 정말 조아!! _(뿌듯)_",
    "오늘따라 손끝이 예술이야!! _(황홀)_",
    "계속되는 성공에 심장이 콩콩!! _(두근)_",
    "이 정도면 오늘의 던지기 왕이야!! _(자랑)_",
    "또또또 성공!! 완전 행복해!! _(행복)_",
    "연속 성공 계속!! 오늘 운이 좋아!! _(신남)_",
    "성공만 계속하니까 기분 최고야!! _(황홀)_",
    "역시 햄미!! 오늘 던지기는 다 맞아!! _(자신감)_",
    "계속 성공하는 하루, 정말 행복해!! _(행복)_",
)


def _format_remaining(remaining: timedelta) -> str:
    total_seconds = max(int(remaining.total_seconds()), 0)
    minutes, seconds = divmod(total_seconds, 60)
    if minutes > 0:
        return f"{minutes}분 {seconds}초"
    return f"{seconds}초"


def _reset_cooldown_abuse(stats: dict) -> dict:
    """쿨타임이 아닐 때(성공/실패) 명령어가 실행되면 남용 카운터를 0으로 되돌린다."""
    counts = dict(stats.get("cooldown_abuse_counts") or {})
    if counts.get(_METHOD, 0) != 0:
        counts[_METHOD] = 0
    return counts


async def handle(user_id: int) -> str:
    user = await get_user(user_id)
    now = datetime.now(timezone.utc)

    cooldown_until_str = user.get("plastic_cooldown_until")
    if cooldown_until_str is not None:
        cooldown_until = datetime.fromisoformat(cooldown_until_str)
        if cooldown_until > now:
            delta, current, message = await _register_cooldown_abuse(user_id, cooldown_until - now)
            return _with_notice(message, delta, current)

    stats = await ensure_daily_stats(user_id)
    total_delta = 0
    current_affection = user["affection"]

    if random.random() >= _SUCCESS_RATE:
        await set_plastic_cooldown(user_id, now + _COOLDOWN)
        await update_daily_stats(
            user_id, {"plastic_streak": 0, "cooldown_abuse_counts": _reset_cooldown_abuse(stats)}
        )
        return random.choice(_FAIL_MESSAGES)

    new_streak = stats["plastic_streak"] + 1
    update_fields = {
        "plastic_streak": new_streak,
        "cooldown_abuse_counts": _reset_cooldown_abuse(stats),
    }
    achievement_notices: list[str] = []
    already_claimed_today = False
    gained_new_affection = False

    if new_streak == 1:
        message = random.choice(_SUCCESS_STREAK1_MESSAGES)
    elif new_streak == 2:
        message = random.choice(_SUCCESS_STREAK2_MESSAGES)
    elif new_streak == _STREAK_TARGET:
        message = random.choice(_SUCCESS_STREAK_MESSAGES)
    else:
        message = random.choice(_SUCCESS_STREAK_CONTINUED_MESSAGES)

    if not stats["plastic_success_claimed"]:
        result = await add_affection(user_id, 1, _METHOD)
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]
        update_fields["plastic_success_claimed"] = True
        if result["applied_amount"] > 0:
            gained_new_affection = True
        if result["achievement_notice"]:
            achievement_notices.append(result["achievement_notice"])
    else:
        already_claimed_today = True

    # "페트병 댄스"는 성공 전체를 통틀어 최초 1회(오늘 이미 획득했어도 매일 초기화되는
    # plastic_success_claimed와 달리, award()가 전체 기간 기준으로 알아서 중복 방지한다).
    if await award_achievement(user_id, achievements.plastic_dance.ID):
        achievement_notices.append(f"🏆 업적 달성: {achievements.plastic_dance.NAME}!!")

    # 하루 동안 페트병으로 얻을 수 있는 호감도는 최대 4(성공 +1, 연속 3회 보너스 +3)로
    # 고정된다 — 4회차 이상 연속 성공해도 이 보너스는 딱 한 번만 지급된다.
    if new_streak >= _STREAK_TARGET and not stats["plastic_streak_bonus_claimed"]:
        result = await add_affection(user_id, 3, _METHOD)
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]
        update_fields["plastic_streak_bonus_claimed"] = True
        if result["applied_amount"] > 0:
            gained_new_affection = True
        if result["achievement_notice"]:
            achievement_notices.append(result["achievement_notice"])
    elif new_streak >= _STREAK_TARGET:
        already_claimed_today = True

    if new_streak >= _STREAK_TARGET and await award_achievement(user_id, achievements.plastic_dance_god.ID):
        achievement_notices.append(f"🏆 업적 달성: {achievements.plastic_dance_god.NAME}!!")

    await update_daily_stats(user_id, update_fields)
    # 같은 던지기에서 다른 마일스톤(예: 3연속 보너스)으로 실제 새 호감도를 받았다면, 그
    # 옆에 "이미 획득함" 노트를 같이 보여주면 모순돼 보이므로 진짜 아무것도 못 받았을 때만 붙인다.
    if already_claimed_today and not gained_new_affection:
        message += f"\n{_ALREADY_CLAIMED_TODAY_NOTE}"
    for notice in achievement_notices:
        message += f"\n{notice}"
    return _with_notice(message, total_delta, current_affection)


async def _register_cooldown_abuse(user_id: int, remaining: timedelta) -> tuple[int, int, str]:
    stats = await ensure_daily_stats(user_id)
    counts = dict(stats.get("cooldown_abuse_counts") or {})
    count = counts.get(_METHOD, 0) + 1
    counts[_METHOD] = count
    await update_daily_stats(user_id, {"cooldown_abuse_counts": counts})

    # 남은 시간은 스포일러 마크다운으로 가려서 직접 눌러야 보이게 한다 (사용자 확정).
    remaining_label = f" (남은 시간: ||{_format_remaining(remaining)}||)"

    if count > _COOLDOWN_ABUSE_FREE_COUNT:
        result = await add_affection(user_id, -1)
        message = random.choice(_COOLDOWN_PENALTY_MESSAGES) + remaining_label
        return result["applied_amount"], result["new_affection"], message

    user = await get_user(user_id)
    if count == _COOLDOWN_ABUSE_FREE_COUNT:
        message = random.choice(_COOLDOWN_WARNING_MESSAGES) + remaining_label
    else:
        message = random.choice(_COOLDOWN_MESSAGES) + remaining_label
    return 0, user["affection"], message


def _with_notice(message: str, delta: int, current: int) -> str:
    if delta == 0:
        return message
    return message + format_affection_notice(delta, current)
