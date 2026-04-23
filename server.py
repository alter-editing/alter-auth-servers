import os
import json
import secrets
import requests
from datetime import datetime
from flask import Flask, request, jsonify

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_KEY = os.environ.get("ADMIN_KEY")

DATA_DIR = "data"
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
BANNED_FILE = os.path.join(DATA_DIR, "banned.json")
LOGS_FILE = os.path.join(DATA_DIR, "logs.json")


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default):
    ensure_data_dir()
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


SESSIONS = load_json(SESSIONS_FILE, {})
USERS = load_json(USERS_FILE, {})
BANNED = load_json(BANNED_FILE, {})
LOGS = load_json(LOGS_FILE, [])


def now_str():
    return datetime.utcnow().isoformat() + "Z"


def add_log(action, extra=None):
    global LOGS
    item = {
        "time": now_str(),
        "action": action,
        "extra": extra or {}
    }
    LOGS.append(item)

    # ограничение размера
    if len(LOGS) > 1000:
        LOGS = LOGS[-1000:]

    save_json(LOGS_FILE, LOGS)


def save_all():
    save_json(SESSIONS_FILE, SESSIONS)
    save_json(USERS_FILE, USERS)
    save_json(BANNED_FILE, BANNED)
    save_json(LOGS_FILE, LOGS)


def is_admin(req):
    key = req.headers.get("X-Admin-Key") or req.args.get("admin_key")
    return ADMIN_KEY and key == ADMIN_KEY


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
        "status": "created",
        "created_at": now_str()
    }
    save_json(SESSIONS_FILE, SESSIONS)

    add_log("create_session", {
        "session_token": token
    })

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
        add_log("bot_confirm_invalid_session", {
            "session_token": token,
            "telegram_user_id": user_id
        })
        return jsonify({
            "ok": False,
            "authorized": False,
            "error": "invalid_session"
        }), 400

    if not user_id:
        add_log("bot_confirm_missing_user_id", {
            "session_token": token
        })
        return jsonify({
            "ok": False,
            "authorized": False,
            "error": "missing_user_id"
        }), 400

    user_id_str = str(user_id)

    if BANNED.get(user_id_str):
        SESSIONS[token]["telegram_user_id"] = user_id
        SESSIONS[token]["username"] = username
        SESSIONS[token]["first_name"] = first_name
        SESSIONS[token]["authorized"] = False
        SESSIONS[token]["status"] = "banned"

        USERS[user_id_str] = {
            "telegram_user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_status": "banned",
            "last_seen": now_str()
        }

        save_all()
        add_log("bot_confirm_banned", {
            "telegram_user_id": user_id,
            "username": username
        })

        return jsonify({
            "ok": True,
            "authorized": False,
            "status": "banned"
        })

    authorized, status_info = check_subscription(user_id)

    SESSIONS[token]["telegram_user_id"] = user_id
    SESSIONS[token]["username"] = username
    SESSIONS[token]["first_name"] = first_name
    SESSIONS[token]["authorized"] = authorized
    SESSIONS[token]["status"] = status_info

    USERS[user_id_str] = {
        "telegram_user_id": user_id,
        "username": username,
        "first_name": first_name,
        "last_status": status_info,
        "last_seen": now_str()
    }

    save_all()

    add_log("bot_confirm", {
        "telegram_user_id": user_id,
        "username": username,
        "authorized": authorized,
        "status": status_info
    })

    return jsonify({
        "ok": True,
        "authorized": authorized,
        "status": status_info
    })


# -----------------------------
# ADMIN API
# -----------------------------

@app.route("/admin/users", methods=["GET"])
def admin_users():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "users": list(USERS.values())
    })


@app.route("/admin/logs", methods=["GET"])
def admin_logs():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "logs": LOGS[-200:]
    })


@app.route("/admin/banned", methods=["GET"])
def admin_banned():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "banned": BANNED
    })


@app.route("/admin/ban", methods=["POST"])
def admin_ban():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.json or {}
    user_id = data.get("telegram_user_id")

    if not user_id:
        return jsonify({"ok": False, "error": "missing_telegram_user_id"}), 400

    user_id_str = str(user_id)
    BANNED[user_id_str] = {
        "telegram_user_id": user_id,
        "banned_at": now_str()
    }

    save_json(BANNED_FILE, BANNED)
    add_log("admin_ban", {"telegram_user_id": user_id})

    return jsonify({"ok": True})


@app.route("/admin/unban", methods=["POST"])
def admin_unban():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.json or {}
    user_id = data.get("telegram_user_id")

    if not user_id:
        return jsonify({"ok": False, "error": "missing_telegram_user_id"}), 400

    user_id_str = str(user_id)
    if user_id_str in BANNED:
        del BANNED[user_id_str]

    save_json(BANNED_FILE, BANNED)
    add_log("admin_unban", {"telegram_user_id": user_id})

    return jsonify({"ok": True})


@app.route("/admin/reset-user", methods=["POST"])
def admin_reset_user():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    data = request.json or {}
    user_id = data.get("telegram_user_id")

    if not user_id:
        return jsonify({"ok": False, "error": "missing_telegram_user_id"}), 400

    user_id_str = str(user_id)

    # удалить из users
    if user_id_str in USERS:
        del USERS[user_id_str]

    # очистить все сессии этого user_id
    for token in list(SESSIONS.keys()):
        if str(SESSIONS[token].get("telegram_user_id")) == user_id_str:
            SESSIONS[token]["authorized"] = False
            SESSIONS[token]["status"] = "reset_by_admin"

    save_all()
    add_log("admin_reset_user", {"telegram_user_id": user_id})

    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
