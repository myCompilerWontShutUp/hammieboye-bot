import random
from datetime import datetime, timedelta, timezone

import levels
from command.economy_common import format_coin_notice
from db.affection import add_affection, format_affection_notice
from db.daily_stats import claim_coin_daily_use, ensure_daily_stats, update_daily_stats
from db.users import claim_coin_cooldown, get_user
from db.wallet import add_coins

# 성공 시엔 쿨타임이 흐르지 않는 /페트병과 달리, /동전은 호출할 때마다 무조건 쿨타임이
# 시작된다(실패 개념 자체가 없음 — 매번 지급). 1시간이라 /페트병(30초)보다 훨씬 길다.
_COOLDOWN = timedelta(hours=1)
_METHOD = "coin"

# 쿨타임 고정 메시지는 이 횟수까지는 그냥 보여주고 그다음부터 남용 페널티 — /페트병과
# 동일한 원칙(§4-5), 남용 페널티도 동일하게 호감도 -1(동전이 아니라 호감도가 깎인다).
_COOLDOWN_ABUSE_FREE_COUNT = 3

# 하루 최대 사용 횟수(2026-09-05 신규) — 쿨타임(1시간)과 별개로, 쿨타임이 다 지났어도
# 오늘 이미 이만큼 벌었으면 더 못 받는다. daily_stats.coin_claims_today로 원자적 판정.
_DAILY_CLAIM_LIMIT = 3

# 지급량은 이제 고정이다(2026-09-05, 보유 상한 폐지와 함께 랜덤 범위도 폐지) — 기본
# 1개 + 자판기 그랜트 부스터 품목으로 늘린 coin_grant_bonus. 밑줄을 뗀 공개 이름
# (2026-09-11) — command/assets.py가 "/동전 사용 시 기본 획득량" 표시에 재사용한다
# (command/eat.py가 use.py에 재사용시킬 때와 동일한 원칙, CLAUDE.md §15 참고).
BASE_GRANT = 1

# 레벨별 확률로 2배 또는 5배를 물어온다(2026-09-09 2배 신설, 2026-09-10 레벨
# 시스템 도입으로 고정 10% → levels.Level.double_drop_chance로 레벨업할수록 상승,
# 같은 날 5배 신설 — levels.Level.quintuple_drop_chance) — coin_grant_bonus까지
# 합산한 최종 지급량 전체에 곱해진다. apply_day_multiplier(날짜 배율)와는
# 독립적으로 중첩된다(의도된 동작 — 보너스와 특별한 날 배율이 겹치면 더 크게 받을
# 수 있음). 5배와 2배는 하나의 판정을 공유(누적 구간 방식)해 서로 배타적이다 —
# [0, 5배 확률) 구간이면 5배, 그다음 [5배 확률, 5배+2배 확률) 구간이면 2배,
# 나머지는 평소 지급.
_BONUS_MULTIPLIER = 2
_SUPER_BONUS_MULTIPLIER = 5

_GRANT_MESSAGES = (
    "쳇바퀴를 신나게 굴렸더니 동전이 떨어져써!! _(신남)_",
    "열심히 굴렸다!! 짤그락, 동전이야!! _(뿌듯)_",
    "쳇바퀴 굴리기 완료!! 동전 획득!! _(당당)_",
    "빙글빙글 돌았더니 동전이 나왔어!! _(신기)_",
    "오늘도 열심히 굴려써!! 동전 냠냠!! _(만족)_",
    "쳇바퀴 아래에 동전이 떨어져 있었어!! _(발견)_",
    "부지런히 굴렸더니 보상이 왔어!! _(자랑)_",
    "쳇바퀴 굴리기, 오늘도 성공!! _(뿌듯)_",
    "짤그락짤그락!! 동전 벌었다!! _(신남)_",
    "열심히 뛰었더니 동전이 모여써!! _(뿌듯)_",
    "쳇바퀴가 동전을 뱉어냈어!! _(놀람)_",
    "오늘의 노동, 동전으로 보답받았어!! _(뿌듯)_",
    "빙글빙글 돌리기 완료!! 수당 나왔다!! _(신남)_",
    "쳇바퀴 굴리느라 힘들었지만 조아!! _(뿌듯)_",
    "동전이 데굴데굴 굴러왔어!! _(신기)_",
    "열심히 일한 만큼 동전이 생겨써!! _(당당)_",
    "쳇바퀴 밑에서 동전을 주워써!! _(발견)_",
    "부지런한 햄미에게 동전이!! _(자랑)_",
    "오늘도 착실하게 동전을 벌었어!! _(뿌듯)_",
    "쳇바퀴 굴리기, 동전으로 정산 완료!! _(만족)_",
)

