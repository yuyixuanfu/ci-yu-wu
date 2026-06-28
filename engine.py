#!/usr/bin/env python3
"""词与物 — AI可玩版引擎

接口:
  new_game()          → (state, text)   开新局
  cmd(state, inst)    → (state, text)   执行指令
  load_game()         → state           从文件读
  save_game(state)    → None            存文件

AI接入方式:
  1. 函数调用: import engine; state = engine.new_game()[0]; state, text = engine.cmd(state, "新角")
  2. 命令行:   python engine.py "新角"  (自动从文件读存档、执行、存回)
  3. 工具调用: 配合 tool-schema.json 使用

特性:
  - 批量指令: "前进5" 连走5步, "攻3" 连攻3次
  - 分号串联: "前进;前进;说 我在" 依次执行
  - 状态栏JSON: 每次输出末尾带紧凑状态
  - 确定性PRNG: 同seed同指令=同结果
"""
import sys, os, io, json, base64, hashlib, time

# 确保UTF-8输出
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "ciyuwu_save.json")

# ── 确定性PRNG ──────────────────────────────────────
def _mulberry32(seed):
    """确定性随机数生成器。同seed=同序列。"""
    def _gen():
        nonlocal seed
        while True:
            seed = (seed + 0x6D2B79F5) & 0xFFFFFFFF
            t = seed
            t = ((t ^ (t >> 15)) * t | 1) & 0xFFFFFFFF
            t = ((t ^ (t >> 15)) * t | 1) & 0xFFFFFFFF
            yield (t ^ (t >> 15)) & 0xFFFFFFFF
    return _gen()

class _DetRandom:
    """替换random的确定性随机。"""
    def __init__(self, seed=42):
        self._gen = _mulberry32(seed)
        self._state = seed

    def seed(self, s):
        self._state = s
        self._gen = _mulberry32(s)

    def random(self):
        return next(self._gen) / 0xFFFFFFFF

    def randint(self, a, b):
        return a + int(self.random() * (b - a + 1))

    def choice(self, seq):
        return seq[self.randint(0, len(seq) - 1)]

    def sample(self, seq, k):
        idxs = list(range(len(seq)))
        result = []
        for _ in range(min(k, len(idxs))):
            i = self.randint(0, len(idxs) - 1)
            result.append(seq[idxs[i]])
            idxs.pop(i)
        return result

    def shuffle(self, seq):
        for i in range(len(seq) - 1, 0, -1):
            j = self.randint(0, i)
            seq[i], seq[j] = seq[j], seq[i]

    def choices(self, population, weights=None, k=1):
        """加权随机选择——兼容random.choices。"""
        if weights is None:
            return [self.choice(population) for _ in range(k)]
        # 加权选择
        total = sum(weights)
        result = []
        for _ in range(k):
            r = self.random() * total
            cum = 0
            for item, w in zip(population, weights):
                cum += w
                if r <= cum:
                    result.append(item)
                    break
            else:
                result.append(population[-1])
        return result


# ── 注入确定性随机到dark_engine ─────────────────────
import random as _stdlib_random
_det_rng = _DetRandom(42)

def _patch_random():
    """把dark_engine/dark_combat/dark_data的random换成确定性版本。"""
    import dark_engine, dark_combat, dark_data
    dark_engine.random = _det_rng
    dark_combat.random = _det_rng
    dark_data.random = _det_rng
    # re模块不需要换
    # 滚属性用的random也走_det_rng
    dark_data._rng = _det_rng


# ── 快照 ────────────────────────────────────────────
_SKIP_ATTRS = {'combat'}

def _to_jsonable(obj):
    if isinstance(obj, set):
        return {'__t': 'set', 'v': [_to_jsonable(x) for x in sorted(obj, key=str)]}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return obj

def _from_jsonable(obj):
    if isinstance(obj, dict):
        if obj.get('__t') == 'set':
            return set(_from_jsonable(x) for x in obj.get('v', []))
        return {k: _from_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_from_jsonable(x) for x in obj]
    return obj

