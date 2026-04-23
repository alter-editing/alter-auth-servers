import os
import json
import secrets
import requests
from datetime import datetime
from flask import Flask, request, jsonify, Response

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

    if len(LOGS) > 2000:
        LOGS = LOGS[-2000:]

    save_json(LOGS_FILE, LOGS)


def save_all():
    save_json(SESSIONS_FILE, SESSIONS)
    save_json(USERS_FILE, USERS)
    save_json(BANNED_FILE, BANNED)
    save_json(LOGS_FILE, LOGS)


def is_admin(req):
    key = req.headers.get("X-Admin-Key") or req.args.get("admin_key")
    return bool(ADMIN_KEY) and key == ADMIN_KEY


def require_admin():
    if not is_admin(request):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


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
    denied = require_admin()
    if denied:
        return denied

    user_list = list(USERS.values())
    user_list.sort(key=lambda x: x.get("last_seen", ""), reverse=True)

    return jsonify({
        "ok": True,
        "users": user_list,
        "banned": BANNED
    })


@app.route("/admin/logs", methods=["GET"])
def admin_logs():
    denied = require_admin()
    if denied:
        return denied

    return jsonify({
        "ok": True,
        "logs": LOGS[-300:]
    })


@app.route("/admin/banned", methods=["GET"])
def admin_banned():
    denied = require_admin()
    if denied:
        return denied

    return jsonify({
        "ok": True,
        "banned": BANNED
    })


@app.route("/admin/ban", methods=["POST"])
def admin_ban():
    denied = require_admin()
    if denied:
        return denied

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
    denied = require_admin()
    if denied:
        return denied

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
    denied = require_admin()
    if denied:
        return denied

    data = request.json or {}
    user_id = data.get("telegram_user_id")

    if not user_id:
        return jsonify({"ok": False, "error": "missing_telegram_user_id"}), 400

    user_id_str = str(user_id)

    if user_id_str in USERS:
        del USERS[user_id_str]

    for token in list(SESSIONS.keys()):
        if str(SESSIONS[token].get("telegram_user_id")) == user_id_str:
            SESSIONS[token]["authorized"] = False
            SESSIONS[token]["status"] = "reset_by_admin"

    save_all()
    add_log("admin_reset_user", {"telegram_user_id": user_id})

    return jsonify({"ok": True})


# -----------------------------
# HTML ADMIN PANEL
# -----------------------------