# 10% 보너스 전용 문구 풀(2026-09-09 신규) — 평소 지급 문구와 구분해서 "더 많이
# 물어왔다"는 걸 확실히 알 수 있게 한다.
_BONUS_GRANT_MESSAGES = (
    "저쪽에 동전이 많아써!! 그래서 평소보다 더 많이 들고왓써!! _(신남)_",
    "오늘은 대박이야!! 동전을 두 배로 물어왔어!! _(흥분)_",
    "쳇바퀴 굴리다가 동전 무더기를 발견해써!! _(놀람)_",
    "운 좋게 동전이 잔뜩 쌓여 있었어!! 다 들고왔지!! _(자랑)_",
    "오늘따라 동전이 유독 많이 떨어져써!! _(신기)_",
    "숨겨진 동전 창고를 찾았어!! 두 배로 챙겨왔다!! _(뿌듯)_",
    "짤그락짤그락!! 오늘은 유난히 두둑해!! _(신남)_",
    "쳇바퀴 밑이 동전으로 가득했어!! 다 쓸어왔어!! _(황홀)_",
    "오늘 운이 좋았나 봐!! 동전이 두 배야!! _(들뜸)_",
    "동전이 우르르 쏟아져 나왔어!! _(놀람)_",
    "이건 대박이다!! 평소보다 훨씬 많이 벌었어!! _(환호)_",
    "저쪽 구석에 동전이 잔뜩 있었어!! 냠냠 다 챙겼지!! _(만족)_",
    "오늘 쳇바퀴가 유독 후하게 주네!! _(신남)_",
    "동전 대박!! 두 배로 받아왔어!! _(흥분)_",
    "우연히 동전 더미를 발견했어!! 놓칠 수 없었지!! _(자랑)_",
    "오늘은 운수 좋은 날이야!! 동전이 두 배!! _(뿌듯)_",
    "쳇바퀴 굴리다 동전 잭팟 터졌어!! _(황홀)_",
    "이렇게 많이 물어온 건 처음이야!! _(놀람)_",
    "동전이 두 배로 짤랑거려!! 기분 최고야!! _(신남)_",
    "오늘의 노동이 두 배로 보답받았어!! _(뿌듯)_",
)

# 5배 보너스 전용 문구 풀(2026-09-10 신규) — 2배보다 훨씬 희귀하니 "초대박" 급으로
# 더 호들갑스럽게 표현해서 확실히 구분되게 한다.
_SUPER_BONUS_GRANT_MESSAGES = (
    "이게 무슨 일이야?!?! 동전이 다섯 배나 쏟아졌어!! _(경악)_",
    "초대박!! 오늘 동전이 다섯 배로 터졌어!! _(황홀)_",
    "말도 안 돼!! 동전이 다섯 배로 쌓여 있었어!! _(충격)_",
    "이런 행운이!! 다섯 배로 몽땅 쓸어왔다!! _(흥분)_",
    "쳇바퀴 아래가 동전으로 파묻혀 있었어!! 다섯 배!! _(경악)_",
    "역대급 잭팟이야!! 동전 다섯 배 획득!! _(황홀)_",
    "이건 진짜 대박이다!! 다섯 배나 물어왔어!! _(흥분)_",
    "믿기지 않아!! 오늘은 다섯 배로 운이 트였어!! _(놀람)_",
    "동전 창고를 통째로 발견한 기분이야!! 다섯 배!! _(경악)_",
    "이렇게까지 많이 물어온 적은 처음이야!! 다섯 배!! _(충격)_",
    "오늘은 완전 럭키데이야!! 동전 다섯 배!! _(신남)_",
    "쳇바퀴 신이 도와준 게 틀림없어!! 다섯 배!! _(황홀)_",
    "동전 폭탄이 터진 줄 알았어!! 다섯 배야!! _(경악)_",
    "이 정도면 전설이야!! 다섯 배 획득!! _(흥분)_",
    "오늘따라 동전이 유난히 넘쳐났어!! 다섯 배!! _(놀람)_",
    "믿을 수 없는 행운!! 다섯 배로 받아왔어!! _(황홀)_",
    "쳇바퀴 밑에 보물창고가 있었나 봐!! 다섯 배!! _(경악)_",
    "오늘 운은 진짜 제대로다!! 동전 다섯 배!! _(신남)_",
    "이런 날이 다 있네!! 다섯 배나 벌었어!! _(흥분)_",
    "완전 초대박!! 동전이 다섯 배로 짤랑거려!! _(황홀)_",
)

