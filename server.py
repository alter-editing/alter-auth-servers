import os
from flask import Flask, jsonify

app = Flask(__name__)

@app.get("/")
def home():
    return jsonify({"ok": True, "message": "server is running"})

@app.post("/auth/create-session")
def create_session():
    return jsonify({
        "session_token": "test",
        "bot_link": "https://t.me/alterediting"
    })

@app.get("/auth/status/<token>")
def status(token):
    return jsonify({"authorized": False})

@app.post("/auth/bot-confirm")
def confirm():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
