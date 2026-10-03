import subprocess
import smtplib
import json
import urllib.parse
import urllib.request
from email.message import EmailMessage


def notify(
    title: str,
    message: str,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False

    script = (
        "display notification "
        f"{message!r} "
        "with title "
        f"{title!r}"
    )

    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return False

    return True


def notify_telegram(
    bot_token: str | None,
    chat_id: str | None,
    message: str,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False
    if not bot_token or not chat_id:
        return False

    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=data,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except OSError:
        return False


def notify_discord(
    webhook_url: str | None,
    message: str,
    components: list[dict] | None = None,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False
    if not webhook_url:
        return False

    payload = {"content": message}
    if components:
        payload["components"] = components
    data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        webhook_url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ai-trading-system",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except OSError:
        return False


def notify_slack(
    webhook_url: str | None,
    message: str,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False
    if not webhook_url:
        return False

    data = json.dumps({"text": message}).encode("utf-8")

    request = urllib.request.Request(
        webhook_url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ai-trading-system",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except OSError:
        return False


def notify_email(
    smtp_host: str | None,
    smtp_port: int | None,
    smtp_username: str | None,
    smtp_password: str | None,
    sender: str | None,
    recipient: str | None,
    subject: str,
    message: str,
    enabled: bool = True,
) -> bool:
    if not enabled:
        return False
    if not all(
        [
            smtp_host,
            smtp_port,
            smtp_username,
            smtp_password,
            sender,
            recipient,
        ]
    ):
        return False

    email = EmailMessage()
    email["From"] = sender
    email["To"] = recipient
    email["Subject"] = subject
    email.set_content(message)

    try:
        with smtplib.SMTP(str(smtp_host), int(smtp_port), timeout=10) as smtp:
            smtp.starttls()
            smtp.login(str(smtp_username), str(smtp_password))
            smtp.send_message(email)
    except OSError:
        return False
    except smtplib.SMTPException:
        return False

    return True
