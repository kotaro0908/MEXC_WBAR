import os
import json
import time
import logging
import smtplib
import requests
import traceback
from twilio.rest import Client
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from utils.logger import get_logger

logger = get_logger(__name__)


class BotMonitor:
    def __init__(self, max_consecutive_losses=3):
        """
        BOT監視クラスの初期化

        Parameters:
            max_consecutive_losses (int): 停止のトリガーとなる連続損失回数
        """
        self.max_consecutive_losses = max_consecutive_losses
        self.consecutive_losses = 0
        self.monitoring_active = True
        self.last_trade_result = None
        self.alert_sent = False
        self.last_trade_time = datetime.now()

        # 無活動監視設定
        self.inactivity_monitor_enabled = os.getenv("INACTIVITY_MONITOR_ENABLED", "FALSE").upper() == "TRUE"
        self.inactivity_timeout_minutes = int(os.getenv("INACTIVITY_TIMEOUT_MINUTES", "60"))
        self.inactivity_check_interval = int(os.getenv("INACTIVITY_CHECK_INTERVAL", "300"))  # 5分毎にチェック
        self.last_inactivity_check = datetime.now()
        self.inactivity_alert_sent = False

        # 通知設定の読み込み
        self.discord_webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
        self.email_enabled = os.getenv("EMAIL_NOTIFICATIONS", "FALSE").upper() == "TRUE"
        self.email_from = os.getenv("EMAIL_FROM", "")
        self.email_to = os.getenv("EMAIL_TO", "")
        self.email_smtp_server = os.getenv("EMAIL_SMTP_SERVER", "smtp.gmail.com")
        self.email_smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
        self.email_username = os.getenv("EMAIL_USERNAME", "")
        self.email_password = os.getenv("EMAIL_PASSWORD", "")

        # Twilio設定の読み込み
        self.twilio_enabled = os.getenv("TWILIO_ENABLED", "FALSE").upper() == "TRUE"
        self.twilio_account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        self.twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        self.twilio_from_number = os.getenv("TWILIO_FROM_NUMBER", "")
        self.twilio_to_number = os.getenv("TWILIO_TO_NUMBER", "")
        self.twilio_max_attempts = int(os.getenv("TWILIO_MAX_ATTEMPTS", "5"))
        self.twilio_call_interval = int(os.getenv("TWILIO_CALL_INTERVAL", "60"))
        self.twilio_client = None

        # 電話通知設定
        self.call_on_error = os.getenv("CALL_ON_ERROR", "FALSE").upper() == "TRUE"
        self.call_on_inactivity = os.getenv("CALL_ON_INACTIVITY", "TRUE").upper() == "TRUE"
        self.call_on_bot_stop = os.getenv("CALL_ON_BOT_STOP", "TRUE").upper() == "TRUE"

        # Twilio初期化
        if self.twilio_enabled and self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number and self.twilio_to_number:
            try:
                self.twilio_client = Client(self.twilio_account_sid, self.twilio_auth_token)
                logger.info(f"Twilio通知: 有効 (From={self.twilio_from_number}, To={self.twilio_to_number})")
            except Exception as e:
                logger.error(f"Twilio初期化エラー: {str(e)}")
                self.twilio_client = None
                self.twilio_enabled = False
        else:
            logger.info("Twilio通知: 無効")

        # 設定のログ出力（パスワードは表示しない）
        logger.info(
            f"Discord WebHook URL: {self.discord_webhook_url[:20]}..." if self.discord_webhook_url else "Discord WebHook未設定")
        logger.info(f"メール通知: {self.email_enabled}")
        if self.email_enabled:
            logger.info(
                f"メール設定: From={self.email_from}, To={self.email_to}, Server={self.email_smtp_server}:{self.email_smtp_port}")

        # ログ監視設定
        self.log_handler = LogMonitorHandler(self)
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_handler)

        # 発生したエラーを保存するリスト
        self.error_history = []

        logger.info("BOT監視モジュールが初期化されました")

        # テスト通知を送信（開発時のみ有効にする）
        test_mode = os.getenv("MONITOR_TEST_MODE", "FALSE").upper() == "TRUE"
        if test_mode:
            self.test_notifications()

    def test_notifications(self):
        """開発用: 通知機能をテストする"""
        logger.info("テスト通知を送信します...")
        test_message = f"これはテスト通知です。時刻: {datetime.now().isoformat()}"

        # Discordテスト
        if self.discord_webhook_url:
            try:
                success = self.send_discord_notification(test_message)
                logger.info(f"Discordテスト通知: {'成功' if success else '失敗'}")
            except Exception as e:
                logger.error(f"Discordテスト通知エラー: {str(e)}")
                logger.error(traceback.format_exc())

        # メールテスト
        if self.email_enabled and self.email_from and self.email_to:
            try:
                success = self.send_email_notification("BOT監視システム テスト", test_message)
                logger.info(f"メールテスト通知: {'成功' if success else '失敗'}")
            except Exception as e:
                logger.error(f"メールテスト通知エラー: {str(e)}")
                logger.error(traceback.format_exc())

        # Twilioテスト
        if self.twilio_enabled and self.twilio_client:
            try:
                success = self.send_test_call("これはテスト通知です")
                logger.info(f"Twilioテスト通知: {'成功' if success else '失敗'}")
            except Exception as e:
                logger.error(f"Twilioテスト通知エラー: {str(e)}")
                logger.error(traceback.format_exc())

    def update_trade_result(self, trade_result):
        """
        トレード結果を更新し、連続損失をチェックする

        Parameters:
            trade_result (dict): トレード結果の辞書
        """
        self.last_trade_result = trade_result
        self.last_trade_time = datetime.now()  # トレード時間を更新

        # インアクティビティアラートが発生していた場合はリセット
        if self.inactivity_alert_sent:
            logger.info("取引が再開されたため、無活動アラートをリセットします")
            self.inactivity_alert_sent = False

        # 損失かどうかを判定
        if trade_result.get("exit_type") == "SL":
            self.consecutive_losses += 1
            logger.warning(f"損失を検出: 連続{self.consecutive_losses}回目")

            # 最大連続損失回数に達したかチェック
            if self.consecutive_losses >= self.max_consecutive_losses:
                # 連続した損失の詳細を記録
                loss_details = f"{self.max_consecutive_losses}回連続で損失が発生しました。"
                loss_details += f" 最新トレード情報: {json.dumps(trade_result, ensure_ascii=False)}"
                self.error_history.append(loss_details)
                self.trigger_alert("連続損失", loss_details)
        else:
            # 勝ちトレードでリセット
            self.consecutive_losses = 0

    def trigger_alert(self, alert_type, message):
        """
        アラートを発生させる

        Parameters:
            alert_type (str): アラートの種類
            message (str): アラートメッセージ
        """
        if self.alert_sent:
            # すでにアラートが送信されている場合は送信しない
            return

        # エラー詳細を保存
        self.error_history.append(message)

        # 発生したエラーの履歴をまとめる
        error_history_text = "\n".join(self.error_history[-5:])  # 最新5件まで

        full_message = f"【BOT停止アラート】 {alert_type}\n"
        full_message += f"詳細: {message}\n"
        full_message += f"実行時刻: {datetime.now().isoformat()}\n"
        full_message += f"\n最近のエラー履歴:\n{error_history_text}"

        # 外部システムに通知する前に、ローカルのログに記録
        print("=" * 80)
        print(full_message)
        print("=" * 80)

        # Discord通知
        discord_sent = False
        if self.discord_webhook_url:
            discord_sent = self.send_discord_notification(full_message)
            print(f"Discord通知: {'送信成功' if discord_sent else '送信失敗'}")

        # メール通知
        email_sent = False
        if self.email_enabled and self.email_from and self.email_to:
            email_sent = self.send_email_notification(f"BOT停止アラート: {alert_type}", full_message)
            print(f"メール通知: {'送信成功' if email_sent else '送信失敗'}")

        # Twilio電話通知 - BOT停止時のみ通知するように変更
        call_sent = False
        if self.twilio_enabled and self.twilio_client and self.call_on_bot_stop:
            call_sent = self.make_persistent_calls(alert_type, message)
            print(f"電話通知: {'送信成功' if call_sent else '送信失敗'}")

        # アラート送信済みフラグをセット
        self.alert_sent = True

        # BOT停止フラグをセット
        self.monitoring_active = False

        # 単純なログだけを残す（再帰を避けるため詳細は含めない）
        logger.critical(f"BOTを停止しました: {alert_type} - Discord:{discord_sent} Email:{email_sent} Call:{call_sent}")

    def send_discord_notification(self, message):
        """
        Discord Webhookを使用して通知を送信

        Parameters:
            message (str): 送信するメッセージ

        Returns:
            bool: 送信成功したらTrue
        """
        try:
            payload = {
                "content": message,
                "username": "BOT監視システム"
            }

            response = requests.post(self.discord_webhook_url, json=payload)
            if response.status_code == 204:
                print(f"Discord通知が送信されました (Status: {response.status_code})")
                return True
            else:
                print(f"Discord通知の送信に失敗: Status={response.status_code}, Response={response.text}")
                return False
        except Exception as e:
            print(f"Discord通知の送信中にエラーが発生: {str(e)}")
            print(traceback.format_exc())
            return False

    def send_email_notification(self, subject, message):
        """
        メール通知を送信

        Parameters:
            subject (str): メールの件名
            message (str): メールの本文

        Returns:
            bool: 送信成功したらTrue
        """
        try:
            msg = MIMEMultipart()
            msg['From'] = self.email_from
            msg['To'] = self.email_to
            msg['Subject'] = subject

            msg.attach(MIMEText(message, 'plain'))

            print(f"SMTP接続: {self.email_smtp_server}:{self.email_smtp_port}")
            server = smtplib.SMTP(self.email_smtp_server, self.email_smtp_port)
            server.set_debuglevel(1)  # デバッグログを有効化
            server.ehlo()
            server.starttls()
            server.ehlo()
            print(f"ログイン: {self.email_username}")
            server.login(self.email_username, self.email_password)
            print("メール送信中...")
            server.send_message(msg)
            server.quit()

            print("メール通知が送信されました")
            return True
        except Exception as e:
            print(f"メール通知の送信中にエラーが発生: {str(e)}")
            print(traceback.format_exc())
            return False

    def make_persistent_calls(self, alert_type, error_message):
        """
        応答があるまで繰り返し電話をかける関数

        Parameters:
            alert_type (str): アラートの種類
            error_message (str): エラーメッセージ

        Returns:
            bool: いずれかの通話が応答されたらTrue
        """
        if not self.twilio_client:
            logger.error("Twilioクライアントが初期化されていません")
            return False

        attempts = 0
        call_answered = False
        max_attempts = self.twilio_max_attempts
        interval = self.twilio_call_interval

        logger.info(f"電話通知を開始します: 最大{max_attempts}回, 間隔{interval}秒")

        while not call_answered and attempts < max_attempts:
            attempts += 1
            logger.info(f"電話通知試行 {attempts}/{max_attempts}")

            # 日本語のエラーメッセージを設定したTwiML
            twiml = f"""
            <Response>
                <Say language="ja-JP">仮想通貨BOTにエラーが発生しました。{alert_type}</Say>
                <Pause length="1"/>
                <Say language="ja-JP">{error_message}</Say>
                <Pause length="1"/>
                <Say language="ja-JP">至急確認してください。</Say>
            </Response>
            """

            try:
                # 電話をかける
                call = self.twilio_client.calls.create(
                    to=self.twilio_to_number,  # 通知先の電話番号
                    from_=self.twilio_from_number,  # Twilioで購入した電話番号
                    twiml=twiml
                )

                logger.info(f"電話をかけました: {call.sid}")

                # 通話ステータスを取得して応答を確認
                time.sleep(30)  # 少し待ってからステータスを確認

                call_status = self.twilio_client.calls(call.sid).fetch()

                if call_status.status == "completed" and hasattr(call_status,
                                                                 'answered_by') and call_status.answered_by:
                    logger.info("電話が応答されました")
                    call_answered = True
                elif call_status.status == "in-progress":
                    logger.info("通話中です")
                    call_answered = True  # 通話中も応答として扱う
                else:
                    logger.info(f"現在の通話状態: {call_status.status}")
                    if call_status.status in ["failed", "busy", "no-answer", "canceled"]:
                        logger.info(f"{interval}秒後に再試行します")
                        time.sleep(interval)
                    else:
                        # その他の状態（queued, ringing等）の場合はもう少し待つ
                        logger.info("もう少し待ってステータスを再確認します")
                        time.sleep(15)
                        # ステータス再確認
                        call_status = self.twilio_client.calls(call.sid).fetch()
                        if call_status.status != "completed" and call_status.status != "in-progress":
                            logger.info(f"{interval}秒後に再試行します")
                            time.sleep(interval)

            except Exception as e:
                logger.error(f"電話通知中にエラーが発生しました: {e}")
                logger.error(traceback.format_exc())
                time.sleep(interval)

        if call_answered:
            logger.info("電話通知に成功しました")
        else:
            logger.warning(f"最大試行回数({max_attempts}回)に達しました")

        return call_answered

    def send_test_call(self, test_message):
        """テスト用の電話通知を送信する"""
        if not self.twilio_client:
            logger.error("Twilioクライアントが初期化されていません")
            return False

        # テスト用TwiML
        twiml = f"""
        <Response>
            <Say language="ja-JP">これはテスト通知です。</Say>
            <Pause length="1"/>
            <Say language="ja-JP">{test_message}</Say>
        </Response>
        """

        try:
            # テスト電話をかける
            call = self.twilio_client.calls.create(
                to=self.twilio_to_number,
                from_=self.twilio_from_number,
                twiml=twiml
            )
            logger.info(f"テスト電話をかけました: {call.sid}")
            return True
        except Exception as e:
            logger.error(f"テスト電話中にエラーが発生しました: {e}")
            logger.error(traceback.format_exc())
            return False

    def check_inactivity(self):
        """
        無活動状態をチェックし、必要に応じて通知する
        """
        if not self.inactivity_monitor_enabled:
            return

        current_time = datetime.now()
        # 前回のチェックから一定時間経過しているか確認
        if (current_time - self.last_inactivity_check).total_seconds() < self.inactivity_check_interval:
            return

        self.last_inactivity_check = current_time

        # 最後のトレードからの経過時間を計算
        elapsed_minutes = (current_time - self.last_trade_time).total_seconds() / 60

        # タイムアウト時間を超えていて、まだアラートが送信されていない場合
        if elapsed_minutes > self.inactivity_timeout_minutes and not self.inactivity_alert_sent:
            inactivity_message = f"BOTが{int(elapsed_minutes)}分間取引を行っていません。最後の取引時間: {self.last_trade_time.isoformat()}"
            logger.warning(inactivity_message)

            # Discord通知
            if self.discord_webhook_url:
                self.send_discord_notification(f"【BOT無活動アラート】\n{inactivity_message}")

            # メール通知
            if self.email_enabled and self.email_from and self.email_to:
                self.send_email_notification("BOT無活動アラート", inactivity_message)

            # 電話通知
            if self.twilio_enabled and self.twilio_client and self.call_on_inactivity:
                self.make_persistent_calls("無活動アラート", inactivity_message)

            self.inactivity_alert_sent = True
            logger.info("無活動アラートを送信しました")

        # 無活動時間が短くなった場合はアラートをリセット
        elif elapsed_minutes <= self.inactivity_timeout_minutes and self.inactivity_alert_sent:
            self.inactivity_alert_sent = False
            logger.info("無活動状態が解消されたため、アラートをリセットしました")

    def is_bot_running(self):
        """
        BOTの実行状態を確認

        Returns:
            bool: BOTが実行中であればTrue、停止していればFalse
        """
        # 無活動チェックを実行
        self.check_inactivity()
        return self.monitoring_active