@app.route("/admin", methods=["GET"])
def admin_panel():
    denied = require_admin()
    if denied:
        return denied

    html = f"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>AlterEditing Admin</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{
            box-sizing: border-box;
            font-family: Arial, sans-serif;
        }}
        body {{
            margin: 0;
            background: #0d0f14;
            color: #fff;
        }}
        .wrap {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 24px;
        }}
        h1 {{
            margin: 0 0 18px;
            font-size: 28px;
        }}
        .topbar {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin-bottom: 20px;
        }}
        .btn {{
            background: #1e2533;
            color: #fff;
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 12px;
            padding: 10px 14px;
            cursor: pointer;
        }}
        .btn:hover {{
            background: #273146;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1.2fr 1fr;
            gap: 20px;
        }}
        .card {{
            background: #121723;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            padding: 18px;
            box-shadow: 0 6px 24px rgba(0,0,0,0.25);
        }}
        .card h2 {{
            margin-top: 0;
            font-size: 20px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        th, td {{
            text-align: left;
            padding: 10px 8px;
            border-bottom: 1px solid rgba(255,255,255,0.08);
            vertical-align: top;
            font-size: 14px;
        }}
        th {{
            color: #9fb2d9;
            font-weight: 600;
        }}
        .tag {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 999px;
            font-size: 12px;
            background: rgba(255,255,255,0.08);
        }}
        .tag.green {{ background: rgba(46, 160, 67, 0.18); color: #72e28d; }}
        .tag.red {{ background: rgba(248, 81, 73, 0.18); color: #ff8f87; }}
        .tag.yellow {{ background: rgba(210, 153, 34, 0.18); color: #f0c674; }}
        .actions {{
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }}
        .small-btn {{
            border: 0;
            border-radius: 10px;
            padding: 8px 10px;
            cursor: pointer;
            color: white;
            font-size: 13px;
        }}
        .ban {{ background: #8a2632; }}
        .unban {{ background: #1f6f43; }}
        .reset {{ background: #825d1a; }}
        .log-box {{
            max-height: 750px;
            overflow: auto;
            display: flex;
            flex-direction: column;
            gap: 10px;
        }}
        .log-item {{
            background: #0d1220;
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 12px;
            padding: 10px 12px;
            font-size: 13px;
        }}
        .muted {{
            color: #9fa8bb;
        }}
        .mono {{
            font-family: Consolas, monospace;
            word-break: break-word;
        }}
        .status {{
            margin-bottom: 12px;
            color: #9fb2d9;
            font-size: 13px;
        }}
        .empty {{
            color: #8e97aa;
            padding: 12px 0;
        }}
        @media (max-width: 1050px) {{
            .grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <h1>AlterEditing Admin Console</h1>

        <div class="topbar">
            <button class="btn" onclick="reloadAll()">Refresh</button>
            <button class="btn" onclick="reloadUsers()">Refresh Users</button>
            <button class="btn" onclick="reloadLogs()">Refresh Logs</button>
        </div>

        <div class="grid">
            <div class="card">
                <h2>Users</h2>
                <div class="status" id="usersStatus">Loading users...</div>
                <div style="overflow:auto;">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Username</th>
                                <th>Name</th>
                                <th>Status</th>
                                <th>Seen</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="usersTable"></tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <h2>Logs</h2>
                <div class="status" id="logsStatus">Loading logs...</div>
                <div class="log-box" id="logsBox"></div>
            </div>
        </div>
    </div>

    <script>
        const ADMIN_KEY = new URLSearchParams(window.location.search).get("admin_key");

        function esc(v) {{
            if (v === null || v === undefined) return "";
            return String(v)
                .replaceAll("&", "&amp;")
                .replaceAll("<", "&lt;")
                .replaceAll(">", "&gt;")
                .replaceAll('"', "&quot;");
        }}

        function statusBadge(status, isBanned) {{
            if (isBanned || status === "banned") {{
                return '<span class="tag red">banned</span>';
            }}
            if (status === "member" || status === "administrator" || status === "creator") {{
                return '<span class="tag green">' + esc(status) + '</span>';
            }}
            if (status) {{
                return '<span class="tag yellow">' + esc(status) + '</span>';
            }}
            return '<span class="tag">unknown</span>';
        }}

        async function apiGet(url) {{
            const r = await fetch(url);
            return await r.json();
        }}

        async function apiPost(url, body) {{
            const r = await fetch(url, {{
                method: "POST",
                headers: {{
                    "Content-Type": "application/json",
                    "X-Admin-Key": ADMIN_KEY || ""
                }},
                body: JSON.stringify(body || {{}})
            }});
            return await r.json();
        }}

        async function reloadUsers() {{
            document.getElementById("usersStatus").innerText = "Loading users...";

            const data = await apiGet(`/admin/users?admin_key=${{encodeURIComponent(ADMIN_KEY || "")}}`);
            const tbody = document.getElementById("usersTable");
            tbody.innerHTML = "";

            if (!data.ok) {{
                document.getElementById("usersStatus").innerText = "Failed to load users.";
                return;
            }}

            const banned = data.banned || {{}};
            const users = data.users || [];

            document.getElementById("usersStatus").innerText = `Users: ${{users.length}}`;

            if (!users.length) {{
                tbody.innerHTML = `<tr><td colspan="6" class="empty">No users yet.</td></tr>`;
                return;
            }}

            for (const user of users) {{
                const id = user.telegram_user_id;
                const isBanned = !!banned[String(id)];

                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td class="mono">${{esc(id)}}</td>
                    <td>${{esc(user.username || "")}}</td>
                    <td>${{esc(user.first_name || "")}}</td>
                    <td>${{statusBadge(user.last_status, isBanned)}}</td>
                    <td class="mono">${{esc(user.last_seen || "")}}</td>
                    <td>
                        <div class="actions">
                            <button class="small-btn ban" onclick="banUser('${{esc(id)}}')">Ban</button>
                            <button class="small-btn unban" onclick="unbanUser('${{esc(id)}}')">Unban</button>
                            <button class="small-btn reset" onclick="resetUser('${{esc(id)}}')">Reset</button>
                        </div>
                    </td>
                `;
                tbody.appendChild(tr);
            }}
        }}

        async function reloadLogs() {{
            document.getElementById("logsStatus").innerText = "Loading logs...";

            const data = await apiGet(`/admin/logs?admin_key=${{encodeURIComponent(ADMIN_KEY || "")}}`);
            const box = document.getElementById("logsBox");
            box.innerHTML = "";

            if (!data.ok) {{
                document.getElementById("logsStatus").innerText = "Failed to load logs.";
                return;
            }}

            const logs = data.logs || [];
            document.getElementById("logsStatus").innerText = `Logs: ${{logs.length}}`;

            if (!logs.length) {{
                box.innerHTML = `<div class="empty">No logs yet.</div>`;
                return;
            }}

            const reversed = [...logs].reverse();
            for (const log of reversed) {{
                const div = document.createElement("div");
                div.className = "log-item";
                div.innerHTML = `
                    <div><strong>${{esc(log.action)}}</strong></div>
                    <div class="muted mono">${{esc(log.time)}}</div>
                    <div class="mono">${{esc(JSON.stringify(log.extra || {{}}, null, 2))}}</div>
                `;
                box.appendChild(div);
            }}
        }}

        async function banUser(userId) {{
            if (!confirm(`Ban user ${{userId}}?`)) return;
            const data = await apiPost("/admin/ban", {{ telegram_user_id: userId }});
            if (data.ok) {{
                await reloadAll();
            }} else {{
                alert("Ban failed: " + JSON.stringify(data));
            }}
        }}

        async function unbanUser(userId) {{
            if (!confirm(`Unban user ${{userId}}?`)) return;
            const data = await apiPost("/admin/unban", {{ telegram_user_id: userId }});
            if (data.ok) {{
                await reloadAll();
            }} else {{
                alert("Unban failed: " + JSON.stringify(data));
            }}
        }}

        async function resetUser(userId) {{
            if (!confirm(`Reset user ${{userId}}?`)) return;
            const data = await apiPost("/admin/reset-user", {{ telegram_user_id: userId }});
            if (data.ok) {{
                await reloadAll();
            }} else {{
                alert("Reset failed: " + JSON.stringify(data));
            }}
        }}

        async function reloadAll() {{
            await reloadUsers();
            await reloadLogs();
        }}

        reloadAll();
    </script>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
