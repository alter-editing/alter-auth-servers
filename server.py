import os
import secrets
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

SESSIONS = {}


def check_subscription(user_id):
    if not BOT_TOKEN or not CHANNEL_ID:
        return False, "missing_env"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"

    try:
        r = requests.get(
            url,
            params={
                "chat_id": CHANNEL_ID,
                "user_id": user_id
            },
            timeout=15
        )

        data = r.json()

        if not data.get("ok"):
            return False, f"telegram_error: {data}"

        status = data["result"]["status"]
        authorized = status in ["member", "administrator", "creator"]
        return authorized, status

    except Exception as e:
        return False, f"exception: {e}"


@app.route("/")
def home():
    return {"ok": True}


@app.route("/auth/create-session", methods=["GET", "POST"])
def create_session():
    token = secrets.token_urlsafe(16)

    SESSIONS[token] = {
        "authorized": False,
        "telegram_user_id": None,
        "status": "created"
    }

    return jsonify({
        "session_token": token,
        "bot_link": f"https://t.me/AlterEditingSend_bot?start={token}"
    })


@app.route("/auth/status/<token>", methods=["GET"])
def auth_status(token):
    session = SESSIONS.get(token)

    if not session:
        return jsonify({
            "ok": False,
            "authorized": False,
            "error": "session_not_found"
        }), 404

    return jsonify({
        "ok": True,
        "authorized": session["authorized"],
        "telegram_user_id": session.get("telegram_user_id"),
        "status": session.get("status")
    })


@app.route("/auth/bot-confirm", methods=["POST"])
def bot_confirm():
    data = request.json or {}

    token = data.get("session_token")
    user_id = data.get("telegram_user_id")
    username = data.get("username")
    first_name = data.get("first_name")

    if not token or token not in SESSIONS:
        return jsonify({
            "ok": False,
            "authorized": False,
            "error": "invalid_session"
        }), 400

    if not user_id:
        return jsonify({
            "ok": False,
            "authorized": False,
            "error": "missing_user_id"
        }), 400

    authorized, status_info = check_subscription(user_id)

    SESSIONS[token]["telegram_user_id"] = user_id
    SESSIONS[token]["username"] = username
    SESSIONS[token]["first_name"] = first_name
    SESSIONS[token]["authorized"] = authorized
    SESSIONS[token]["status"] = status_info

    return jsonify({
        "ok": True,
        "authorized": authorized,
        "status": status_info
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
