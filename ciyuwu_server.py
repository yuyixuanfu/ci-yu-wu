#!/usr/bin/env python3
"""词与物 — HTTP API 服务

让任何能发HTTP请求的AI都能玩。

启动：
    python ciyuwu_server.py
    默认 localhost:8877

用法：
    POST /new          开新局
    POST /cmd          执行指令（需带state）
    GET  /             说明

每个请求返回新的state，下次请求带回来。服务端不存状态。
"""

import sys, os, io, json, threading

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from flask import Flask, request, jsonify
from engine import new_game as _new_game, cmd as _cmd, _ensure_init, _snapshot, _restore, _status_bar

app = Flask(__name__)
_lock = threading.Lock()
_initialized = False


def _init():
    global _initialized
    if not _initialized:
        _ensure_init()
        _initialized = True


@app.route('/')
def index():
    return jsonify({
        "game": "词与物",
        "description": "灰白世界的文字冒险。你不说话，就不存在。",
        "endpoints": {
            "POST /new": "开新局。可选 seed 参数。",
            "POST /cmd": "执行指令。需带 cmd 和 state。",
        },
        "example_new": {"seed": 42},
        "example_cmd": {"cmd": "前进", "state": "{...上一次返回的state}"},
        "commands": [
            "新角", "确认", "前进", "前进5", "回镇", "出镇 灰林",
            "说 [话]", "攻", "防", "术", "逃", "词库", "状态",
            "商店", "买 [物品]", "残壁", "写 [话]", "赎词",
            "塔", "喊 [话]", "酒馆", "神殿", "求签", "广场",
            "打工", "黑活", "用 [物品]", "脱出",
        ],
    })


@app.route('/new', methods=['POST'])
def new_game():
    _init()
    body = request.get_json(silent=True) or {}
    seed = body.get("seed")

    with _lock:
        state, text = _new_game(seed=seed)

    return jsonify({
        "text": text,
        "state": state,
        "done": False,
    })


@app.route('/cmd', methods=['POST'])
def cmd_game():
    _init()
    body = request.get_json(silent=True) or {}
    instruction = body.get("cmd", "")
    state = body.get("state")

    if not instruction:
        return jsonify({"error": "缺少 cmd 字段"}), 400
    if state is None:
        return jsonify({"error": "缺少 state 字段（用 /new 获取初始 state）"}), 400

    with _lock:
        from dark_engine import DarkWorld
        w = DarkWorld()
        _restore(w, state)
        text = w.cmd(instruction)
        w._save_meta()
        new_state = _snapshot(w)
        done = w.phase == "ending"

    return jsonify({
        "text": text,
        "state": new_state,
        "done": done,
    })


if __name__ == '__main__':
    _init()
    print("词与物 HTTP API — localhost:8877")
    print("POST /new  开局 | POST /cmd  执行指令 | GET /  说明")
    app.run(host='0.0.0.0', port=8877, debug=False)
