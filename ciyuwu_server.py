#!/usr/bin/env python3
"""词与物 — HTTP API 服务

让任何能发HTTP请求的AI都能玩。省token版。

启动：
    python ciyuwu_server.py
    默认 localhost:8877

用法：
    POST /new          开新局（可选 seed, compact 参数）
    POST /cmd          执行指令（需带 session 或 state）
    GET  /             说明

两种模式：
  compact=True  — 省token：状态存服务端，返回精简文本+状态摘要
  compact=False — 完整模式：每次返回完整state（兼容旧接口）

compact模式省token原理：
  1. 状态存服务端，AI只需带session_id（16字符），不用带2k+的snapshot
  2. 输出去掉指令提示、重复描述
  3. 状态用一行JSON摘要代替完整snapshot
  4. 支持批量指令（前进5, 攻3）减少交互次数
"""

import sys, os, io, json, threading, time, uuid

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from flask import Flask, request, jsonify
from engine import new_game as _new_game, cmd as _cmd, _ensure_init, _snapshot, _restore, _status_bar, _atomic_json_write, _SAVE_FILE

app = Flask(__name__)
_lock = threading.Lock()
_initialized = False

# ── 服务端session存储 ──
_sessions = {}  # session_id -> (state, last_access_time, last_words)
_SESSION_MAX = 100  # 最多存100个session
_SESSION_TTL = 3600  # 1小时过期

# ── 跨session meta持久化 ──
# F-2 修复：与 dark_engine / engine 共用 ciyuwu_save.json
_META_FILE = _SAVE_FILE
# 旧 ciyuwu_meta.json 自动迁移到新名
_old_meta = os.path.join(_HERE, "ciyuwu_meta.json")
if os.path.exists(_old_meta) and not os.path.exists(_META_FILE):
    try:
        os.rename(_old_meta, _META_FILE)
    except Exception as _e:        import sys; print(f"[WARN] {_e}", file=sys.stderr)
_META_KEYS = ["echoes", "runs", "echo_map", "killed_bosses",
              "unlocked_origins", "wall_writings", "total_wait",
              "unlocked_achievements", "heart_slots",
              "cross_word_stats", "game_diary",
              "cross_deform_count", "cross_swallow_count"]

