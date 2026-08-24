async def handle(user_id: int) -> str:
    # command_registry가 이 파일을 import하므로, 순환 참조를 피하려고 호출 시점에 지연 import한다.
    import command_registry

    lines = ["햄미가 아는 명령어들이야, 이렇게 불러줘!!"]
    for command in command_registry.COMMANDS:
        lines.append(f"- {command.aliases[0]}")
    return "\n".join(lines)
