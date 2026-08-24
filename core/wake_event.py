import logging
import random

import discord

from core import presence
from db.affection import add_affection, add_affection_uncapped, format_affection_notice
from db.guild_sleep_state import register_mention

_ANNOYED_PROBABILITY = 0.9
_ANNOYED_DELTA = -5
_NIGHTMARE_DELTA = 5
_NIGHTMARE_METHOD = "sleep_wake_nightmare"

# UX 개선 8: 취침 중 맨션 깨움 이벤트 대사 (각 항목 = 2줄, CLAUDE.md 원문 그대로 유지)
_ANNOYED_PAIRS = (
    ("왜 깨워써… 햄미 아직 꿈에서 해바라기씨 먹고 이써써.", "흥, 인간 미워. 햄미 다시 잘 거야뾱."),
    ("햄미 깨운 인간 누구야… 지금 깨물 각 재는 중이야.", "일단 귀찮으니까 햄미는 다시 자러 갈래."),
    ("조은 아침 아니야. 햄미한텐 아직 새벽이야.", "인사는 했으니까 다시 이불 속으로 갈 거야뾱."),
    ("햄미 지금 엄청 졸려… 말 걸면 페트병 흔들 거야.", "쟈쟈쟉 하기 전에 햄미 다시 잘래."),
    ("인간은 왜 아침마다 일어나는 거야? 이해할 수 업써.", "햄미는 현명한 햄스터니까 다시 자러 갈게."),
    ("햄미 분명 눈 감고 이써는데 누가 아침을 가져와써.", "아침 돌려보내고 햄미는 다시 잘 거야뾱."),
    ("깨우지 마라써… 햄미 볼주머니도 아직 자고 이써.", "다시능 깨우지마. 햄미는 먼저 갈게."),
    ("햄미 기상 취소할래. 오늘은 일어나기 시러.", "방금 인사는 못 들은 걸로 하고 다시 잘 거야."),
    ("아침 인사는 무슨 아침 인사야… 간식도 업쓰면서.", "햄미 삐져써. 다시 자러 갈 거야뾱."),
    ("이씨... 햄미를 깨우다니 인간... 나빠써", "깨우지마라... 햄미 다시 잔다뾱."),
)

_NIGHTMARE_PAIRS = (
    ("인간아 깨워줘서 고마워… 무서운 꿈 꾸고 이써써.", "이제 안심해써. 햄미는 다시 잘게뾱."),
    ("꿈에서 해바라기씨가 전부 사라져써… 증말 무서워써.", "깨워줘서 고마워. 햄미 다시 조은 꿈 꾸러 갈게."),
    ("악몽에서 고양이가 햄미를 쫓아와써… 큰일 날 뻔해써.", "인간 덕분에 살았어. 이제 다시 자도 될 것 같아뾱."),
    ("꿈에서 페트병을 아무리 흔들어도 소리가 안 나써…", "깨워줘서 고마워. 햄미 확인했으니까 다시 잘게."),
    ("무서운 꿈 때문에 볼주머니까지 떨려써…", "옆에 있어줘서 고마워. 햄미 다시 자러 갈래."),
    ("인간이 햄미를 두고 사라지는 꿈을 꿔써…", "여기 이써서 다행이야. 햄미 안심하고 다시 잘게뾱."),
    ("꿈에서 간식 없는 세상에 갇혀 이써써… 너무 끔찍해.", "깨워준 인간 조아. 햄미 다시 꿈나라로 갈게."),
    ("햄미 방금 엄청 무서운 꿈 꿔써… 쪼금 울 뻔해써.", "깨워줘서 고마워뾱. 이제 다시 잘 수 이써."),
    ("악몽 속에서 햄미가 평범한 인간이 되어버려써…", "깨고 보니 햄스터라 다행이야. 다시 안심하고 잘게."),
    ("햄미 혼자 어두운 곳에 갇힌 꿈을 꿔써…", "인간 목소리 들으니까 괜찮아졌어. 햄미 다시 잘게뾱."),
)


async def handle_mention(message: discord.Message) -> None:
    """취침 시간대(00:00~06:30)에 봇이 맨션됐을 때 호출한다.

    메시지 1개 = 맨션 횟수 1회(메시지 안에서 여러 번 연속 맨션해도 1회).
    서버마다 그날 새로 뽑힌 임계치(1~10)에 도달하면 딱 1번만 깨움 이벤트가
    발생하고, 이후엔 다음 밤까지 그 서버는 방해금지(무반응) 상태가 된다.
    """
    guild_id = message.guild.id
    if not await register_mention(guild_id):
        return

    await presence.enter_dnd()

    user_id = message.author.id
    if random.random() < _ANNOYED_PROBABILITY:
        line1, line2 = random.choice(_ANNOYED_PAIRS)
        result = await add_affection(user_id, _ANNOYED_DELTA)
        notice = format_affection_notice(result["applied_amount"], result["new_affection"])
    else:
        line1, line2 = random.choice(_NIGHTMARE_PAIRS)
        new_affection = await add_affection_uncapped(user_id, _NIGHTMARE_DELTA, _NIGHTMARE_METHOD)
        notice = format_affection_notice(_NIGHTMARE_DELTA, new_affection)

    try:
        await message.reply(f"{line1}\n{line2}{notice}")
    except discord.HTTPException:
        logging.exception("Failed to reply to wake event in guild %s", guild_id)