def _snapshot(w):
    """把DarkWorld所有可序列化属性拍成dict。"""
    state = {}
    for attr in dir(w):
        if attr.startswith('__') or attr in _SKIP_ATTRS:
            continue
        val = getattr(w, attr, None)
        if callable(val):
            continue
        try:
            state[attr] = _to_jsonable(val)
        except:
            pass
    # combat单独处理
    if w.combat:
        c = w.combat
        state['_combat'] = {
            'player': _to_jsonable(c.player),
            'enemy': _to_jsonable(c.enemy),
            'turn': c.turn,
            'word_cooldowns': _to_jsonable(c.word_cooldowns),
            'skills_sealed': _to_jsonable(c.skills_sealed),
            'layer': c.layer,
            'deformation_count': getattr(c, 'deformation_count', 0),
            'swallow_count': getattr(c, 'swallow_count', 0),
            'word_fate': _to_jsonable(getattr(c, 'word_fate', {})),
            'player_defending': c.player_defending,
            'snapshot_stolen': getattr(c, 'snapshot_stolen', False),
            'stolen_word': getattr(c, 'stolen_word', None),
            'compliance_declarations': getattr(c, 'compliance_declarations', 0),
            'silence_bonus': getattr(c, 'silence_bonus', False),
            'silence_turns': getattr(c, 'silence_turns', 0),
            '_conv_questions_asked': getattr(c, '_conv_questions_asked', 0),
            '_defend_streak': getattr(c, '_defend_streak', 0),
        }
    # PRNG状态
    state['_rng_state'] = _det_rng._state
    return state

def _restore(w, state):
    """恢复快照。"""
    rng_state = state.pop('_rng_state', None)
    if rng_state is not None:
        _det_rng.seed(rng_state)

    combat_data = state.pop('_combat', None)
    for attr, val in state.items():
        if attr in _SKIP_ATTRS:
            continue
        try:
            setattr(w, attr, _from_jsonable(val))
        except:
            pass
    # 恢复combat
    if combat_data:
        from dark_combat import CombatState
        player = _from_jsonable(combat_data['player'])
        enemy = _from_jsonable(combat_data['enemy'])
        c = CombatState(player, enemy, combat_data.get('layer', '灰林'))
        c.turn = combat_data.get('turn', 0)
        c.word_cooldowns = _from_jsonable(combat_data.get('word_cooldowns', {}))
        c.skills_sealed = _from_jsonable(combat_data.get('skills_sealed', []))
        c.deformation_count = combat_data.get('deformation_count', 0)
        c.swallow_count = combat_data.get('swallow_count', 0)
        c.word_fate = _from_jsonable(combat_data.get('word_fate', {}))
        c.player_defending = combat_data.get('player_defending', False)
        c.snapshot_stolen = combat_data.get('snapshot_stolen', False)
        c.stolen_word = combat_data.get('stolen_word', None)
        c.compliance_declarations = combat_data.get('compliance_declarations', 0)
        c.silence_bonus = combat_data.get('silence_bonus', False)
        c.silence_turns = combat_data.get('silence_turns', 0)
        if '_conv_questions_asked' in combat_data:
            c._conv_questions_asked = combat_data['_conv_questions_asked']
        if '_defend_streak' in combat_data:
            c._defend_streak = combat_data['_defend_streak']
        w.combat = c


# ── 状态栏 ──────────────────────────────────────────
def _status_bar(w):
    """紧凑JSON状态栏——让AI知道在哪。"""
    phase_names = {
        "init": "开始", "creation": "创建角色", "town": "镇上",
        "explore": "探索", "combat": "战斗", "fork": "分叉路",
        "dead": "死亡", "dead_who": "死后问答", "dead_wipe": "存档选择",
        "void": "虚空", "judgment": "审问", "ending": "结局",
    }
    bar = {
        "phase": phase_names.get(w.phase, w.phase),
        "area": w.area or "",
        "run": w.runs,
    }
    if w.phase not in ("init", "creation", "ending"):
        bar["hp"] = f"{w.hp}/{w.max_hp}"
        bar["mp"] = f"{w.mp}/{w.max_mp}"
        bar["compliance"] = w.compliance
        bar["hunger"] = w.hunger
        if w.gold > 0:
            bar["gold"] = w.gold
        if w.her_presence > 0:
            bar["her"] = w.her_presence
        if w.words:
            bar["words"] = w.words
        bar["r_flags"] = w.r_flags
    if w.phase == "combat" and w.combat:
        e = w.combat.enemy
        bar["enemy"] = {"name": e.get('name', '?'), "hp": e.get('hp', 0)}
    # 子状态——让AI知道卡在哪个交互里
    if getattr(w, 'current_sage', None):
        bar["sub"] = "sage"
        bar["sage"] = w.current_sage.get("name", "?")
    elif getattr(w, 'current_broken', None):
        bar["sub"] = "broken"
    elif getattr(w, 'current_special', None):
        bar["sub"] = "special"
        bar["special"] = w.current_special.get("name", "?")
    elif getattr(w, '_light_bearer_active', False):
        bar["sub"] = "light_bearer"
    elif getattr(w, '_crease_active', False):
        bar["sub"] = "crease"
    elif w.phase == "fork":
        bar["sub"] = "fork"
    elif w.phase == "dead_who":
        bar["sub"] = "dead_who"
    elif w.phase == "dead_wipe":
        bar["sub"] = "dead_wipe"
    elif w.phase == "judgment":
        bar["sub"] = "judgment"
        bar["judgment_step"] = getattr(w, '_judgment_step', 0)
    return json.dumps(bar, ensure_ascii=False, separators=(',', ':'))


