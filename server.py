import os
import secrets
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

SESSIONS = {}


# 🔹 Проверка подписки
def check_subscription(user_id):
    if not BOT_TOKEN or not CHANNEL_ID:
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"

    try:
        r = requests.get(url, params={
            "chat_id": CHANNEL_ID,
            "user_id": user_id
        }, timeout=10)

        data = r.json()

        if not data.get("ok"):
            return False

        status = data["result"]["status"]

        return status in ["member", "administrator", "creator"]

    except:
        return False


# 🔹 Проверка работы сервера
@app.route("/")
def home():
    return {"ok": True}


# 🔹 Создание сессии (GET + POST)
@app.route("/auth/create-session", methods=["GET", "POST"])
def create_session():
    token = secrets.token_urlsafe(16)

    SESSIONS[token] = {
        "authorized": False
    }

    return jsonify({
        "session_token": token,
        "bot_link": f"https://t.me/AlterEditingSend_bot?start={token}"
    })


# 🔹 Проверка статуса
@app.route("/auth/status/<token>")
def status(token):
    session = SESSIONS.get(token)

    if not session:
        return {"authorized": False}

    return {"authorized": session["authorized"]}


# 🔹 Подтверждение от бота
@app.route("/auth/bot-confirm", methods=["POST"])
def confirm():
    data = request.json or {}

    token = data.get("session_token")
    user_id = data.get("telegram_user_id")

    if not token or token not in SESSIONS:
        return {"ok": False}

    if not user_id:
        return {"ok": False}

    if check_subscription(user_id):
        SESSIONS[token]["authorized"] = True

    return {"ok": True}


# 🔹 Запуск (ВАЖНО ДЛЯ RENDER)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
