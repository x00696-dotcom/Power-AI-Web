import os
from pathlib import Path
from flask import Flask, send_file, jsonify

BASE = Path(__file__).resolve().parent
app = Flask(__name__)

@app.get("/")
def index():
    return send_file(BASE / "app.html")

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "Power AI V05 Secure"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
