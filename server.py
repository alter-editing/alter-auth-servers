import os
import sqlite3
import secrets
from datetime import datetime

import requests
from flask import Flask, request, jsonify, Response, redirect, url_for
from flask_socketio import SocketIO

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "change-me")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
ADMIN_KEY = os.environ.get("ADMIN_KEY")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "AlterEditingSend_bot")

DB_DIR = "data"
DB_PATH = os.path.join(DB_DIR, "auth.db")


def now_str():
    return datetime.utcnow().isoformat() + "Z"


def ensure_db_dir():
    os.makedirs(DB_DIR, exist_ok=True)


def db():
    ensure_db_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            telegram_user_id TEXT,
            username TEXT,
            first_name TEXT,
            authorized INTEGER NOT NULL DEFAULT 0,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_status TEXT,
            last_seen TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS banned (
            telegram_user_id TEXT PRIMARY KEY,
            banned_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            t TEXT,
            a TEXT,
            uid TEXT,
            u TEXT,
            s TEXT,
            ok TEXT,
            tok TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def short_log(action, uid=None, u=None, s=None, ok=None, tok=None):
    conn = db()
    conn.execute(
        "INSERT INTO logs (t, a, uid, u, s, ok, tok) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_str(), action, str(uid) if uid is not None else None, u, s, str(ok) if ok is not None else None, tok),
    )
    conn.commit()
    conn.execute(
        "DELETE FROM logs WHERE id NOT IN (SELECT id FROM logs ORDER BY id DESC LIMIT 1000)"
    )
    conn.commit()
    conn.close()


def is_admin(req):
    key = req.headers.get("X-Admin-Key") or req.args.get("admin_key")
    return bool(ADMIN_KEY and key == ADMIN_KEY)


def h(text):
    if text is None:
        return ""
    s = str(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', '&quot;')


def get_session(token):
    conn = db()
    row = conn.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_stats():
    conn = db()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    active_ok = conn.execute(
        "SELECT COUNT(*) FROM users WHERE last_status IN ('member', 'administrator', 'creator')"
    ).fetchone()[0]
    banned_count = conn.execute("SELECT COUNT(*) FROM banned").fetchone()[0]
    total_logs = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "active_ok": active_ok,
        "banned_count": banned_count,
        "total_logs": total_logs,
    }


def invalidate_user_sessions(user_id, status_value="banned"):
    conn = db()
    rows = conn.execute("SELECT token FROM sessions WHERE telegram_user_id=?", (str(user_id),)).fetchall()
    conn.execute(
        "UPDATE sessions SET authorized=0, status=?, updated_at=? WHERE telegram_user_id=?",
        (status_value, now_str(), str(user_id)),
    )
    conn.commit()
    conn.close()
    for row in rows:
        socketio.emit("session_revoked", {"token": row[0], "status": status_value})


def check_subscription(user_id):
    if not BOT_TOKEN or not CHANNEL_ID:
        return False, "missing_env"
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember"
    try:
        r = requests.get(url, params={"chat_id": CHANNEL_ID, "user_id": user_id}, timeout=15)
        data = r.json()
        if not data.get("ok"):
            return False, "telegram_error"
        status = data["result"]["status"]
        return status in ["member", "administrator", "creator"], status
    except Exception:
        return False, "exception"


def panel_layout(title, body_html):
    return f"""
<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{h(title)}</title>
<style>
body {{ margin:0; font-family:Arial,sans-serif; background:#0b0f17; color:#f2f4f8; }}
.wrap {{ max-width:1250px; margin:0 auto; padding:22px; }}
h1 {{ margin:0 0 16px 0; font-size:30px; }}
.topbar {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:18px; }}
.btn {{ display:inline-block; padding:10px 14px; border-radius:10px; background:#131a27; color:white; text-decoration:none; border:1px solid #253047; }}
.btn:hover {{ background:#182133; }}
.grid {{ display:grid; grid-template-columns:repeat(4,minmax(180px,1fr)); gap:12px; margin-bottom:18px; }}
.stat {{ background:#121826; border:1px solid #243048; border-radius:14px; padding:14px; }}
.stat .label {{ color:#9cadc8; font-size:12px; margin-bottom:8px; }}
.stat .value {{ font-size:24px; font-weight:700; }}
.card {{ background:#121826; border:1px solid #243048; border-radius:16px; padding:16px; margin-bottom:18px; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ text-align:left; padding:12px; border-bottom:1px solid #233046; vertical-align:top; font-size:14px; }}
th {{ color:#c3d0e7; background:#151d2c; }}
tr:hover td {{ background:#151c29; }}
.muted {{ color:#97a7c0; }}
.status {{ display:inline-block; padding:5px 10px; border-radius:999px; font-size:12px; border:1px solid #2b3750; background:#0e1420; }}
.member,.creator,.administrator {{ color:#9cf0b8; border-color:#27553a; }}
.left,.banned,.reset_by_admin {{ color:#ffb3b3; border-color:#5b2929; }}
.created {{ color:#ffd28a; border-color:#5d4921; }}
.mono {{ font-family:Consolas,monospace; word-break:break-word; }}
.actions {{ display:flex; gap:8px; flex-wrap:wrap; }}
form {{ margin:0; }}
button {{ cursor:pointer; border:1px solid #2e3a54; background:#182133; color:white; padding:8px 12px; border-radius:10px; }}
button:hover {{ background:#202a3f; }}
.danger {{ border-color:#703131; background:#2a1717; }}
.danger:hover {{ background:#351d1d; }}
.good {{ border-color:#2d5e40; background:#15241a; }}
.good:hover {{ background:#1d3023; }}
.small {{ font-size:12px; }}
</style>
</head>
<body><div class='wrap'>{body_html}</div></body></html>
"""


def render_stats(stats):
    return f"""
    <div class='grid'>
        <div class='stat'><div class='label'>Всего пользователей</div><div class='value'>{stats['total_users']}</div></div>
        <div class='stat'><div class='label'>Активных / подтверждённых</div><div class='value'>{stats['active_ok']}</div></div>
        <div class='stat'><div class='label'>Забанено</div><div class='value'>{stats['banned_count']}</div></div>
        <div class='stat'><div class='label'>Всего логов</div><div class='value'>{stats['total_logs']}</div></div>
    </div>
    """


@app.route("/")
def home():
    return {"ok": True}


@app.route("/auth/create-session", methods=["GET", "POST"])
def create_session():
    token = secrets.token_urlsafe(16)
    conn = db()
    conn.execute(
        "INSERT INTO sessions (token, telegram_user_id, username, first_name, authorized, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (token, None, None, None, 0, "created", now_str(), now_str()),
    )
    conn.commit()
    conn.close()
    short_log("create", tok=token[:8])
    return jsonify({
        "session_token": token,
        "bot_link": f"https://t.me/{BOT_USERNAME}?start={token}"
    })


@app.route("/auth/status/<token>", methods=["GET"])
def auth_status(token):
    session = get_session(token)
    if not session:
        return jsonify({"ok": False, "authorized": False, "error": "session_not_found"}), 404
    return jsonify({
        "ok": True,
        "authorized": bool(session["authorized"]),
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

    if not token or not get_session(token):
        short_log("bad_session", uid=user_id, tok=(token or "")[:8])
        return jsonify({"ok": False, "authorized": False, "error": "invalid_session"}), 400

    if not user_id:
        short_log("no_uid", tok=token[:8])
        return jsonify({"ok": False, "authorized": False, "error": "missing_user_id"}), 400

    user_id_str = str(user_id)
    conn = db()
    banned = conn.execute("SELECT 1 FROM banned WHERE telegram_user_id=?", (user_id_str,)).fetchone() is not None

    if banned:
        conn.execute(
            "UPDATE sessions SET telegram_user_id=?, username=?, first_name=?, authorized=0, status=?, updated_at=? WHERE token=?",
            (user_id_str, username, first_name, "banned", now_str(), token),
        )
        conn.execute(
            "INSERT INTO users (telegram_user_id, username, first_name, last_status, last_seen) VALUES (?, ?, ?, ?, ?) ON CONFLICT(telegram_user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_status=excluded.last_status, last_seen=excluded.last_seen",
            (user_id_str, username, first_name, "banned", now_str()),
        )
        conn.commit()
        conn.close()
        short_log("banned_try", uid=user_id, u=username, s="banned", ok=False)
        socketio.emit("auth_status_updated", {"token": token, "authorized": False, "status": "banned"})
        return jsonify({"ok": True, "authorized": False, "status": "banned"})

    authorized, status_info = check_subscription(user_id)
    conn.execute(
        "UPDATE sessions SET telegram_user_id=?, username=?, first_name=?, authorized=?, status=?, updated_at=? WHERE token=?",
        (user_id_str, username, first_name, 1 if authorized else 0, status_info, now_str(), token),
    )
    conn.execute(
        "INSERT INTO users (telegram_user_id, username, first_name, last_status, last_seen) VALUES (?, ?, ?, ?, ?) ON CONFLICT(telegram_user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_status=excluded.last_status, last_seen=excluded.last_seen",
        (user_id_str, username, first_name, status_info, now_str()),
    )
    conn.commit()
    conn.close()

    short_log("confirm", uid=user_id, u=username, s=status_info, ok=authorized)
    socketio.emit("auth_status_updated", {"token": token, "authorized": authorized, "status": status_info})
    return jsonify({"ok": True, "authorized": authorized, "status": status_info})


@app.route("/admin/stats", methods=["GET"])
def admin_stats():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify({"ok": True, "stats": get_stats()})


@app.route("/admin/users", methods=["GET"])
def admin_users():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY last_seen DESC").fetchall()]
    conn.close()
    return jsonify({"ok": True, "users": rows, "stats": get_stats()})


@app.route("/admin/logs", methods=["GET"])
def admin_logs():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 200").fetchall()]
    conn.close()
    return jsonify({"ok": True, "logs": rows, "stats": get_stats()})


@app.route("/admin/banned", methods=["GET"])
def admin_banned():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM banned ORDER BY banned_at DESC").fetchall()]
    conn.close()
    return jsonify({"ok": True, "banned": rows, "stats": get_stats()})


@app.route("/admin/ban", methods=["POST"])
def admin_ban():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.json or {}
    user_id = data.get("telegram_user_id")
    if not user_id:
        return jsonify({"ok": False, "error": "missing_telegram_user_id"}), 400
    conn = db()
    conn.execute(
        "INSERT INTO banned (telegram_user_id, banned_at) VALUES (?, ?) ON CONFLICT(telegram_user_id) DO UPDATE SET banned_at=excluded.banned_at",
        (str(user_id), now_str()),
    )
    conn.commit()
    conn.close()
    invalidate_user_sessions(user_id, "banned")
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
    conn = db()
    conn.execute("DELETE FROM banned WHERE telegram_user_id=?", (str(user_id),))
    conn.commit()
    conn.close()
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
    conn = db()
    conn.execute("DELETE FROM users WHERE telegram_user_id=?", (str(user_id),))
    conn.commit()
    conn.close()
    invalidate_user_sessions(user_id, "reset_by_admin")
    short_log("reset", uid=user_id)
    return jsonify({"ok": True})


@app.route("/admin/panel", methods=["GET"])
def admin_panel():
    if not is_admin(request):
        return Response("Unauthorized", status=401)
    admin_key = request.args.get("admin_key", "")
    stats = get_stats()
    conn = db()
    users = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY last_seen DESC").fetchall()]
    conn.close()
    rows = []
    for user in users:
        user_id = user.get("telegram_user_id")
        username = user.get("username") or ""
        first_name = user.get("first_name") or ""
        last_status = user.get("last_status") or "unknown"
        last_seen = user.get("last_seen") or ""
        conn = db()
        banned = conn.execute("SELECT 1 FROM banned WHERE telegram_user_id=?", (str(user_id),)).fetchone() is not None
        conn.close()
        rows.append(f"""
        <tr>
            <td class='mono'>{h(user_id)}</td>
            <td>{('@' + h(username)) if username else '<span class="muted">-</span>'}</td>
            <td>{h(first_name) if first_name else '<span class="muted">-</span>'}</td>
            <td><span class='status {h(last_status)}'>{h(last_status)}</span></td>
            <td class='small'>{h(last_seen)}</td>
            <td>{'<span class="status banned">banned</span>' if banned else '<span class="status">active</span>'}</td>
            <td>
                <div class='actions'>
                    <form method='post' action='/admin/panel/ban?admin_key={h(admin_key)}'><input type='hidden' name='telegram_user_id' value='{h(user_id)}'><button class='danger' type='submit'>Ban</button></form>
                    <form method='post' action='/admin/panel/unban?admin_key={h(admin_key)}'><input type='hidden' name='telegram_user_id' value='{h(user_id)}'><button class='good' type='submit'>Unban</button></form>
                    <form method='post' action='/admin/panel/reset-user?admin_key={h(admin_key)}'><input type='hidden' name='telegram_user_id' value='{h(user_id)}'><button type='submit'>Reset</button></form>
                </div>
            </td>
        </tr>
        """)
    body = f"""
    <h1>Admin Panel</h1>
    <div class='topbar'>
        <a class='btn' href='/admin/panel?admin_key={h(admin_key)}'>Users</a>
        <a class='btn' href='/admin/logs-page?admin_key={h(admin_key)}'>Logs</a>
        <a class='btn' href='/admin/banned-page?admin_key={h(admin_key)}'>Banned</a>
    </div>
    {render_stats(stats)}
    <div class='card'>
        <table>
            <thead><tr><th>Telegram ID</th><th>Username</th><th>First name</th><th>Status</th><th>Last seen</th><th>Access</th><th>Actions</th></tr></thead>
            <tbody>{''.join(rows) if rows else '<tr><td colspan="7" class="muted">No users yet</td></tr>'}</tbody>
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
    conn = db()
    logs = [dict(r) for r in conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 200").fetchall()]
    conn.close()
    rows = []
    for item in logs:
        rows.append(f"""
        <tr>
            <td class='small'>{h(item.get('t'))}</td>
            <td>{h(item.get('a'))}</td>
            <td class='mono'>{h(item.get('uid', ''))}</td>
            <td>{h(item.get('u', ''))}</td>
            <td>{h(item.get('s', ''))}</td>
            <td>{h(item.get('ok', ''))}</td>
            <td class='mono small'>{h(item.get('tok', ''))}</td>
        </tr>
        """)
    body = f"""
    <h1>Logs</h1>
    <div class='topbar'>
        <a class='btn' href='/admin/panel?admin_key={h(admin_key)}'>Users</a>
        <a class='btn' href='/admin/logs-page?admin_key={h(admin_key)}'>Logs</a>
        <a class='btn' href='/admin/banned-page?admin_key={h(admin_key)}'>Banned</a>
    </div>
    {render_stats(stats)}
    <div class='card'>
        <table>
            <thead><tr><th>Time</th><th>Action</th><th>User ID</th><th>Username</th><th>Status</th><th>OK</th><th>Token</th></tr></thead>
            <tbody>{''.join(rows) if rows else '<tr><td colspan="7" class="muted">No logs yet</td></tr>'}</tbody>
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
    conn = db()
    banned = [dict(r) for r in conn.execute("SELECT * FROM banned ORDER BY banned_at DESC").fetchall()]
    conn.close()
    rows = []
    for data in banned:
        user_id = data.get('telegram_user_id')
        rows.append(f"""
        <tr>
            <td class='mono'>{h(user_id)}</td>
            <td class='small'>{h(data.get('banned_at'))}</td>
            <td>
                <form method='post' action='/admin/panel/unban?admin_key={h(admin_key)}'>
                    <input type='hidden' name='telegram_user_id' value='{h(user_id)}'>
                    <button class='good' type='submit'>Unban</button>
                </form>
            </td>
        </tr>
        """)
    body = f"""
    <h1>Banned Users</h1>
    <div class='topbar'>
        <a class='btn' href='/admin/panel?admin_key={h(admin_key)}'>Users</a>
        <a class='btn' href='/admin/logs-page?admin_key={h(admin_key)}'>Logs</a>
        <a class='btn' href='/admin/banned-page?admin_key={h(admin_key)}'>Banned</a>
    </div>
    {render_stats(stats)}
    <div class='card'>
        <table>
            <thead><tr><th>Telegram ID</th><th>Banned at</th><th>Action</th></tr></thead>
            <tbody>{''.join(rows) if rows else '<tr><td colspan="3" class="muted">No banned users</td></tr>'}</tbody>
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
        conn = db()
        conn.execute(
            "INSERT INTO banned (telegram_user_id, banned_at) VALUES (?, ?) ON CONFLICT(telegram_user_id) DO UPDATE SET banned_at=excluded.banned_at",
            (str(user_id), now_str()),
        )
        conn.commit()
        conn.close()
        invalidate_user_sessions(user_id, "banned")
        short_log("ban", uid=user_id)
    return redirect(url_for("admin_panel", admin_key=admin_key))


@app.route("/admin/panel/unban", methods=["POST"])
def admin_panel_unban():
    if not is_admin(request):
        return Response("Unauthorized", status=401)
    admin_key = request.args.get("admin_key", "")
    user_id = request.form.get("telegram_user_id")
    if user_id:
        conn = db()
        conn.execute("DELETE FROM banned WHERE telegram_user_id=?", (str(user_id),))
        conn.commit()
        conn.close()
        short_log("unban", uid=user_id)
    return redirect(url_for("admin_panel", admin_key=admin_key))


@app.route("/admin/panel/reset-user", methods=["POST"])
def admin_panel_reset_user():
    if not is_admin(request):
        return Response("Unauthorized", status=401)
    admin_key = request.args.get("admin_key", "")
    user_id = request.form.get("telegram_user_id")
    if user_id:
        conn = db()
        conn.execute("DELETE FROM users WHERE telegram_user_id=?", (str(user_id),))
        conn.commit()
        conn.close()
        invalidate_user_sessions(user_id, "reset_by_admin")
        short_log("reset", uid=user_id)
    return redirect(url_for("admin_panel", admin_key=admin_key))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port)
