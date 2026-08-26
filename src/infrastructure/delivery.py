"""발송 채널 — 완성된 문자열을 외부 세계로 내보낸다.

여기가 infrastructure인 이유: 이 모듈은 리포트에 **무엇이 쓰여 있는지
전혀 모른다.** 완성된 문자열을 받아 디스크나 SMTP 소켓에 밀어넣을 뿐이다.
"무엇을 어떻게 보여줄지"는 presentation/renderers.py의 몫이다.

    md → HTML 로 바꾸면?   내용이 달라진다  → presentation
    파일 → 메일 로 바꾸면?  경로만 달라진다  → infrastructure

채널은 config로 on/off 한다. 멱등키는 State의 delivered 필드로 관리되므로
(체크포인터에 얹혀감) 여기서는 기록을 남기지 않는다. 발송 노드가
delivered를 보고 skip을 판단한다.
"""

from __future__ import annotations

from pathlib import Path

from src.constants import OUTPUT_ROOT


class FileDelivery:
    """md 파일로 저장."""

    channel = "file"
    #: 정해진 수신자에게 밀어내는 채널이 아니다. 질의 실행에서도 남긴다.
    broadcast = False

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or OUTPUT_ROOT

    async def deliver(self, key: str, content: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{key}.md"
        path.write_text(content, encoding="utf-8")
        return str(path)


class MailDelivery:
    """메일 발송. 실제 SMTP 호출은 주석 처리했다."""

    channel = "mail"
    #: 정규 수신자 전원에게 나간다. 사람이 임시로 던진 질의 결과는 보내지 않는다.
    broadcast = True

    def __init__(self, recipients: list[str], subject_prefix: str = "[운영리포트]") -> None:
        self.recipients = recipients
        self.subject_prefix = subject_prefix

    async def deliver(self, key: str, content: str) -> str:
        # 실제 구현:
        # msg = EmailMessage()
        # msg["Subject"] = f"{self.subject_prefix} {key}"
        # msg["To"] = ", ".join(self.recipients)
        # msg.set_content(content)
        # async with aiosmtplib.SMTP(hostname=..., port=...) as smtp:
        #     await smtp.send_message(msg)
        return f"mail://{','.join(self.recipients)}?subject={self.subject_prefix} {key}"
