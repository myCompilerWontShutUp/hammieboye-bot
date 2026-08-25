import random
from datetime import datetime, timedelta, timezone

from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.users import get_user, set_plastic_cooldown

# CLAUDE.md 섹션 3-1
_COOLDOWN = timedelta(minutes=10)
_METHOD = "plastic_bottle"
_STREAK_TARGET = 3
_SUCCESS_RATE = 0.5

# 섹션 4-5: 쿨타임 고정 메시지를 이 횟수까지는 그냥 보여주고, 그다음부터 남용 페널티
_COOLDOWN_ABUSE_FREE_COUNT = 3

# 4개 상황별 고정 문구 풀 (API로 생성 후 검수해서 고정, 사용자 요청 — 최소 20개씩).
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
_SUCCESS_MESSAGES = (
    "성공해써!! 햄미 춤춘다 빙글빙글!! _(환희)_",
    "페트병아 잘 봐!! 햄미가 날아올랐어!! _(신남)_",
    "우와아!! 던지기 완전 대박 나써!! _(기쁨)_",
    "햄미 우승이닷!! 발바닥이 저절로 움직여!! _(흥분)_",
    "빙글빙글 흔들흔들!! 오늘은 햄미의 날이야!! _(쾌감)_",
    "성공이라니!! 간식 열 개 먹은 기분이야!! _(행복)_",
    "페트병 던지고 춤까지 완벽해써!! _(뿌듯)_",
    "햄미 최고!! 꼬리까지 씰룩씰룩해!! _(들뜸)_",
    "이얏호!! 병이 멀리 날아가서 너무 조아!! _(환호)_",
    "춤춰라 햄미!! 오늘 무대는 페트병 옆이야!! _(열광)_",
    "성공 성공!! 햄미 발이 멈추질 않네!! _(즐거움)_",
    "우다다다!! 던지기도 춤도 다 해냈어!! _(통쾌)_",
    "햄미가 해냈다구!! 꼬물꼬물 댄스 간다!! _(자랑)_",
    "페트병이 빙 날아갔어!! 햄미 마음도 둥실이야!! _(황홀)_",
    "짝짝짝!! 햄미 춤 실력도 꽤 조은데?? _(만족)_",
    "성공했으니 축제다!! 발을 쿵쿵 굴러야지!! _(축제)_",
    "와아아!! 햄미의 던지기 기술이 빛났어!! _(감탄)_",
    "춤추면서 또 던질까?? 오늘은 자신감 만땅이야!! _(자신)_",
    "페트병도 햄미도 신나서 빙글빙글이야!! _(경쾌)_",
    "대성공!! 햄미는 지금 완전 날아갈 것 같아!! _(환희)_",
)
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


async def handle(user_id: int) -> str:
    user = await get_user(user_id)
    now = datetime.now(timezone.utc)

    cooldown_until = user.get("plastic_cooldown_until")
    if cooldown_until is not None and datetime.fromisoformat(cooldown_until) > now:
        delta, current = await _register_cooldown_abuse(user_id)
        return _with_notice(random.choice(_COOLDOWN_MESSAGES), delta, current)

    stats = await ensure_daily_stats(user_id)
    total_delta = 0
    current_affection = user["affection"]

    if random.random() >= _SUCCESS_RATE:
        await set_plastic_cooldown(user_id, now + _COOLDOWN)
        await update_daily_stats(user_id, {"plastic_streak": 0})
        return random.choice(_FAIL_MESSAGES)

    new_streak = stats["plastic_streak"] + 1
    update_fields = {"plastic_streak": new_streak}
    message = random.choice(_SUCCESS_MESSAGES)

    if not stats["plastic_success_claimed"]:
        result = await add_affection(user_id, 1, _METHOD)
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]
        update_fields["plastic_success_claimed"] = True

    if new_streak >= _STREAK_TARGET and not stats["plastic_streak_bonus_claimed"]:
        result = await add_affection(user_id, 3, _METHOD)
        total_delta += result["applied_amount"]
        current_affection = result["new_affection"]
        update_fields["plastic_streak_bonus_claimed"] = True
        message = random.choice(_SUCCESS_STREAK_MESSAGES)

    await update_daily_stats(user_id, update_fields)
    return _with_notice(message, total_delta, current_affection)


async def _register_cooldown_abuse(user_id: int) -> tuple[int, int]:
    stats = await ensure_daily_stats(user_id)
    counts = dict(stats.get("cooldown_abuse_counts") or {})
    count = counts.get(_METHOD, 0) + 1
    counts[_METHOD] = count
    await update_daily_stats(user_id, {"cooldown_abuse_counts": counts})

    if count > _COOLDOWN_ABUSE_FREE_COUNT:
        result = await add_affection(user_id, -1)
        return result["applied_amount"], result["new_affection"]

    user = await get_user(user_id)
    return 0, user["affection"]


def _with_notice(message: str, delta: int, current: int) -> str:
    if delta == 0:
        return message
    return message + format_affection_notice(delta, current)
