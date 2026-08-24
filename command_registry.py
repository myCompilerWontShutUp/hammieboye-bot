from command.base import Command
from command.help.help import handle as help_handle
from command.info.info import handle as info_handle
from command.plastic.plastic import handle as plastic_handle

# 명령어별 prefix(별칭) 목록과 기타 설정은 전부 여기 한 곳에서 관리한다.
# 실제 실행 코드는 command/<이름>/<이름>.py의 handle()에만 둔다.
COMMANDS = (
    Command(
        name="plastic",
        aliases=("페트병", "플라스틱 병", "플라스틱"),
        handler=plastic_handle,
    ),
    Command(
        name="help",
        aliases=("도움", "헬프", "명령어", "리스트"),
        handler=help_handle,
    ),
    Command(
        name="info",
        aliases=("나", "정보", "호감도"),
        handler=info_handle,
    ),
)
