import logging
import random

import discord

import achievements
from events import presence
from events.scheduler import mark_late_wake
from db.achievements import award as award_achievement
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.guild_sleep_state import register_mention

# 짜증 75% / 악몽 감사 25%(§21, 2026-08-27: 기존 10% -> 25%로 상향 — 이 상수는 "짜증"
# 분기가 나올 확률이라, 악몽 확률을 올리려면 이 값을 낮춰야 한다).
_ANNOYED_PROBABILITY = 0.75
_ANNOYED_DELTA = -5
_NIGHTMARE_DELTA = 5
_NIGHTMARE_METHOD = "sleep_wake_nightmare"

# UX 개선 8: 취침 중 맨션 깨움 이벤트 대사 (각 항목 = 2줄, 추임새 제거 반영)
_ANNOYED_PAIRS = (
    ("왜 깨워써… 햄미 아직 꿈에서 해바라기씨 먹고 이써써.", "흥, 인간 미워. 햄미 다시 잘 거야."),
    ("햄미 깨운 인간 누구야… 지금 깨물 각 재는 중이야.", "일단 귀찮으니까 햄미는 다시 자러 갈래."),
    ("조은 아침 아니야. 햄미한텐 아직 새벽이야.", "인사는 했으니까 다시 이불 속으로 갈 거야."),
    ("햄미 지금 엄청 졸려… 말 걸면 페트병 흔들 거야.", "그거 하기 전에 햄미 다시 잘래."),
    ("인간은 왜 아침마다 일어나는 거야? 이해할 수 업써.", "햄미는 현명한 햄스터니까 다시 자러 갈게."),
    ("햄미 분명 눈 감고 이써는데 누가 아침을 가져와써.", "아침 돌려보내고 햄미는 다시 잘 거야."),
    ("깨우지 마라써… 햄미 볼주머니도 아직 자고 이써.", "다시능 깨우지마. 햄미는 먼저 갈게."),
    ("햄미 기상 취소할래. 오늘은 일어나기 시러.", "방금 인사는 못 들은 걸로 하고 다시 잘 거야."),
    ("아침 인사는 무슨 아침 인사야… 간식도 업쓰면서.", "햄미 삐져써. 다시 자러 갈 거야."),
    ("이씨... 햄미를 깨우다니 인간... 나빠써", "깨우지마라... 햄미 다시 잔다."),
    ("아직 캄캄한데 왜 부르는 거야… 이해가 안 돼써.", "귀찮으니까 햄미는 다시 자러 갈래."),
    ("햄미 볼주머니도 잠들어 이썬는데 깨워버려써.", "다시 재워줘… 아니면 햄미가 알아서 잘게."),
    ("한밤중에 부르는 인간이 어디 이써…", "햄미는 화 안 낼 테니까 다시 잘게."),
    ("눈이 안 떠져… 지금은 진짜 아니야.", "이 얘기는 낼 하자. 햄미 다시 잔다."),
    ("잠깐 정신이 들었다가 다시 감겨써…", "인사만 받고 햄미는 다시 잘 거야."),
    ("햄미 꿈에서 막 재밌었는데 끊겨버려써.", "아쉽지만 다시 그 꿈으로 돌아갈게."),
    ("이 시간에 누가 자꾸 부르는 거야…", "대답은 했으니까 햄미는 다시 잔다."),
    ("아직 쳇바퀴도 안 돌렸는데 깨워버려써.", "그건 이따 하고 지금은 다시 잘래."),
    ("햄미 완전 곤히 자고 이썬는데…", "미안하지만 햄미는 다시 자러 갈게."),
    ("이런 시간에 깨우면 햄미 삐진다구.", "일단 삐진 채로 다시 자러 간다."),
)

