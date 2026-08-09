import os
from flask import Flask
import threading

app = Flask(__name__)

@app.route('/')
def home():
    return "I'm alive"

def run():
    # 優先讀取 Render 提供的 PORT，如果沒有（例如本機測試）則預設 8000
    port = int(os.environ.get("PORT", 8000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    thread = threading.Thread(target=run)
    thread.start()