_COOLDOWN_MESSAGES = (
    "방금 쳇바퀴 다 굴렸어!! 아직 숨 고르는 중이야!! _(헥헥)_",
    "쪼금만 기다려줘!! 다리가 아직 쉬는 중이야!! _(피곤)_",
    "쳇바퀴는 아직 식지도 안 았어!! 나중에 다시 와줘!! _(당황)_",
    "방금 열심히 굴렸잖아!! 좀 쉬어야 다시 벌지!! _(헐떡)_",
    "주변에 동전이 하나도 안 보여!! _(두리번)_",
    "쳇바퀴가 아직 쉬는 중이야!! 조금만 기다려!! _(멍함)_",
    "또 굴리고 싶은데 다리에 힘이 없어!! _(답답)_",
    "힘이 다 빠져서 지금은 못 굴리겠어!! _(지침)_",
    "다리가 아직도 후들후들해!! 조금만 기다려줘!! _(후들)_",
    "쳇바퀴 버튼이 잠들어써!! 깨워도 꿈쩍 안 해!! _(졸림)_",
    "방금 번 힘을 충전하는 중이야!! _(충전)_",
    "또 벌고 싶은데 너무 힘들어서 못 찾겠어!! _(삐짐)_",
    "주변에 동전이 전혀 안 보여!! 좀 더 찾아봐야 해!! _(체념)_",
    "이제 좀 벌 수 있으려나 했는데 아직이었어!! _(실망)_",
    "한 번 열심히 굴렸더니 완전히 지쳐써!! 잠깐 쉴게!! _(휴식)_",
    "왜 또 안 벌려지지?? 아직 힘이 안 돌아왔나 봐!! _(깨달음)_",
    "쳇바퀴를 다시 굴리려는데 다리가 안 움직여!! _(끙)_",
    "다리는 멀쩡한데 동전이 안 보여!! _(애원)_",
    "조아, 기다릴게!! 힘 차면 바로 굴릴 거야!! _(의지)_",
    "앗, 아직은 못 벌어!! 쳇바퀴 옆에서 대기할게!! _(대기)_",
)
_COOLDOWN_WARNING_MESSAGES = (
    "아직 준비 안됬다고!! 경고야, 시간 되면 그때 굴려!! _(화남)_",
    "자꾸 이러면 진짜 삐질 거야!! 이게 마지막 경고야!! _(경고)_",
    "아직 힘이 없다니까?? 계속 이러면 화낼 거야!! _(짜증)_",
    "한 번만 더 그러면 진짜 화날 거야!! 기다려!! _(경고)_",
    "이제 진짜 마지막이야!! 좀 기다려줘!! _(단호)_",
    "자꾸 재촉하면 햄미도 화낼 거야!! 이게 경고야!! _(화남)_",
    "그만 좀 눌러!! 이번이 마지막 봐주는 거야!! _(짜증)_",
    "아직 못 벌어준다니까!! 다음엔 진짜 화낼 거야!! _(경고)_",
    "몇 번째야 진짜!! 이제 정말 마지막 경고야!! _(답답)_",
    "햄미 슬슬 삐지려 그래!! 조금만 기다려!! _(삐짐)_",
    "이게 진짜진짜 마지막 기회야!! 조금만 참아줘!! _(경고)_",
    "계속 이러면 화낼 준비 돼써!! 마지막 경고야!! _(화남)_",
    "아직 안 된다고 했잖아!! 이번이 진짜 끝이야!! _(단호)_",
    "한번만 더 누르면 삐질 거야!! 진짜야!! _(삐짐)_",
    "자꾸 그러면 나 화낼 거야!! 마지막으로 말해!! _(경고)_",
    "슬슬 인내심이 바닥나!! 이게 마지막이야!! _(짜증)_",
    "이번이 마지막 경고라구!! 진짜 좀 기다려!! _(단호)_",
    "계속하면 진짜 삐질 거야!! 마지막 기회야!! _(경고)_",
    "햄미 화나기 직전이야!! 제발 좀 기다려줘!! _(짜증)_",
    "마지막으로 경고할게!! 더 누르면 화낼 거야!! _(화남)_",
)
# 쿨타임은 다 지났어도 오늘 이미 3번 다 벌었으면 못 받는다(2026-09-05 신규) — 메타
# 발언("하루 한도") 없이 힘들어서/피곤해서 못 굴리겠다는 자연스러운 이유로 표현한다.
_DAILY_LIMIT_LINES = (
    "오늘은 이만큼 굴렸으면 충분해!! 내일 또 굴릴게!! _(만족)_",
    "오늘치 쳇바퀴는 이미 다 굴렸어!! 내일 다시 와줘!! _(뿌듯)_",
    "너무 많이 굴려서 오늘은 힘이 다 빠져써!! _(지침)_",
    "오늘은 여기까지!! 다리가 완전히 풀려버려써!! _(헥헥)_",
    "이제 오늘은 진짜 못 굴리겠어!! 푹 쉬어야 대!! _(피곤)_",
    "오늘 몫은 다 채웠어!! 내일 또 보자!! _(끄덕)_",
    "쳇바퀴를 너무 굴려서 오늘은 여기까지야!! _(헐떡)_",
    "오늘은 이 정도로 만족할래!! 내일 또 굴려줄게!! _(웃음)_",
    "오늘 벌 만큼은 다 벌어써!! 내일 다시 굴려보자!! _(뿌듯)_",
    "힘을 다 써버려써!! 오늘은 이만 쉴게!! _(지침)_",
    "오늘의 쳇바퀴는 끝났어!! 내일 만나!! _(방긋)_",
    "이제 다리가 후들거려서 못 굴려!! 내일 다시 와줘!! _(후들)_",
    "오늘 분량은 다 채웠어!! 조금만 기다려줘!! _(만족)_",
    "너무 열심히 굴려서 오늘은 지쳐써!! _(헥헥)_",
    "오늘은 충분히 굴렸어!! 내일 또 해보자!! _(끄덕)_",
    "이제 오늘 몫은 끝!! 내일 다시 만나자!! _(웃음)_",
    "쳇바퀴 굴리기, 오늘은 여기까지가 한계야!! _(지침)_",
    "오늘은 힘을 다 써서 더는 못 굴리겠어!! _(피곤)_",
    "오늘치는 다 굴려써!! 내일 또 부탁해!! _(방긋)_",
    "이 정도면 오늘 할 만큼 했어!! 내일 봐!! _(뿌듯)_",
)