_NIGHTMARE_PAIRS = (
    ("인간아 깨워줘서 고마워… 무서운 꿈 꾸고 이써써.", "이제 안심해써. 햄미는 다시 잘게."),
    ("꿈에서 해바라기씨가 전부 사라져써… 증말 무서워써.", "깨워줘서 고마워. 햄미 다시 조은 꿈 꾸러 갈게."),
    ("악몽에서 고양이가 햄미를 쫓아와써… 큰일 날 뻔해써.", "인간 덕분에 살았어. 이제 다시 자도 될 것 같아."),
    ("꿈에서 페트병을 아무리 흔들어도 소리가 안 나써…", "깨워줘서 고마워. 햄미 확인했으니까 다시 잘게."),
    ("무서운 꿈 때문에 볼주머니까지 떨려써…", "옆에 있어줘서 고마워. 햄미 다시 자러 갈래."),
    ("인간이 햄미를 두고 사라지는 꿈을 꿔써…", "여기 이써서 다행이야. 햄미 안심하고 다시 잘게."),
    ("꿈에서 간식 없는 세상에 갇혀 이써써… 너무 끔찍해.", "깨워준 인간 조아. 햄미 다시 꿈나라로 갈게."),
    ("햄미 방금 엄청 무서운 꿈 꿔써… 쪼금 울 뻔해써.", "깨워줘서 고마워. 이제 다시 잘 수 이써."),
    ("악몽 속에서 햄미가 평범한 인간이 되어버려써…", "깨고 보니 햄스터라 다행이야. 다시 안심하고 잘게."),
    ("햄미 혼자 어두운 곳에 갇힌 꿈을 꿔써…", "인간 목소리 들으니까 괜찮아졌어. 햄미 다시 잘게."),
    ("꿈에서 쳇바퀴가 멈추지를 않았어… 무서워써.", "깨워줘서 다행이야. 햄미 다시 편하게 잘게."),
    ("페트병이 갑자기 사라지는 꿈을 꿔써… 슬퍼써.", "인간 덕분에 안심했어. 다시 자러 갈게."),
    ("꿈속에서 햄미 볼주머니가 텅 비어이썬써…", "깨워줘서 고마워. 이제 편히 잘 수 이써."),
    ("무서운 그림자가 햄미를 쫓아오는 꿈이었어…", "덕분에 깼어. 햄미 안심하고 다시 잘게."),
    ("꿈에서 해바라기씨가 전부 썩어이썬써…", "깨워줘서 다행이야. 다시 조은 꿈 꿀게."),
    ("햄미가 페트병 없이 혼자 남겨지는 꿈이었어…", "옆에 있어줘서 고마워. 다시 잘게."),
    ("꿈속 세상이 갑자기 다 캄캄해져써…", "인간 목소리에 안심돼써. 다시 잘게."),
    ("무서운 소리에 쫓기는 꿈을 꾸고 이썬써…", "깨워줘서 정말 고마워. 이제 다시 잘게."),
    ("햄미가 쳇바퀴에서 못 내려오는 꿈이었어…", "덕분에 나왔어. 안심하고 다시 잘게."),
    ("꿈에서 모두가 햄미를 몰라보는 꿈이었어…", "깨고 나니까 다행이야. 다시 편히 잘게."),
)


async def handle_mention(message: discord.Message) -> None:
    """취침 시간대(00:00~06:30, 방해금지 발동 시 그날은 00:00~07:00)에 봇이 맨션됐을 때
    호출한다.

    메시지 1개 = 맨션 횟수 1회(메시지 안에서 여러 번 연속 맨션해도 1회).
    서버마다 그날 새로 뽑힌 임계치(1~10)에 도달하면 딱 1번만 깨움 이벤트가
    발생하고, 이후엔 다음 밤까지 그 서버는 방해금지(무반응) 상태가 된다. 발동 시
    그날 기상도 30분 늦춰진다(§28) — 아침 인사가 평소 인사 대신 피곤한 톤으로 나간다.
    """
    guild_id = message.guild.id
    if not await register_mention(guild_id):
        return

    await presence.enter_dnd()
    # 방해금지 발동 = 중간에 누가 깨운 것 -> 그날 기상은 30분 늦춰진 07:00로 밀린다.
    mark_late_wake()

    user_id = message.author.id
    if random.random() < _ANNOYED_PROBABILITY:
        line1, line2 = random.choice(_ANNOYED_PAIRS)
        result = await add_affection(user_id, _ANNOYED_DELTA)
        notice = format_affection_notice(result["applied_amount"], result["new_affection"])
        achievement_notice = result["achievement_notice"]
    else:
        line1, line2 = random.choice(_NIGHTMARE_PAIRS)
        result = await add_affection_uncapped(user_id, _NIGHTMARE_DELTA, _NIGHTMARE_METHOD)
        notice = format_affection_notice(_NIGHTMARE_DELTA, result["new_affection"])
        achievement_notice = result["achievement_notice"]

        # "악몽 해방"(전설) — 취침 중인 햄미를 악몽에서 깨워줬을 때.
        if await award_achievement(user_id, achievements.nightmare_freed.ID):
            extra = f"🏆 업적 달성: {achievements.format_name(achievements.nightmare_freed)}!!"
            achievement_notice = f"{achievement_notice}\n{extra}" if achievement_notice else extra

    reply_text = f"{line1}\n{line2}{notice}"
    if achievement_notice:
        reply_text += f"\n{achievement_notice}"

    try:
        await message.reply(reply_text)
    except discord.HTTPException:
        logging.exception("Failed to reply to wake event in guild %s", guild_id)
