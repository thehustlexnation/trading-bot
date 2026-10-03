from paper_trading.notifications import (
    notify,
    notify_email,
    notify_slack,
    notify_telegram,
)


def test_notify_disabled_returns_false():
    assert not notify(
        "Title",
        "Message",
        enabled=False,
    )


def test_notify_telegram_disabled_returns_false():
    assert not notify_telegram(
        "token",
        "chat",
        "message",
        enabled=False,
    )


def test_notify_telegram_missing_credentials_returns_false():
    assert not notify_telegram(
        None,
        "chat",
        "message",
    )


def test_notify_slack_disabled_returns_false():
    assert not notify_slack(
        "https://example.com/webhook",
        "message",
        enabled=False,
    )


def test_notify_slack_missing_webhook_returns_false():
    assert not notify_slack(
        None,
        "message",
    )


def test_notify_email_disabled_returns_false():
    assert not notify_email(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user",
        smtp_password="password",
        sender="from@example.com",
        recipient="to@example.com",
        subject="subject",
        message="message",
        enabled=False,
    )


def test_notify_email_missing_credentials_returns_false():
    assert not notify_email(
        smtp_host=None,
        smtp_port=587,
        smtp_username="user",
        smtp_password="password",
        sender="from@example.com",
        recipient="to@example.com",
        subject="subject",
        message="message",
    )
