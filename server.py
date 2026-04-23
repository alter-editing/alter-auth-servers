import os
import json
import secrets
import requests
from datetime import datetime
from flask import Flask, request, jsonify, Response, redirect, url_for

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


def save_all():
    save_json(SESSIONS_FILE, SESSIONS)
    save_json(USERS_FILE, USERS)
    save_json(BANNED_FILE, BANNED)
    save_json(LOGS_FILE, LOGS)


def now_str():
    return datetime.utcnow().isoformat() + "Z"


def short_log(action, **kwargs):
    global LOGS

    item = {
        "t": now_str(),
        "a": action,
    }

    # короткие ключи
    if "uid" in kwargs:
        item["uid"] = kwargs["uid"]
    if "u" in kwargs:
        item["u"] = kwargs["u"]
    if "s" in kwargs:
        item["s"] = kwargs["s"]
    if "ok" in kwargs:
        item["ok"] = kwargs["ok"]
    if "tok" in kwargs:
        item["tok"] = kwargs["tok"]

    LOGS.append(item)

    if len(LOGS) > 1000:
        LOGS = LOGS[-1000:]

    save_json(LOGS_FILE, LOGS)


def is_admin(req):
    key = req.headers.get("X-Admin-Key") or req.args.get("admin_key")
    return bool(ADMIN_KEY and key == ADMIN_KEY)


def h(text):
    if text is None:
        return ""
    s = str(text)
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


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
            return False, f"telegram_error"

        status = data["result"]["status"]
        authorized = status in ["member", "administrator", "creator"]
        return authorized, status

    except Exception:
        return False, "exception"


def invalidate_user_sessions(user_id, status_value="banned"):
    user_id_str = str(user_id)
    for token in list(SESSIONS.keys()):
        if str(SESSIONS[token].get("telegram_user_id")) == user_id_str:
            SESSIONS[token]["authorized"] = False
            SESSIONS[token]["status"] = status_value


def get_stats():
    total_users = len(USERS)
    total_banned = len(BANNED)

    active_ok = 0
    for user in USERS.values():
        st = user.get("last_status")
        if st in ["member", "administrator", "creator"]:
            active_ok += 1

    return {
        "total_users": total_users,
        "active_ok": active_ok,
        "banned_count": total_banned,
        "total_logs": len(LOGS),
    }


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

    short_log("create", tok=token[:8])

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
        short_log("bad_session", uid=user_id, tok=(token or "")[:8])
        return jsonify({
            "ok": False,
            "authorized": False,
            "error": "invalid_session"
        }), 400

    if not user_id:
        short_log("no_uid", tok=token[:8])
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
        short_log("banned_try", uid=user_id, u=username, s="banned", ok=False)

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
    short_log("confirm", uid=user_id, u=username, s=status_info, ok=authorized)

    return jsonify({
        "ok": True,
        "authorized": authorized,
        "status": status_info
    })


# -----------------------------
# ADMIN JSON API
# -----------------------------

@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "stats": get_stats()
    })


@app.route("/admin/users", methods=["GET"])
def admin_users():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    users = sorted(
        USERS.values(),
        key=lambda x: x.get("last_seen", ""),
        reverse=True
    )

    return jsonify({
        "ok": True,
        "users": users,
        "stats": get_stats()
    })


@app.route("/admin/logs", methods=["GET"])
def admin_logs():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "logs": LOGS[-200:],
        "stats": get_stats()
    })


