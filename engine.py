#!/usr/bin/env python3
"""词与物 — AI可玩版引擎

接口:
  new_game()          → (state, text)   开新局
  cmd(state, inst)    → (state, text)   执行指令
  load_game()         → state|None|LOAD_CORRUPT  从文件读（损坏≠无档）
  save_game(state)    → bool            存文件（True=成功）

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
import sys, os, io, json, time, traceback

# 确保UTF-8输出
if (sys.stdout.encoding or '').lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

try:
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _HERE = os.getcwd()
_SAVE_FILE = os.path.join(_HERE, "ciyuwu_save.json")

# ST-2 修复：进程内线程锁——防止多线程并发写 .tmp 文件（Windows 上 WinError 32）
import threading as _threading
_atomic_write_lock = _threading.Lock()

def _atomic_json_write_unlocked(path, data):
    """无锁版原子写——供已持 _atomic_write_lock 的调用方复用（P0-10：
    读-合并-写必须整体在一个临界区内完成，持锁后再进带锁版本会自死锁）。
    临时名带 pid+线程id：进程内锁挡不住多进程，同名 .tmp 会被互相截断。"""
    tmp = f"{path}.{os.getpid()}.{_threading.get_ident()}.tmp"
    ok = False
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)
        ok = True
    except Exception as e:
        print(f"[WARN] _atomic_json_write 失败 {path}: {e}", file=sys.stderr)
    finally:
        # F-3 修复：清理残留 .tmp（rename 成功后 tmp 已不存在；这里只处理失败情况）
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception as _e:
            print(f"[WARN] {_e}", file=sys.stderr); traceback.print_exc(file=sys.stderr)
    return ok

def _atomic_json_write(path, data):
    """原子 JSON 写入：写 .tmp 后 rename。失败时清理残留 .tmp 文件。
    F-3 修复：原版 except: pass 会留下 .tmp 永久残留，现在用 finally 清理。
    ST-2 修复：进程内线程锁防止并发 .tmp 冲突。
    返回 True/False 显式状态（ISSUE-4）；allow_nan=False 禁写非法 JSON（ISSUE-15）。
    """
    with _atomic_write_lock:
        return _atomic_json_write_unlocked(path, data)

# ── 确定性PRNG ──────────────────────────────────────
def _mulberry32(seed):
    """确定性随机数生成器。同seed=同序列。"""
    def _gen():
        nonlocal seed
        while True:
            seed = (seed + 0x6D2B79F5) & 0xFFFFFFFF
            t = seed
            t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
            t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
            yield (t ^ (t >> 15)) & 0xFFFFFFFF
    return _gen()

class _DetRandom:
    """替换random的确定性随机。S2-1修复：加threading.Lock保护内部状态。
    只在最底层 _next_raw() 加锁，上层方法通过 _next_raw() 间接获取随机数，
    避免 Lock 不可重入导致死锁。
    ISSUE-2 修复：_state 保存完整生成器状态（内部seed，随每次抽取更新），
    而非只存初始 seed——snapshot/restore 往返该值即可精确续跑，不再把序列拨回开头。"""
    def __init__(self, seed=42):
        self._lock = _threading.Lock()
        self._state = seed & 0xFFFFFFFF
        self._gen = _mulberry32(self._state)

    def _next_raw(self):
        """底层：线程安全地获取下一个原始随机整数。"""
        with self._lock:
            val = next(self._gen)
            # 与 _mulberry32 内部 seed 同步（每步先 +0x6D2B79F5）
            self._state = (self._state + 0x6D2B79F5) & 0xFFFFFFFF
            return val

    def seed(self, s):
        with self._lock:
            self._state = s & 0xFFFFFFFF
            self._gen = _mulberry32(self._state)

    def random(self):
        return self._next_raw() / 0xFFFFFFFF

    def randint(self, a, b):
        # ISSUE-8 修复：random()==1.0 时会算出 b+1 越界，钳到 b
        return min(a + int(self.random() * (b - a + 1)), b)

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
                if r < cum:
                    result.append(item)
                    break
            else:
                result.append(population[-1])
        return result


# ── 注入确定性随机到dark_engine ─────────────────────
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


# 跨局 meta 字段名与 DarkWorld._save_meta/_load 统一（ISSUE-7）
_META_KEYS = ("echoes", "runs", "echo_map", "killed_bosses",
              "unlocked_origins", "wall_writings", "total_wait",
              "unlocked_achievements", "heart_slots",
              "cross_word_stats", "game_diary",
              "cross_deform_count", "cross_swallow_count",
              "tavern_regular_visits")
# meta 键名 → DarkWorld 实例属性名（酒馆常客计数在实例上带下划线前缀）
_META_ATTRS = {"tavern_regular_visits": "_tavern_regular_visits"}


def _merge_save_meta(self):
    """ISSUE-3 修复：meta 写入并入已有全量快照——只更新 _META_KEYS，
    其余键原样保留。禁止用 14 个 meta 键覆盖 save_game() 写入的全量快照。
    由 _ensure_init 挂到 DarkWorld._save_meta，覆盖其全部调用点。
    P0-10 修复：读-合并-写整体持 _atomic_write_lock——原来只有写入阶段
    被锁住，两线程交错时后写者会用旧基线的合并结果整文件覆盖先写者的进度。"""
    data = {}
    for k in _META_KEYS:
        attr = _META_ATTRS.get(k, k)
        if hasattr(self, attr):
            data[k] = getattr(self, attr)
    with _atomic_write_lock:
        existing = {}
        if os.path.exists(_SAVE_FILE):
            try:
                with open(_SAVE_FILE, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, dict):
                    existing = {}
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError,
                    TypeError, IOError, OSError) as e:
                # 旧档损坏：先备份再写 meta，不静默丢弃
                backup = _SAVE_FILE + ".corrupt"
                try:
                    os.replace(_SAVE_FILE, backup)
                    print(f"[WARN] meta合并前存档损坏，已备份到 {backup}: {e}", file=sys.stderr)
                except OSError as _e:
                    print(f"[WARN] meta合并前存档损坏且备份失败: {_e}", file=sys.stderr)
                existing = {}
        old_runs = existing.get("runs") if isinstance(existing, dict) else None
        existing.update(data)
        # runs 取 max：指定 seed 的可复现局会把局数归零（见 new_game），不能因此丢生涯计数
        if isinstance(old_runs, int) and isinstance(data.get("runs"), int):
            existing["runs"] = max(old_runs, data["runs"])
        return _atomic_json_write_unlocked(_SAVE_FILE, existing)


# ── 快照 ────────────────────────────────────────────
_SKIP_ATTRS = {'combat'}

# F-4/ST-1 修复：存档属性白名单动态构建——从 DarkWorld() 实例收集所有
# 在 __init__ 中设置的属性。防止漏字段（如 _go_town、_devil_self_harm_mult 等）。
# ISSUE-1/13 修复：白名单延迟到 _ensure_init 再构建——import 期执行
# `from dark_engine import DarkWorld` 会与 dark_engine 的 `from engine import ...`
# 形成循环导入（特定导入顺序崩），且 DarkWorld() 构造自带读档 IO 副作用。
_RESTORE_WHITELIST = None

def _build_whitelist():
    """运行时构造白名单：new_game 一次，收集所有非方法、非内置属性。"""
    from dark_engine import DarkWorld
    w = DarkWorld()
    attrs = set()
    for attr in dir(w):
        if attr.startswith('__') or attr in _SKIP_ATTRS:
            continue
        val = getattr(w, attr, None)
        if callable(val):
            continue
        attrs.add(attr)
    return frozenset(attrs)

def _ensure_whitelist():
    """延迟构建白名单（进程内一次）。"""
    global _RESTORE_WHITELIST
    if _RESTORE_WHITELIST is None:
        _RESTORE_WHITELIST = _build_whitelist()
    return _RESTORE_WHITELIST

def _to_jsonable(obj, _seen=None):
    if _seen is None:
        _seen = set()
    # BUG-25/27 修复：循环引用防护只针对"容器/自定义对象"，
    # 不能用 id(int) 因为 CPython 小整数缓存让 id(0)==id(0)
    if isinstance(obj, set):
        return {'__t': 'set', 'v': [_to_jsonable(x, _seen) for x in sorted(obj, key=str)]}
    if isinstance(obj, dict):
        # dict 本身可递归引用，用 (id, type) 跟踪
        key = (id(obj), type(obj))
        if key in _seen:
            return {'__t': 'ref', 'id': id(obj)}
        _seen.add(key)
        result = {k: _to_jsonable(v, _seen) for k, v in obj.items()}
        # P1-38：递归返回后回溯删除——_seen 只该含"当前递归路径上的祖先"。
        # 不删会把同一容器在本属性内的第二次出现（浅拷贝构造的共享引用）
        # 误判成循环，还原成 None 丢数据
        _seen.discard(key)
        return result
    if isinstance(obj, (list, tuple)):
        key = (id(obj), type(obj))
        if key in _seen:
            return {'__t': 'ref', 'id': id(obj)}
        _seen.add(key)
        result = [_to_jsonable(x, _seen) for x in obj]
        _seen.discard(key)
        return result
    # BUG-19 修复：dataclass 等自定义对象走 __dict__ 兜底，避免静默丢失
    if hasattr(obj, '__dict__') and not isinstance(obj, type):
        key = (id(obj), type(obj))
        if key in _seen:
            return {'__t': 'ref', 'id': id(obj)}
        _seen.add(key)
        result = {'__t': 'obj', 'cls': type(obj).__name__,
                  'v': _to_jsonable(obj.__dict__, _seen)}
        _seen.discard(key)
        return result
    return obj

# P0-9：快照占位标记的还原哨兵。'__t':'obj'/'unserializable' 的原始对象
# 无法从 dict 重建，返回它让 _restore 跳过并告警，不再把占位 dict 当合法值写入
_UNRESTORABLE = object()

def _from_jsonable(obj):
    if isinstance(obj, dict):
        if obj.get('__t') == 'set':
            return set(_from_jsonable(x) for x in obj.get('v', []))
        if obj.get('__t') == 'obj':
            # BUG-19 的占位只有排查价值，没有还原价值（P0-9）
            return _UNRESTORABLE
        if obj.get('__t') == 'ref':
            return None  # 循环引用回退——上层自行处理
        if obj.get('__t') == 'unserializable':
            return _UNRESTORABLE
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
        except Exception as e:
            # BUG-19 修复：不再静默吞错，记录占位以便排查
            state[attr] = {'__t': 'unserializable', 'type': type(val).__name__,
                           'err': str(e)[:200]}
            import sys
            print(f"[WARN] _snapshot: 跳过 {attr}: {e}", file=sys.stderr)
    # combat单独处理
    if w.combat:
        c = w.combat
        state['_combat'] = {
            'player': _to_jsonable(c.player),
            'enemy': _to_jsonable(c.enemy),
            'turn': c.turn,
            'log': getattr(c, 'log', []),  # BUG-20 修复：补 log 字段
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
            '_last_player_dmg': getattr(c, '_last_player_dmg', 0),  # BUG-20
            '_her_echo_spent': getattr(c, '_her_echo_spent', False),  # BUG-20
            '_see_deformation': getattr(c, '_see_deformation', False),  # BUG-20
            '_conv_questions_asked': getattr(c, '_conv_questions_asked', 0),
            '_defend_streak': getattr(c, '_defend_streak', 0),
        }
    # PRNG状态（完整生成器状态，见 _DetRandom._state）
    state['_rng_state'] = _det_rng._state
    return state

def _restore(w, state):
    """恢复快照。F-4 修复：只恢复白名单内的属性，避免恶意/损坏存档注入。
    ISSUE-6 修复：不修改入参 state——拷贝后再 pop。"""
    if not isinstance(state, dict):
        import sys
        print(f"[WARN] _restore: state 不是 dict（{type(state).__name__}），跳过", file=sys.stderr)
        return
    state = dict(state)  # ISSUE-6：拷贝，避免 pop 隐式修改调用方对象
    rng_state = state.pop('_rng_state', None)
    if rng_state is not None and isinstance(rng_state, int):
        _det_rng.seed(rng_state)

    combat_data = state.pop('_combat', None)
    whitelist = _ensure_whitelist()
    rejected = []
    for attr, val in state.items():
        if attr in _SKIP_ATTRS:
            continue
        # F-4 修复：白名单 + 基础类型校验
        if attr not in whitelist:
            rejected.append(attr)
            continue
        try:
            restored = _from_jsonable(val)
        except Exception as e:
            import sys
            print(f"[WARN] _restore 跳过 {attr}: {e}", file=sys.stderr)
            rejected.append(attr)
            continue
        if restored is _UNRESTORABLE:
            # P0-9：占位标记不能当合法值 setattr——走 rejected 通道留痕
            import sys
            print(f"[WARN] _restore: {attr} 含不可还原对象，跳过该字段", file=sys.stderr)
            rejected.append(attr)
            continue
        try:
            setattr(w, attr, restored)
        except Exception as e:
            import sys
            print(f"[WARN] _restore 跳过 {attr}: {e}", file=sys.stderr)
            rejected.append(attr)
    if rejected:
        # P1-40：拒绝时打印具体字段名——只报数量时，丢什么字段无从排查
        import sys
        print(f"[WARN] _restore 拒绝 {len(rejected)} 个字段: {', '.join(sorted(rejected))}", file=sys.stderr)
    # 恢复combat
    if combat_data:
        from dark_combat import CombatState
        # ISSUE-12 修复：.get + 明确错误，缺 player/enemy 不再直接下标崩溃
        player = combat_data.get('player')
        enemy = combat_data.get('enemy')
        if player is None or enemy is None:
            import sys
            print("[WARN] _restore: _combat 缺少 player/enemy 键，跳过战斗恢复", file=sys.stderr)
        else:
            player = _from_jsonable(player)
            enemy = _from_jsonable(enemy)
            c = CombatState(player, enemy, combat_data.get('layer', '灰林'))
            c.turn = combat_data.get('turn', 0)
            c.log = combat_data.get('log', [])  # BUG-20 修复：补 log 字段
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
            # BUG-20 修复：补全其余 _private 字段
            c._last_player_dmg = combat_data.get('_last_player_dmg', 0)
            c._her_echo_spent = combat_data.get('_her_echo_spent', False)
            c._see_deformation = combat_data.get('_see_deformation', False)
            if '_conv_questions_asked' in combat_data:
                c._conv_questions_asked = combat_data['_conv_questions_asked']
            if '_defend_streak' in combat_data:
                c._defend_streak = combat_data['_defend_streak']
            w.combat = c


# ── 状态栏 ──────────────────────────────────────────
# S3说明：此函数输出JSON格式。ciyuwu_server._status_from_state()输出竖线分隔格式。
# 两种格式并存，AI客户端需同时支持。长期建议统一为JSON。
# ISSUE-16 修复：phase 中文名提为常量，避免散落字符串
_PHASE_NAMES = {
    "init": "开始", "creation": "创建角色", "town": "镇上",
    "explore": "探索", "combat": "战斗", "fork": "分叉路",
    "dead": "死亡", "dead_who": "死后问答", "dead_wipe": "存档选择",
    "void": "虚空", "judgment": "审问", "ending": "结局",
}


class _StateView:
    """state dict 的属性视图——让 _status_bar 能在失败路径复用（ISSUE-11）。
    快照不含 combat（_SKIP_ATTRS）；缺失字段返回 None 不崩。"""
    def __init__(self, d):
        self.combat = None
        if isinstance(d, dict):
            self.__dict__.update(d)

    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            raise AttributeError(name)
        return None


def _fail(state, msg, w=None):
    """ISSUE-11：统一失败返回结构——与成功同构（文本+\\n+状态栏JSON），
    状态栏带 error 字段以区分成功/失败。"""
    src = w if w is not None else _StateView(state)
    return state, f"{msg}\n" + _status_bar(src, error=msg)


def _status_bar(w, error=None):
    """紧凑JSON状态栏——让AI知道在哪。w 可为 DarkWorld 或 state dict。"""
    phase_names = _PHASE_NAMES
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
    # 死亡阶段优先：_handle_death 不清 current_special 等残留交互时，
    # sub 会被盖成 special，测试/AI 误判没进死亡流程
    if w.phase == "dead_who":
        bar["sub"] = "dead_who"
    elif w.phase == "dead_wipe":
        bar["sub"] = "dead_wipe"
    elif getattr(w, '_pending_pickup', None):
        bar["sub"] = "pickup"
        bar["pickup"] = w._pending_pickup.get("name", "?")
    elif getattr(w, '_square_sit', 0) > 0:
        bar["sub"] = "square"
        bar["square_sit"] = w._square_sit
    elif getattr(w, 'current_sage', None):
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
    elif w.phase == "judgment":
        bar["sub"] = "judgment"
        bar["judgment_step"] = getattr(w, '_judgment_step', 0)
    if error is not None:
        bar["error"] = error
    return json.dumps(bar, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


# ── 核心接口 ────────────────────────────────────────
_initialized = False

def _ensure_init():
    global _initialized
    if not _initialized:
        sys.path.insert(0, _HERE)
        _patch_random()
        # ISSUE-3：接管 meta 写入——并入全量快照，禁止覆盖 save_game 的全量档
        from dark_engine import DarkWorld
        DarkWorld._save_meta = _merge_save_meta
        # ISSUE-1/13：白名单延迟到这里构建（避开循环导入与 import 期 IO）
        _ensure_whitelist()
        _initialized = True

def new_game(seed=None):
    """开新局。返回 (state_dict, 开场文字)。"""
    _ensure_init()
    from dark_engine import DarkWorld
    if seed is not None:
        _det_rng.seed(seed)
    else:
        # C-4 修复：无 seed 时也重置 _det_rng，避免上次局残留污染
        # 用当前时间 + runs 数混合作为新种子
        meta_runs = 0
        if os.path.exists(_SAVE_FILE):
            try:
                with open(_SAVE_FILE, "r", encoding="utf-8") as f:
                    meta_runs = json.load(f).get("runs", 0)
            except Exception as _e:
                import sys; print(f"[WARN] {_e}", file=sys.stderr); traceback.print_exc(file=sys.stderr)
        _det_rng.seed(int(time.time() * 1000) + meta_runs)
    # 先读持久化的meta——新局也保留跨局进度
    meta = {}
    if os.path.exists(_SAVE_FILE):
        try:
            with open(_SAVE_FILE, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception as _e:
            import sys; print(f"[WARN] {_e}", file=sys.stderr); traceback.print_exc(file=sys.stderr)
    w = DarkWorld()
    text = w.cmd("帮助")
    # 恢复跨局meta——echoes/killed_bosses/achievements等不因新局重置
    # ISSUE-7 修复：字段与 _save_meta 统一（含 tavern_regular_visits），
    # meta 键名与实例属性名的差异走 _META_ATTRS 映射
    if meta:
        for k in _META_KEYS:
            if k in meta:
                setattr(w, _META_ATTRS.get(k, k), meta[k])
    if seed is not None:
        # ISSUE-2 关联：接口约定"同seed同指令=同结果"。meta 的 runs 会让
        # _start_creation 跳过 runs*7 个随机数，同 seed 的流程随存档漂移；
        # 指定 seed 时本局归零局数保证可复现（生涯局数由 _merge_save_meta 以
        # max 保留，不会因此丢档）
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

    # M-1 修复：输入验证——限长 + 去控制字符
    # ISSUE-11：失败与成功统一返回 (state, text+"\n"+状态栏)，状态栏带 error 字段
    if not isinstance(instruction, str):
        return _fail(state, "? 非字符串指令")
    instruction = instruction.strip()
    if not instruction:
        return _fail(state, "? 空指令")
    # 限长 200 字符（防止超长字符串拖慢正则）
    # ISSUE-9 修复：截断不再静默，输出中带标记
    truncated = False
    if len(instruction) > 200:
        instruction = instruction[:200]
        truncated = True
    # 去控制字符（保留 emoji/中文/标点）
    instruction = ''.join(c for c in instruction if c == '\t' or c == '\n' or
                          not (ord(c) < 32 and c not in '\t\n') and ord(c) != 127)
    if not instruction:
        return _fail(state, "? 无有效指令")

    # 恢复世界
    w = DarkWorld()
    _restore(w, state)

    notes = []  # 截断/拒绝标记（ISSUE-9/10/17）
    # 处理分号串联
    # BUG-12 修复：分号 + 批量混合——每个 part 内部也要识别批量（如 "前进5;说 我在"）
    if ';' in instruction:
        raw_parts = [p.strip() for p in instruction.split(';') if p.strip()]
        parts = raw_parts[:_MAX_PARTS]  # S3: 分号串联上限10
        if len(raw_parts) > _MAX_PARTS:
            # ISSUE-10 修复：声明截断，不再静默丢弃
            notes.append(f"?已截断：丢弃{len(raw_parts) - _MAX_PARTS}条超限指令（串联上限{_MAX_PARTS}）")
        # BUG-15 修复：移除从未引用的 prev_phase 死代码
        texts = []
        for part in parts:
            w, t = _exec_with_batch(w, part)
            texts.append(t)
            if w.phase == "ending":
                break
            # phase大变（战斗结束、回镇、死亡等）就停
            # BUG-3 修复：从 break 列表移除 "fork"——分叉下玩家可继续多步操作
            # （_auto_step 会自动决策左/右），不属于"大相位切换"
            if w.phase in ("dead", "dead_who", "dead_wipe", "void", "town",
                           "judgment", "creation", "init", "ending"):
                break
        full_text = "\n---\n".join(texts)
    else:
        w, full_text = _exec_with_batch(w, instruction)
    if truncated:
        notes.append("?指令超长，已截断至200字符")

    # 持久化跨局数据
    w._save_meta()

    # 输出：标记 + 游戏内容 + 状态栏（状态栏恒为最后一行）
    new_state = _snapshot(w)
    status = _status_bar(w)
    body = "\n".join(notes + [full_text]) if notes else full_text
    output = body + "\n" + status
    return new_state, output

# ISSUE-16 修复：批量指令前缀提为常量
_BATCHABLE = ("前进", "攻", "防", "术")
_MAX_PARTS = 10    # 分号串联上限
_BATCH_MAX = 20    # 批量次数上限


def _parse_batch(inst):
    """解析批量指令。返回 (基础指令, 次数) 或 None。
    ISSUE-10：次数上限 _BATCH_MAX=20（调用方按需声明截断）。
    ISSUE-17：count<1（如 "前进0"）返回 (base, 0) 而非 None——
    None 会让"前进0"原样下传给 w.cmd，这里改为让调用方明确拒绝。"""
    for base in _BATCHABLE:
        if inst.startswith(base):
            rest = inst[len(base):].strip()
            if rest.isdigit():
                count = int(rest)
                return base, min(count, _BATCH_MAX)
    return None

def _exec_single(w, instruction):
    """执行单条指令。返回 (world, text)。"""
    instruction = instruction.strip()
    if not instruction:
        return w, "?"

    # BUG-16 修复：原代码两个分支完全等价，移除无用的条件判断
    text = w.cmd(instruction)

    return w, text

def _exec_with_batch(w, instruction):
    """执行单条或批量指令（支持 "前进5"）。返回 (world, text)。"""
    batch = _parse_batch(instruction)
    if batch is not None:
        cmd_base, count = batch
        if count < 1:
            # ISSUE-17 修复：明确拒绝 count<1，不再把 "前进0" 原样下传
            return w, f"?无效次数，拒绝执行: {instruction}"
        note = ""
        rest = instruction[len(cmd_base):].strip()
        if rest.isdigit() and int(rest) > count:
            # ISSUE-10 修复：声明批量截断，不再静默丢弃
            note = f"?次数超限，已截断为{count}次（上限{_BATCH_MAX}）\n"
        texts = []
        for i in range(count):
            w, t = _exec_single(w, cmd_base)
            texts.append(t)
            if w.phase == "ending":
                break
            # 战斗中死亡/回镇/进交互就停
            # combat也停——批量前进遇战斗暂停，让玩家看清遭遇再决定（网友建议①）
            # BUG-3 修复：移除 "fork"——批量前进遇分叉可继续，_auto_step 自动选路
            if w.phase in ("dead", "dead_who", "dead_wipe", "void", "town",
                           "judgment", "creation", "init", "combat"):
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
        return w, note + full_text
    return _exec_single(w, instruction)

class _CorruptSave:
    """load_game 的损坏存档标记（ISSUE-5）。不是 None，避免上层把损坏当无档覆盖。"""
    __slots__ = ()
    def __repr__(self):
        return "LOAD_CORRUPT"


LOAD_CORRUPT = _CorruptSave()


def load_game():
    """从文件读存档。三态可区分（ISSUE-5）：
      state_dict   — 读取成功
      None         — 无存档文件
      LOAD_CORRUPT — 存档损坏/不可读（已备份到 *.corrupt），不得当无档覆盖
    """
    if not os.path.exists(_SAVE_FILE):
        return None
    try:
        with open(_SAVE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"存档根节点类型错误: {type(data).__name__}")
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError,
            TypeError, IOError, OSError) as e:
        # ISSUE-5 修复：捕获完整异常集（含 UnicodeDecodeError）；
        # 损坏时备份并返回可区分标记，不再与无存档混为 None
        backup = _SAVE_FILE + ".corrupt"
        try:
            os.replace(_SAVE_FILE, backup)
            print(f"[WARN] 存档损坏已备份到 {backup}: {e}", file=sys.stderr)
        except OSError as _e:
            print(f"[WARN] 存档损坏且备份失败: {_e}（原错误: {e}）", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return LOAD_CORRUPT


def save_game(state):
    """存档到文件（原子写）。返回 True/False 显式状态（ISSUE-4）。"""
    return _atomic_json_write(_SAVE_FILE, state)


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
        print(text)
        if not save_game(state):
            # P1-39：写盘失败必须让调用方知道，否则静默回档
            print("存档失败！本局进度未保存。")
            sys.exit(1)
        return

    # 读存档
    state = load_game()
    if state is LOAD_CORRUPT:
        # ISSUE-5：损坏≠无档——已备份，禁止自动开新局覆盖
        print("存档损坏（已备份到 ciyuwu_save.json.corrupt）。已中止，避免覆盖。")
        return
    if state is None:
        state, text = new_game()
        print(text)
        if not save_game(state):
            print("存档失败！本局进度未保存。")
            sys.exit(1)
        print("\n（自动开新局。输入 python engine.py \"新角\" 开始。）")
        return

    # 执行
    new_state, text = cmd(state, instruction)
    print(text)
    if not save_game(new_state):
        print("存档失败！本次进度未保存，下次将从上一个存档点继续。")
        sys.exit(1)

if __name__ == "__main__":
    main()
