import sys, asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from src.notifier import Notifier

async def main():
    n = Notifier()

    # ---------- Discord ----------
    if n.discord:
        await n.discord.send("🚀 BOT 通知テスト – Discord OK?")

    # ---------- Email ----------
    if n.email:
        await n.email.send("【BOT テスト】メール通知 OK?")   # ← 引数は本文だけ

    # ---------- Twilio ----------
    if n.twilio:
        await n.twilio.call("BOT テスト – 電話／SMS 通知です。")

    print("テスト送信完了")

asyncio.run(main())