@app.route("/admin/banned", methods=["GET"])
def admin_banned():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    return jsonify({
        "ok": True,
        "banned": BANNED,
        "stats": get_stats()
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

    invalidate_user_sessions(user_id, "banned")
    save_all()
    short_log("ban", uid=user_id)

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
    short_log("unban", uid=user_id)

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

    if user_id_str in USERS:
        del USERS[user_id_str]

    invalidate_user_sessions(user_id, "reset_by_admin")
    save_all()
    short_log("reset", uid=user_id)

    return jsonify({"ok": True})


# -----------------------------
# ADMIN HTML
# -----------------------------

def panel_layout(title, body_html):
    return f"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
<style>
    body {{
        margin: 0;
        font-family: Arial, sans-serif;
        background: #0b0f17;
        color: #f2f4f8;
    }}
    .wrap {{
        max-width: 1250px;
        margin: 0 auto;
        padding: 22px;
    }}
    h1 {{
        margin: 0 0 16px 0;
        font-size: 30px;
    }}
    .topbar {{
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 18px;
    }}
    .btn {{
        display: inline-block;
        padding: 10px 14px;
        border-radius: 10px;
        background: #131a27;
        color: white;
        text-decoration: none;
        border: 1px solid #253047;
    }}
    .btn:hover {{
        background: #182133;
    }}
    .grid {{
        display: grid;
        grid-template-columns: repeat(4, minmax(180px, 1fr));
        gap: 12px;
        margin-bottom: 18px;
    }}
    .stat {{
        background: #121826;
        border: 1px solid #243048;
        border-radius: 14px;
        padding: 14px;
    }}
    .stat .label {{
        color: #9cadc8;
        font-size: 12px;
        margin-bottom: 8px;
    }}
    .stat .value {{
        font-size: 24px;
        font-weight: 700;
    }}
    .card {{
        background: #121826;
        border: 1px solid #243048;
        border-radius: 16px;
        padding: 16px;
        margin-bottom: 18px;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
    }}
    th, td {{
        text-align: left;
        padding: 12px;
        border-bottom: 1px solid #233046;
        vertical-align: top;
        font-size: 14px;
    }}
    th {{
        color: #c3d0e7;
        background: #151d2c;
    }}
    tr:hover td {{
        background: #151c29;
    }}
    .muted {{
        color: #97a7c0;
    }}
    .status {{
        display: inline-block;
        padding: 5px 10px;
        border-radius: 999px;
        font-size: 12px;
        border: 1px solid #2b3750;
        background: #0e1420;
    }}
    .member, .creator, .administrator {{
        color: #9cf0b8;
        border-color: #27553a;
    }}
    .left, .banned, .reset_by_admin {{
        color: #ffb3b3;
        border-color: #5b2929;
    }}
    .created {{
        color: #ffd28a;
        border-color: #5d4921;
    }}
    .mono {{
        font-family: Consolas, monospace;
        word-break: break-word;
    }}
    .actions {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
    }}
    form {{
        margin: 0;
    }}
    button {{
        cursor: pointer;
        border: 1px solid #2e3a54;
        background: #182133;
        color: white;
        padding: 8px 12px;
        border-radius: 10px;
    }}
    button:hover {{
        background: #202a3f;
    }}
    .danger {{
        border-color: #703131;
        background: #2a1717;
    }}
    .danger:hover {{
        background: #351d1d;
    }}
    .good {{
        border-color: #2d5e40;
        background: #15241a;
    }}
    .good:hover {{
        background: #1d3023;
    }}
    .small {{
        font-size: 12px;
    }}
</style>
</head>
<body>
<div class="wrap">
{body_html}
</div>
</body>
</html>
"""


def render_stats(stats):
    return f"""
    <div class="grid">
        <div class="stat">
            <div class="label">Всего пользователей</div>
            <div class="value">{stats["total_users"]}</div>
        </div>
        <div class="stat">
            <div class="label">Активных / подтверждённых</div>
            <div class="value">{stats["active_ok"]}</div>
        </div>
        <div class="stat">
            <div class="label">Забанено</div>
            <div class="value">{stats["banned_count"]}</div>
        </div>
        <div class="stat">
            <div class="label">Всего логов</div>
            <div class="value">{stats["total_logs"]}</div>
        </div>
    </div>
    """


@app.route("/admin/panel", methods=["GET"])
def admin_panel():
    if not is_admin(request):
        return Response("Unauthorized", status=401)

    admin_key = request.args.get("admin_key", "")
    stats = get_stats()
    users = sorted(
        USERS.values(),
        key=lambda x: x.get("last_seen", ""),
        reverse=True
    )

    rows = []
    for user in users:
        user_id = user.get("telegram_user_id")
        username = user.get("username") or ""
        first_name = user.get("first_name") or ""
        last_status = user.get("last_status") or "unknown"
        last_seen = user.get("last_seen") or ""
        banned = str(user_id) in BANNED

        rows.append(f"""
        <tr>
            <td class="mono">{h(user_id)}</td>
            <td>{('@' + h(username)) if username else '<span class="muted">-</span>'}</td>
            <td>{h(first_name) if first_name else '<span class="muted">-</span>'}</td>
            <td><span class="status {h(last_status)}">{h(last_status)}</span></td>
            <td class="small">{h(last_seen)}</td>
            <td>{'<span class="status banned">banned</span>' if banned else '<span class="status">active</span>'}</td>
            <td>
                <div class="actions">
                    <form method="post" action="/admin/panel/ban?admin_key={h(admin_key)}">
                        <input type="hidden" name="telegram_user_id" value="{h(user_id)}">
                        <button class="danger" type="submit">Ban</button>
                    </form>
                    <form method="post" action="/admin/panel/unban?admin_key={h(admin_key)}">
                        <input type="hidden" name="telegram_user_id" value="{h(user_id)}">
                        <button class="good" type="submit">Unban</button>
                    </form>
                    <form method="post" action="/admin/panel/reset-user?admin_key={h(admin_key)}">
                        <input type="hidden" name="telegram_user_id" value="{h(user_id)}">
                        <button type="submit">Reset</button>
                    </form>
                </div>
            </td>
        </tr>
        """)

    body = f"""
    <h1>Admin Panel</h1>
    <div class="topbar">
        <a class="btn" href="/admin/panel?admin_key={h(admin_key)}">Users</a>
        <a class="btn" href="/admin/logs-page?admin_key={h(admin_key)}">Logs</a>
        <a class="btn" href="/admin/banned-page?admin_key={h(admin_key)}">Banned</a>
    </div>

    {render_stats(stats)}

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Telegram ID</th>
                    <th>Username</th>
                    <th>First name</th>
                    <th>Status</th>
                    <th>Last seen</th>
                    <th>Access</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows) if rows else '<tr><td colspan="7" class="muted">No users yet</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return panel_layout("Admin Panel", body)


