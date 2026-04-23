import os
from flask import Flask, request, jsonify
import requests
import secrets

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

SESSIONS = {}


def check_subscription(user_id):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"

    r = requests.get(url, params={
        "chat_id": CHANNEL_ID,
        "user_id": user_id
    })

    data = r.json()

    if not data.get("ok"):
        return False

    status = data["result"]["status"]

    return status in ["member", "administrator", "creator"]


@app.route("/")
def home():
    return {"ok": True}


@app.route("/auth/create-session", methods=["POST"])
def create_session():
    token = secrets.token_urlsafe(16)

    SESSIONS[token] = {
        "authorized": False
    }

    return jsonify({
        "session_token": token,
        "bot_link": f"https://t.me/YOUR_BOT_USERNAME?start={token}"
    })


@app.route("/auth/status/<token>")
def status(token):
    session = SESSIONS.get(token)

    if not session:
        return {"authorized": False}

    return {"authorized": session["authorized"]}


@app.route("/auth/bot-confirm", methods=["POST"])
def confirm():
    data = request.json

    token = data.get("session_token")
    user_id = data.get("telegram_user_id")

    if token not in SESSIONS:
        return {"ok": False}

    if check_subscription(user_id):
        SESSIONS[token]["authorized"] = True

    return {"ok": True}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