class LogMonitorHandler(logging.Handler):
    """
    WARNING/ERRORログをモニタリングするためのログハンドラ
    """

    def __init__(self, monitor):
        super().__init__()
        self.monitor = monitor
        self.setLevel(logging.ERROR)  # ERRORレベル以上のみ捕捉
        self.last_error_time = 0
        self.error_cooldown = 5  # エラー間の最小秒数（再帰防止）

    def emit(self, record):
        # 監視モジュール自体からのログは無視（再帰防止）
        if record.name == 'src.monitor' or 'monitor.py' in getattr(record, 'pathname', ''):
            return

        # すでにアラートが発生している場合は処理しない
        if self.monitor.alert_sent:
            return

        # クールダウンチェック（同一エラーの連続処理防止）
        current_time = time.time()
        if current_time - self.last_error_time < self.error_cooldown:
            return

        self.last_error_time = current_time

        # 詳細情報を収集
        error_info = f"{record.levelname}: {record.getMessage()}"
        if hasattr(record, 'filename') and hasattr(record, 'lineno'):
            error_info += f" (in {record.filename}, line {record.lineno})"
        elif hasattr(record, 'pathname') and hasattr(record, 'lineno'):
            filename = os.path.basename(record.pathname)
            error_info += f" (in {filename}, line {record.lineno})"

        # エラー履歴に追加
        self.monitor.error_history.append(error_info)

        # ERRORまたはCRITICALレベルのログが発生した場合
        if record.levelno >= logging.ERROR:
            self.monitor.trigger_alert(
                "重大なエラー",
                error_info
            )