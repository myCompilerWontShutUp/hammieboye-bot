"""`/봇정보-규칙`·`/봇정보-확률공개`가 보여주는 내용(내기·도박 6개 게임의 규칙과 확률)을
자연어로도 물어볼 수 있게 하는 RAG 문서(2026-09-10 신규). 규칙/확률 텍스트 자체는 각
게임 파일(command/bet.py·slot.py·horse_race.py·double_or_nothing.py)과
command/probability_info.py에 이미 있는 값을 그대로 재사용한다 — 중복 작성하면 나중에
한쪽만 갱신되어 내용이 어긋날 위험이 있어서다."""

from command.bet import ODD_EVEN_RULE_TEXT, RPS_RULE_TEXT, UPDOWN_RULE_TEXT
from command.double_or_nothing import DOUBLE_OR_NOTHING_RULE_TEXT
from command.economy_common import MAX_BET_BETTING, MAX_BET_GAMBLING
from command.horse_race import HORSE_RACE_RULE_TEXT
from command.probability_info import (
    DOUBLE_OR_NOTHING_PROBABILITY_TEXT,
    ODD_EVEN_PROBABILITY_TEXT,
    RPS_PROBABILITY_TEXT,
    UPDOWN_PROBABILITY_TEXT,
    black_market_field_value,
    horse_race_field_value,
    slot_field_value,
)
from command.slot import SLOT_MACHINE_RULE_TEXT


def get_text() -> str:
    return (
        "동전을 걸고 즐기는 미니게임(`/내기`·`/도박`) 규칙과 확률:\n"
        f"- 내기(홀짝·가위바위보·업다운)는 배팅액 1~{MAX_BET_BETTING}동전으로 "
        "비교적 안전하다.\n"
        f"- 도박(슬롯머신·승부예측)은 배팅액 1~{MAX_BET_GAMBLING:,}동전으로 위험한 "
        "만큼 크게 벌 수 있다.\n"
        "- 더블오어낫띵은 배팅액을 직접 입력하지 않고 올인/하프 중 선택하며 상한이 없다.\n"
        "- 게임 진행 중 10분 동안 아무것도 고르지 않으면 포기한 것으로 간주해 배팅액을 "
        "전부 잃는다.\n\n"
        f"[규칙]\n{ODD_EVEN_RULE_TEXT}\n\n{RPS_RULE_TEXT}\n\n{UPDOWN_RULE_TEXT}\n\n"
        f"{SLOT_MACHINE_RULE_TEXT}\n\n{HORSE_RACE_RULE_TEXT}\n\n{DOUBLE_OR_NOTHING_RULE_TEXT}\n\n"
        "[승률/확률]\n"
        f"- 홀짝: {ODD_EVEN_PROBABILITY_TEXT}\n"
        f"- 가위바위보: {RPS_PROBABILITY_TEXT}\n"
        f"- 업다운: {UPDOWN_PROBABILITY_TEXT}\n"
        f"- 슬롯머신: {slot_field_value()}\n"
        f"- 승부예측: {horse_race_field_value()}\n"
        f"- 더블오어낫띵: {DOUBLE_OR_NOTHING_PROBABILITY_TEXT}\n"
        f"- 암시장 확률형 간식: {black_market_field_value()}\n\n"
        "더 자세한 내용이나 표로 정리된 형태는 `/봇정보-규칙`·`/봇정보-확률공개`에서 "
        "확인할 수 있다고 안내해줘야 한다.\n"
        "이 자료에 없는 규칙이나 확률을 지어내서 알려주면 안 된다."
    )