@app.route("/admin/logs-page", methods=["GET"])
def admin_logs_page():
    if not is_admin(request):
        return Response("Unauthorized", status=401)

    admin_key = request.args.get("admin_key", "")
    stats = get_stats()
    logs = LOGS[-200:][::-1]

    rows = []
    for item in logs:
        rows.append(f"""
        <tr>
            <td class="small">{h(item.get("t"))}</td>
            <td>{h(item.get("a"))}</td>
            <td class="mono">{h(item.get("uid", ""))}</td>
            <td>{h(item.get("u", ""))}</td>
            <td>{h(item.get("s", ""))}</td>
            <td>{h(item.get("ok", ""))}</td>
            <td class="mono small">{h(item.get("tok", ""))}</td>
        </tr>
        """)

    body = f"""
    <h1>Logs</h1>
    <div class="topbar">
        <a class="btn" href="/admin/panel?admin_key={h(admin_key)}">Users</a>
        <a class="btn" href="/admin/logs-page?admin_key={h(admin_key)}">Logs</a>
        <a class="btn" href="/admin/banned-page?admin_key={h(admin_key)}">Banned</a>
    </div>

    {render_stats(stats)}

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Action</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Status</th>
                    <th>OK</th>
                    <th>Token</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows) if rows else '<tr><td colspan="7" class="muted">No logs yet</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return panel_layout("Logs", body)


@app.route("/admin/banned-page", methods=["GET"])
def admin_banned_page():
    if not is_admin(request):
        return Response("Unauthorized", status=401)

    admin_key = request.args.get("admin_key", "")
    stats = get_stats()
    rows = []

    for user_id, data in BANNED.items():
        rows.append(f"""
        <tr>
            <td class="mono">{h(user_id)}</td>
            <td class="small">{h(data.get("banned_at"))}</td>
            <td>
                <form method="post" action="/admin/panel/unban?admin_key={h(admin_key)}">
                    <input type="hidden" name="telegram_user_id" value="{h(user_id)}">
                    <button class="good" type="submit">Unban</button>
                </form>
            </td>
        </tr>
        """)

    body = f"""
    <h1>Banned Users</h1>
    <div class="topbar">
        <a class="btn" href="/admin/panel?admin_key={h(admin_key)}">Users</a>
        <a class="btn" href="/admin/logs-page?admin_key={h(admin_key)}">Logs</a>
        <a class="btn" href="/admin/banned-page?admin_key={h(admin_key)}">Banned</a>
    </div>

    {render_stats(stats)}

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Telegram ID</th>
                    <th>Banned at</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows) if rows else '<tr><td colspan="3" class="muted">No banned users</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return panel_layout("Banned Users", body)


@app.route("/admin/panel/ban", methods=["POST"])
def admin_panel_ban():
    if not is_admin(request):
        return Response("Unauthorized", status=401)

    admin_key = request.args.get("admin_key", "")
    user_id = request.form.get("telegram_user_id")

    if user_id:
        user_id_str = str(user_id)
        BANNED[user_id_str] = {
            "telegram_user_id": user_id,
            "banned_at": now_str()
        }
        invalidate_user_sessions(user_id, "banned")
        save_all()
        short_log("ban", uid=user_id)

    return redirect(url_for("admin_panel", admin_key=admin_key))


@app.route("/admin/panel/unban", methods=["POST"])
def admin_panel_unban():
    if not is_admin(request):
        return Response("Unauthorized", status=401)

    admin_key = request.args.get("admin_key", "")
    user_id = request.form.get("telegram_user_id")

    if user_id:
        user_id_str = str(user_id)
        if user_id_str in BANNED:
            del BANNED[user_id_str]
            save_json(BANNED_FILE, BANNED)
            short_log("unban", uid=user_id)

    return redirect(url_for("admin_panel", admin_key=admin_key))


@app.route("/admin/panel/reset-user", methods=["POST"])
def admin_panel_reset_user():
    if not is_admin(request):
        return Response("Unauthorized", status=401)

    admin_key = request.args.get("admin_key", "")
    user_id = request.form.get("telegram_user_id")

    if user_id:
        user_id_str = str(user_id)

        if user_id_str in USERS:
            del USERS[user_id_str]

        invalidate_user_sessions(user_id, "reset_by_admin")
        save_all()
        short_log("reset", uid=user_id)

    return redirect(url_for("admin_panel", admin_key=admin_key))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
