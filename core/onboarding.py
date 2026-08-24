from command.base import normalize

# CLAUDE.md 섹션 5: 별칭 없이 이 문구 하나만 동의로 인정한다.
_CONSENT_PHRASE = "동의"

NOTICE = (
    "안녕! 나는 햄미야 뾱!! 너랑 친해지려면 채팅 횟수, 도와준 횟수, "
    "호감도, 동의 여부·날짜 같은 걸 저장해야 대! 괜찮으면 `해미야 동의`라고 "
    "말해줘 쟈쟈쟉!!"
)

CONFIRMED = "조아!! 이제부터 친하게 지내자 뾱!! 잘 부탁해 햄햄!!"


def is_consent_phrase(text: str) -> bool:
    return normalize(text) == normalize(_CONSENT_PHRASE)
