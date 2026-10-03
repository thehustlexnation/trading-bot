import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

from paper_trading.notifications import notify_email, notify_slack


def parse_args():
    parser = argparse.ArgumentParser(
        description="Send a test alert through Slack and/or email."
    )
    parser.add_argument("--slack", action="store_true")
    parser.add_argument("--email", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    load_dotenv()

    message = "Trading bot test alert: notifications are connected."

    if args.slack:
        sent = notify_slack(
            webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            message=message,
        )
        print(f"Slack sent: {sent}")

    if args.email:
        sent = notify_email(
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
            sender=os.getenv("SMTP_SENDER"),
            recipient=os.getenv("ALERT_EMAIL_TO"),
            subject="Trading Bot Test Alert",
            message=message,
        )
        print(f"Email sent: {sent}")

    if not args.slack and not args.email:
        print("Choose at least one channel: --slack and/or --email")


if __name__ == "__main__":
    main()