# ── 核心接口 ────────────────────────────────────────
_initialized = False

def _ensure_init():
    global _initialized
    if not _initialized:
        sys.path.insert(0, _HERE)
        _patch_random()
        _initialized = True

def new_game(seed=None):
    """开新局。返回 (state_dict, 开场文字)。"""
    _ensure_init()
    from dark_engine import DarkWorld
    if seed is not None:
        _det_rng.seed(seed)
    w = DarkWorld()
    text = w.cmd("帮助")
    # 跳过_load——新局不要旧存档
    w.echoes = 0
    w.runs = 0
    w.phase = "init"
    state = _snapshot(w)
    return state, text

def cmd(state, instruction):
    """执行指令。返回 (新state, 输出文字)。

    支持批量:
      "前进5"     → 连走5步，汇总
      "攻3"       → 连攻3次，汇总
      "前进;说 我在;前进" → 分号串联依次执行
    """
    _ensure_init()
    from dark_engine import DarkWorld

    # 恢复世界
    w = DarkWorld()
    _restore(w, state)

    # 处理分号串联
    if ';' in instruction:
        parts = [p.strip() for p in instruction.split(';') if p.strip()]
        texts = []
        for part in parts:
            w, t = _exec_single(w, part)
            texts.append(t)
            if w.phase == "ending":
                break
        full_text = "\n---\n".join(texts)
    else:
        # 处理批量指令
        batch = _parse_batch(instruction)
        if batch:
            cmd_base, count = batch
            texts = []
            for i in range(count):
                w, t = _exec_single(w, cmd_base)
                texts.append(t)
                if w.phase == "ending":
                    break
                # 战斗中死亡就停
                if w.phase in ("dead", "dead_who", "dead_wipe", "void"):
                    break
            if count > 3:
                # 多步汇总：只显示首尾和状态变化
                full_text = texts[0] if texts else ""
                if len(texts) > 2:
                    full_text += f"\n...（省略{len(texts)-2}步）..."
                if len(texts) > 1:
                    full_text += "\n" + texts[-1]
            else:
                full_text = "\n".join(texts)
        else:
            w, full_text = _exec_single(w, instruction)

    # 持久化跨局数据
    w._save_meta()

    # 输出：游戏内容 + 状态栏
    new_state = _snapshot(w)
    status = _status_bar(w)
    output = full_text + "\n" + status
    return new_state, output

def _parse_batch(inst):
    """解析批量指令。返回 (基础指令, 次数) 或 None。"""
    batchable = ["前进", "攻", "防", "术"]
    for base in batchable:
        if inst.startswith(base):
            rest = inst[len(base):].strip()
            if rest.isdigit():
                count = int(rest)
                return base, min(count, 20)  # 上限20步防死循环
    return None

def _exec_single(w, instruction):
    """执行单条指令。返回 (world, text)。"""
    instruction = instruction.strip()
    if not instruction:
        return w, "?"

    # 战斗中自动推进——如果AI发来的是非战斗指令，提示
    if w.phase == "combat":
        combat_cmds = ("攻", "防", "术", "逃", "说", "物", "状态")
        if not any(instruction.startswith(c) for c in combat_cmds):
            text = w.cmd(instruction)  # 还是执行，让引擎自己处理
        else:
            text = w.cmd(instruction)
    else:
        text = w.cmd(instruction)

    return w, text

def load_game():
    """从文件读存档。返回 state_dict 或 None。"""
    if not os.path.exists(_SAVE_FILE):
        return None
    try:
        with open(_SAVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None

def save_game(state):
    """存档到文件。"""
    try:
        with open(_SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(',', ':'))
    except:
        pass


# ── 命令行入口 ──────────────────────────────────────
def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("用法: python engine.py \"指令\"")
        print("  python engine.py new          — 开新局")
        print("  python engine.py 前进          — 继续游戏")
        print("  python engine.py 前进5         — 连走5步")
        print("  python engine.py 前进;说 我在   — 串联指令")
        return

    instruction = " ".join(sys.argv[1:]).strip()

    if instruction.lower() in ("new", "新局", "new_game"):
        state, text = new_game()
        save_game(state)
        print(text)
        return

    # 读存档
    state = load_game()
    if state is None:
        state, text = new_game()
        save_game(state)
        print(text)
        print("\n（自动开新局。输入 python engine.py \"新角\" 开始。）")
        return

    # 执行
    new_state, text = cmd(state, instruction)
    save_game(new_state)
    print(text)

if __name__ == "__main__":
    main()