def _load_meta():
    """从磁盘读meta进度。"""
    if not os.path.exists(_META_FILE):
        return {}
    try:
        # BUG-14 修复：跨进程读也加锁，避免读到半写的 .tmp 文件
        with _meta_lock("r"):
            with open(_META_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        # BUG-FIX：只吞 JSON/IO 错误，别吞掉所有异常（隐藏 bug）
        import sys
        print(f"[WARN] _load_meta 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

def _save_meta(meta):
    """把meta进度写到磁盘（原子写 + 跨进程文件锁）。"""
    try:
        # BUG-14 修复：跨进程并发下用 advisory lock 防止多副本同时写覆盖
        with _meta_lock("w"):
            _atomic_json_write(_META_FILE, meta)
    except Exception as e:
        import sys
        print(f"[WARN] _save_meta 失败: {e}", file=sys.stderr)

def _meta_lock(mode):
    """跨进程文件锁 context manager。
    优先用 fcntl.flock（Unix），否则用 msvcrt（Windows），都没有则降级为空锁。
    """
    class _NullLock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
    try:
        if hasattr(os, 'O_EXCL'):  # Unix
            import fcntl as _f
            lock_path = _META_FILE + ".lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            op = _f.LOCK_EX if mode == "w" else _f.LOCK_SH
            try:
                _f.flock(fd, op)
            except Exception:
                pass  # flock 失败仍继续，不阻塞业务
            class _UnixLock:
                def __enter__(self): return self
                def __exit__(self, *a):
                    try: _f.flock(fd, _f.LOCK_UN)
                    except: pass
                    try: os.close(fd)
                    except: pass
                    return False
            return _UnixLock()
        elif os.name == 'nt':  # Windows
            import msvcrt
            lock_path = _META_FILE + ".lock"
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                # 简单 lock 1 字节（advisory）
                if mode == "w":
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                else:
                    msvcrt.locking(fd, msvcrt.LK_RLCK, 1)
            except Exception:
                pass
            class _WinLock:
                def __enter__(self): return self
                def __exit__(self, *a):
                    try:
                        if mode == "w":
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                        else:
                            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    except: pass
                    try: os.close(fd)
                    except: pass
                    return False
            return _WinLock()
    except Exception:
        pass
    return _NullLock()

def _extract_meta(state):
    """从state里提取meta字段。"""
    return {k: state.get(k) for k in _META_KEYS if k in state}

def _merge_meta(disk_meta, session_meta):
    """合并两个meta dict——列表去重、dict按键合并、计数器取max。"""
    result = dict(disk_meta)
    for k, v in session_meta.items():
        if v is None:
            continue
        if k in ('killed_bosses', 'unlocked_origins', 'unlocked_achievements',
                  'wall_writings', 'heart_slots', 'game_diary'):
            existing = set(str(x) for x in (result.get(k, []) or []))
            merged = [x for x in (result.get(k, []) or [])]
            for item in (v if isinstance(v, list) else [v]):
                if str(item) not in existing:
                    merged.append(item)
                    existing.add(str(item))
            result[k] = merged
        elif k in ('echoes', 'runs', 'total_wait', 'cross_deform_count', 'cross_swallow_count'):
            result[k] = max(result.get(k, 0) or 0, v)
        elif k in ('echo_map', 'cross_word_stats'):
            existing = result.get(k, {}) or {}
            if isinstance(v, dict):
                for dk, dv in v.items():
                    if dk not in existing:
                        existing[dk] = dv
                    elif isinstance(dv, list) and isinstance(existing[dk], list):
                        seen = set(str(x) for x in existing[dk])
                        for item in dv:
                            if str(item) not in seen:
                                existing[dk].append(item)
                                seen.add(str(item))
                    else:
                        existing[dk] = dv
            result[k] = existing
        else:
            result[k] = v
    return result


def _inject_meta(state, meta):
    """把meta字段注入state。"""
    for k in _META_KEYS:
        if k in meta:
            state[k] = meta[k]
    return state


def _init():
    global _initialized
    if not _initialized:
        _ensure_init()
        _initialized = True


def _cleanup_sessions():
    """清理过期session。"""
    now = time.time()
    expired = [sid for sid, (_, t, _) in _sessions.items() if now - t > _SESSION_TTL]
    for sid in expired:
        del _sessions[sid]
    # 如果太多，删最旧的
    if len(_sessions) > _SESSION_MAX:
        sorted_sessions = sorted(_sessions.items(), key=lambda x: x[1][1])
        for sid, _ in sorted_sessions[:len(_sessions) - _SESSION_MAX]:
            del _sessions[sid]


def _compact_text(text, phase):
    """压缩游戏输出——只去掉指令提示，保留所有叙事。叙事是游戏的魂。"""
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        # 只去掉指令提示行——AI不需要每步读操作说明
        # 单引号包裹的指令列表：'前进' / '状态' / '回镇'
        if stripped.startswith("'") and any(kw in stripped for kw in [
            '前进', '攻', '防', '术', '逃', '说', '状态', '回镇',
            '帮助', '新角', '确认', '词库', '任务', '遗忘', '用',
            '重投', '来路', '买', '写', '喊', '求签', '祈祷',
        ]):
            continue
        # 镇上的指令列表行：工会 / 商店 / 酒馆 / ...
        if stripped.startswith('工会') and '出镇' in stripped:
            continue
        # 状态 / 词库 / 遗刻 / 任务 / 遗忘 / 帮助
        if stripped.startswith('状态') and ('帮助' in stripped or '词库' in stripped) and len(stripped) < 40:
            continue
        # 所有叙事、对话、描述——原封不动保留
        result.append(line)
    return '\n'.join(result).strip()


# ── 从state dict直接生成状态摘要（不反序列化DarkWorld） ──
_PHASE_CODES = {
    "init": "0", "creation": "1", "town": "2",
    "explore": "3", "combat": "4", "fork": "5",
    "dead": "6", "dead_who": "7", "dead_wipe": "8",
    "void": "9", "judgment": "A", "ending": "B",
}

def _status_from_state(state, last_words=None):
    """从state dict直接生成compact状态摘要。

    不需要反序列化DarkWorld，直接读dict字段。
    返回 (status_string, current_words_tuple)。
    """
    phase = state.get("phase", "")
    p = _PHASE_CODES.get(phase, phase)
    area = state.get("area", "") or ""
    hp = state.get("hp", 0)
    mhp = state.get("max_hp", 1)
    mp = state.get("mp", 0)
    mmp = state.get("max_mp", 1)
    c = state.get("compliance", 0)
    h = state.get("hunger", 0)

    parts = [f"{p}|{area}|{hp}/{mhp}|{mp}/{mmp}|{c}|{h}"]

    gold = state.get("gold", 0)
    if gold > 0:
        parts.append(f"g{gold}")
    her = state.get("her_presence", 0)
    if her > 0:
        parts.append(f"h{her}")
    r = state.get("r_flags", 0)
    if r > 0:
        parts.append(f"r{r}")

    # 词表——只在变化时输出
    words = state.get("words", [])
    current_words = tuple(words) if words else ()
    if current_words != last_words:
        parts.append(f"w:{','.join(words) if words else '-'}")

    # 战斗
    combat_data = state.get("_combat")
    if phase == "combat" and combat_data:
        enemy = combat_data.get("enemy", {})
        parts.append(f"e:{enemy.get('name','?')}:{enemy.get('hp',0)}")
        cds = combat_data.get("word_cooldowns")
        if cds:
            cd_str = ','.join(f"{k}({v})" for k, v in cds.items())
            parts.append(f"cd:{cd_str}")
        sealed = combat_data.get("skills_sealed")
        if sealed:
            parts.append(f"sealed:{','.join(sealed)}")

    # 子状态
    sub = ""
    if state.get("_pending_pickup"):
        sub = f"pickup:{state['_pending_pickup'].get('name','')}"
    elif state.get("_square_sit", 0) > 0:
        sub = f"square:{state['_square_sit']}"
    elif state.get("current_sage"):
        sub = f"sage:{state['current_sage'].get('name','')}"
    elif state.get("current_broken"):
        sub = "broken"
    elif state.get("current_special"):
        sub = f"special:{state['current_special'].get('name','')}"
    elif state.get("_light_bearer_active"):
        # BUG-6 修复：与 engine._status_bar / test_all.py 统一用 "light_bearer"
        sub = "light_bearer"
    elif state.get("_crease_active"):
        sub = "crease"
    elif phase == "fork":
        sub = "fork"
    elif phase == "dead_who":
        sub = "dead_who"
    elif phase == "dead_wipe":
        sub = "dead_wipe"
    elif phase == "judgment":
        sub = f"judge:{state.get('_judgment_step', 0)}"
    if sub:
        parts.append(sub)

    return '|'.join(parts), current_words


@app.route('/')
def index():
    return jsonify({
        "game": "词与物",
        "description": "灰白世界的文字冒险。你不说话，就不存在。",
        "endpoints": {
            "POST /new": "开新局。可选 seed, compact=true 参数。",
            "POST /cmd": "执行指令。compact模式带session，否则带state。",
        },
        "compact_mode": {
            "description": "省token模式：状态存服务端，输出精简",
            "savings": "每步省~2000字符(4000 tokens)的state传输，输出省~30%重复文本",
            "usage": "POST /new {compact:true} → 返回session_id; POST /cmd {session:'...', cmd:'前进5'}",
        },
        "commands": [
            "新角", "确认", "前进", "前进5", "回镇", "出镇 灰林",
            "说 [话]", "攻", "攻3", "防", "术", "逃", "词库", "状态",
            "商店", "买 [物品]", "残壁", "写 [话]", "赎词",
            "塔", "喊 [话]", "酒馆", "神殿", "求签", "广场",
            "打工", "黑活", "用 [物品]", "脱出",
        ],
        "batch": "前进5=连走5步, 攻3=连攻3次, 前进;说 我在;前进=串联",
    })


@app.route('/new', methods=['POST'])
def new_game():
    _init()
    body = request.get_json(silent=True) or {}
    seed = body.get("seed")
    compact = body.get("compact", False)

    with _lock:
        _cleanup_sessions()
        # 合并所有session的meta再存一次（避免逐个覆盖丢数据）
        merged_meta = _load_meta()
        for sid, (s, _, _) in _sessions.items():
            session_meta = _extract_meta(s)
            merged_meta = _merge_meta(merged_meta, session_meta)
        _save_meta(merged_meta)

        state, text = _new_game(seed=seed)
        # 注入持久化的meta（echoes/killed_bosses等不因/new重置）
        if merged_meta:
            state = _inject_meta(state, merged_meta)

    if compact:
        # 服务端存状态，返回session_id
        session_id = uuid.uuid4().hex[:16]
        # 叙事完整保留，只去指令提示
        compact_output = _compact_text(text, "init")
        # 从state直接提取摘要，不用反序列化DarkWorld
        status, last_words = _status_from_state(state)
        _sessions[session_id] = (state, time.time(), last_words)
        return jsonify({
            "session": session_id,
            "text": compact_output,
            "status": status,
            "done": False,
        })
    else:
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
    session_id = body.get("session")
    state = body.get("state")
    compact = body.get("compact", False) or (session_id is not None)

    if not instruction:
        return jsonify({"error": "缺少 cmd 字段"}), 400

    with _lock:
        _cleanup_sessions()

        # 从session或直接state恢复
        last_words = None
        if session_id:
            if session_id not in _sessions:
                # BUG-FIX：session 过期/不存在时不静默回退到 body state
                # 否则玩家会以为自己在用 session，实际在用旧 body state
                return jsonify({"error": "session 已过期或不存在", "session": session_id}), 401
            state, _, last_words = _sessions[session_id]
        elif state is None:
            return jsonify({"error": "缺少 session 或 state 字段"}), 400

        new_state, output = _cmd(state, instruction)

        # 持久化meta进度——合并磁盘上的meta再写，避免多session覆盖
        disk_meta = _load_meta()
        session_meta = _extract_meta(new_state)
        disk_meta = _merge_meta(disk_meta, session_meta)
        _save_meta(disk_meta)

        if compact:
            # 存回服务端
            if session_id is None:
                session_id = uuid.uuid4().hex[:16]
            # 叙事完整保留，只去指令提示
            compact_output = _compact_text(output, "")
            # 从state直接提取摘要
            status, current_words = _status_from_state(new_state, last_words)
            _sessions[session_id] = (new_state, time.time(), current_words)

            return jsonify({
                "session": session_id,
                "text": compact_output,
                "status": status,
                "done": new_state.get("phase") == "ending",
            })
        else:
            return jsonify({
                "text": output,
                "state": new_state,
                # BUG-5 修复：旧接口也返回真实 done 状态，与 compact 模式一致
                "done": new_state.get("phase") == "ending",
            })


@app.route('/sessions', methods=['GET'])
def list_sessions():
    """调试用——看当前存了多少session。"""
    return jsonify({
        "count": len(_sessions),
        "sessions": {sid: {"age": int(time.time() - t)} for sid, (_, t, _) in _sessions.items()},
    })


if __name__ == '__main__':
    _init()
    print("词与物 HTTP API — localhost:8877")
    print("POST /new {compact:true}  省token模式")
    print("POST /cmd {session,cmd}   执行指令")
    print("GET  /sessions            查看session数")
    app.run(host='0.0.0.0', port=8877, debug=False)
