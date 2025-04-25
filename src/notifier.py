from __future__ import annotations
"""
notifier.py  –  multi‑channel notification hub (Discord / Email / Twilio)

使い方:
    notifier = Notifier()
    await notifier.send(level="INFO", message="entry filled")

チャンネルは .env → settings で ON/OFF。
メール送信は Gmail STARTTLS 前提／Twilio は通話 or SMS。
"""

import asyncio
import smtplib
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Literal

import aiohttp
from twilio.rest import Client

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

Level = Literal["INFO", "WARN", "ERROR"]


class DiscordClient:
    def __init__(self, webhook: str):
        self.webhook = webhook

    async def send(self, msg: str):
        async with aiohttp.ClientSession() as s:
            await s.post(self.webhook, json={"content": msg})


class EmailClient:
    def __init__(self):
        self.server = settings.EMAIL_SMTP_SERVER
        self.port = settings.EMAIL_SMTP_PORT
        self.username = settings.EMAIL_USERNAME
        self.password = settings.EMAIL_PASSWORD
        self.from_addr = settings.EMAIL_FROM
        self.to_addr = settings.EMAIL_TO

    async def send(self, msg: str):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_send, msg)

    def _sync_send(self, msg: str):
        mime = MIMEText(msg)
        mime["Subject"] = "MEXC_WBAR Bot Notification"
        mime["From"] = self.from_addr
        mime["To"] = self.to_addr
        mime["Date"] = formatdate(localtime=True)
        with smtplib.SMTP(self.server, self.port) as smtp:
            smtp.starttls()
            smtp.login(self.username, self.password)
            smtp.send_message(mime)


class TwilioClient:
    def __init__(self):
        self.enabled = settings.TWILIO_ENABLED
        if not self.enabled:
            return
        self.client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        self.from_num = settings.TWILIO_FROM_NUMBER
        self.to_num = settings.TWILIO_TO_NUMBER
        self.max_attempts = settings.TWILIO_MAX_ATTEMPTS

    async def call(self, msg: str):
        if not self.enabled:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_call, msg)

    def _sync_call(self, msg: str):
        self.client.calls.create(
            twiml=f"<Response><Say>{msg}</Say></Response>",
            to=self.to_num,
            from_=self.from_num,
        )


class Notifier:
    def __init__(self):
        self.discord = DiscordClient(settings.DISCORD_WEBHOOK_URL) if settings.DISCORD_WEBHOOK_URL else None
        self.email = EmailClient() if settings.EMAIL_NOTIFICATIONS else None
        self.twilio = TwilioClient() if settings.TWILIO_ENABLED else None

    async def send(self, level: Level, message: str):
        prefix = {"INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}.get(level, "ℹ️")
        msg = f"{prefix} {message}"
        tasks = []
        if self.discord:
            tasks.append(self.discord.send(msg))
        if self.email and level in ("WARN", "ERROR"):
            tasks.append(self.email.send(msg))
        if self.twilio and level == "ERROR":
            tasks.append(self.twilio.call(message))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.debug(f"[Notifier] Sent: {msg}")
