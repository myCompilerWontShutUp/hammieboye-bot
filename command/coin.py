import random
from datetime import datetime, timedelta, timezone

from command.economy_common import format_coin_notice, maybe_append_capacity_advice
from db.affection import add_affection, format_affection_notice
from db.daily_stats import ensure_daily_stats, update_daily_stats
from db.users import claim_coin_cooldown, get_user
from db.wallet import add_coins

# 성공 시엔 쿨타임이 흐르지 않는 /페트병과 달리, /동전은 호출할 때마다 무조건 쿨타임이
# 시작된다(실패 개념 자체가 없음 — 매번 지급). 1시간이라 /페트병(30초)보다 훨씬 길다.
_COOLDOWN = timedelta(hours=1)
_METHOD = "coin"

# 쿨타임 고정 메시지는 이 횟수까지는 그냥 보여주고 그다음부터 남용 페널티 — /페트병과
# 동일한 원칙(§4-5), 남용 페널티도 동일하게 호감도 -1(동전이 아니라 호감도가 깎인다).
_COOLDOWN_ABUSE_FREE_COUNT = 3

# 지급량 상한: 지갑이 클수록 한 번에 더 많이 벌 수 있다(최소 10) — 용량을 늘릴수록
# /동전 자체의 효율도 같이 올라가게 해서 자판기 용량 업그레이드에 실질적인 유인을 준다.
_MIN_MAX_GRANT = 10
_MAX_GRANT_DIVISOR = 5

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
# 지갑이 이미 꽉 찬 상태(add_coins의 applied_amount == 0)에서 쓰면, "동전 벌었다"는
# 성공 플레이버 + 용량 부족 안내를 조합하는 대신 이 전용 문구를 쓴다 — 실제로는 한
# 푼도 못 받았는데 성공한 것처럼 보이는 게 헷갈린다는 피드백으로 분리됨.
_WALLET_FULL_LINES = (
    "어라, 지갑이 이미 꽉 찼어!! 쳇바퀴 굴려봤자 소용없겠다!! _(갸웃)_",
    "잠깐, 동전 넣을 자리가 없잖아!! 자판기에서 지갑부터 키우고 와!! _(당황)_",
    "지갑이 빵빵해서 한 푼도 더 안 들어가!! _(끄덕)_",
    "어?? 이미 가득 찼는데?? 쳇바퀴 굴린 보람이 없네!! _(허탈)_",
    "동전 자리가 하나도 없어!! 용량부터 늘리고 다시 와줄래?? _(안내)_",
    "지갑이 터지기 직전이야!! 지금은 더 못 벌어!! _(당황)_",
    "이미 꽉 차서 쳇바퀴 굴려도 소용없어!! _(멋쩍)_",
    "동전 넣을 데가 없어!! 자판기에서 용량 업그레이드 어때?? _(권유)_",
    "지갑이 이미 가득이야!! 헛수고했네!! _(웃음)_",
    "더 담을 자리가 없어!! 저금통이라도 하나 장만해봐!! _(추천)_",
    "쳇바퀴는 굴렸는데 지갑이 꽉 차서 못 받았어!! _(아쉬움)_",
    "지갑 용량 초과야!! 지금은 벌어도 의미가 없어!! _(설명)_",
    "동전이 넘칠 지경이라 안 줬어!! 용량부터 늘려줘!! _(단호)_",
    "이미 최대치야!! 자판기 들러서 지갑 키우고 오자!! _(제안)_",
    "지갑에 자리가 없어서 못 벌었어!! _(속상)_",
    "가득 찬 지갑에 동전을 더 넣을 순 없지!! _(끄덕)_",
    "용량이 꽉 차서 이번엔 헛걸음이었어!! _(허탈)_",
    "동전 자리가 없다구!! 자판기에서 확인해봐!! _(안내)_",
    "지갑이 터질 것 같아!! 지금은 더 못 담아!! _(당황)_",
    "이미 가득 차 있어서 벌어도 다 흘러넘쳐!! 용량부터 늘리자!! _(권유)_",
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

    user = await get_user(user_id)
    stats = await ensure_daily_stats(user_id)
    await update_daily_stats(user_id, {"cooldown_abuse_counts": _reset_cooldown_abuse(stats)})

    max_grant = max(_MIN_MAX_GRANT, user["max_coins"] // _MAX_GRANT_DIVISOR)
    amount = random.randint(1, max_grant)
    result = await add_coins(user_id, amount, method=_METHOD)

    if result["applied_amount"] == 0:
        # amount는 항상 1 이상이라, applied_amount가 정확히 0이면 지갑이 이미
        # max_coins에 도달해 있었다는 뜻이다 — "동전 벌었다"는 성공 문구 대신
        # 전용 문구 하나로 상황을 명확히 알려준다(용량 부족 안내도 이 문구 안에
        # 이미 포함돼 있어 maybe_append_capacity_advice를 따로 안 붙인다).
        text = random.choice(_WALLET_FULL_LINES)
    else:
        text = random.choice(_GRANT_MESSAGES)
        text += format_coin_notice(result["applied_amount"], result["new_coins"])
        text = maybe_append_capacity_advice(text, amount, result)
    if result["achievement_notice"]:
        text += f"\n{result['achievement_notice']}"
    return text