_COOLDOWN_PENALTY_MESSAGES = (
    "몇 번을 말해야 알아들어!! 이제 진짜 화났어!! _(화남)_",
    "결국 화나버려써!! 기다려달랬잖아!! _(짜증)_",
    "하지 말라고 했잖아!! 이제 삐졌어!! _(삐짐)_",
    "진짜 화났어!! 기다리라니까 무시하지 마!! _(화남)_",
    "이제 완전히 삐져버렸어!! 그만해줘!! _(삐짐)_",
    "말을 안 들으니까 이렇게 대는 거야!! _(단호)_",
    "햄미 인내심 바닥나써!! 진짜 화났어!! _(화남)_",
    "계속 이러면 곤란해!! 이제 화날 거야!! _(짜증)_",
    "경고했는데도 또 그래!! 실망이야!! _(실망)_",
    "이제 그만 좀!! 햄미 완전 삐졌어!! _(삐짐)_",
    "기다리라고 몇 번을 말해!! _(답답)_",
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


def _format_remaining(remaining: timedelta) -> str:
    total_seconds = max(int(remaining.total_seconds()), 0)
    minutes, seconds = divmod(total_seconds, 60)
    if minutes > 0:
        return f"{minutes}분 {seconds}초"
    return f"{seconds}초"


def _reset_cooldown_abuse(stats: dict) -> dict:
    counts = dict(stats.get("cooldown_abuse_counts") or {})
    if counts.get(_METHOD, 0) != 0:
        counts[_METHOD] = 0
    return counts


async def _register_cooldown_abuse(user_id: int, remaining: timedelta) -> tuple[int, int, str]:
    stats = await ensure_daily_stats(user_id)
    counts = dict(stats.get("cooldown_abuse_counts") or {})
    count = counts.get(_METHOD, 0) + 1
    counts[_METHOD] = count
    await update_daily_stats(user_id, {"cooldown_abuse_counts": counts})

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


def _with_affection_notice(message: str, delta: int, current: int) -> str:
    if delta == 0:
        return message
    return message + format_affection_notice(delta, current)


async def handle(user_id: int) -> str:
    now = datetime.now(timezone.utc)

    # 쿨타임이 아직 안 지났어도 오늘 3번 다 썼으면 어차피 못 받으니, 먼저 오늘 횟수부터
    # 빠르게 확인한다(불필요한 쿨타임 클레임을 아낀다) — 최종 권한은 아래 원자적
    # claim_coin_daily_use가 가진다(아주 드문 동시 요청 경합 대비).
    stats = await ensure_daily_stats(user_id)
    if stats.get("coin_claims_today", 0) >= _DAILY_CLAIM_LIMIT:
        return random.choice(_DAILY_LIMIT_LINES)

    # 확인 후 갱신하는 대신, "쿨타임이 지금 끝나 있을 때만" 원자적으로 새 쿨타임을
    # 먼저 선점한다(claim_coin_cooldown) — 두 요청이 쿨타임 만료 직후 거의 동시에
    # 들어와도 이중 지급이 안 생기게 하는 DB 레벨 가드(TOCTOU 방지).
    claimed = await claim_coin_cooldown(user_id, now + _COOLDOWN)
    if not claimed:
        # 클레임 실패 = 아직 쿨타임 중 — 남은 시간 계산을 위해 최신 값을 다시 읽는다.
        user = await get_user(user_id)
        cooldown_until = datetime.fromisoformat(user["coin_cooldown_until"])
        delta, current, message = await _register_cooldown_abuse(user_id, cooldown_until - now)
        return _with_affection_notice(message, delta, current)

    if not await claim_coin_daily_use(user_id):
        # 아주 드문 경합(쿨타임이 막 끝난 순간 동시 요청)으로 오늘 몫이 방금 다
        # 채워진 경우 — 쿨타임은 이미 새로 잡혔지만 코인은 지급하지 않는다.
        return random.choice(_DAILY_LIMIT_LINES)

    await update_daily_stats(user_id, {"cooldown_abuse_counts": _reset_cooldown_abuse(stats)})

    user = await get_user(user_id)
    base_amount = BASE_GRANT + user["coin_grant_bonus"]
    level = levels.get_level_for_xp(user["total_xp"])
    # 5배/2배는 하나의 굴림을 공유하는 배타적 구간이다 — [0, 5배 확률)이면 5배,
    # 그다음 구간이면 2배, 나머지는 평소 지급(코드 상단 주석 참고).
    roll = random.random()
    if roll < level.quintuple_drop_chance:
        multiplier, bonus_label, text_pool = _SUPER_BONUS_MULTIPLIER, "x5배 (동전 초대박 당첨)", _SUPER_BONUS_GRANT_MESSAGES
    elif roll < level.quintuple_drop_chance + level.double_drop_chance:
        multiplier, bonus_label, text_pool = _BONUS_MULTIPLIER, "x2배 (동전 행운 당첨)", _BONUS_GRANT_MESSAGES
    else:
        multiplier, bonus_label, text_pool = 1, None, _GRANT_MESSAGES
    amount = base_amount * multiplier
    result = await add_coins(user_id, amount, method=_METHOD, apply_day_multiplier=True)

    text = random.choice(text_pool)
    text += format_coin_notice(result["applied_amount"], result["new_coins"], bonus_label=bonus_label)
    if result["achievement_notice"]:
        text += f"\n{result['achievement_notice']}"
    return text
