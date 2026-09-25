#!/usr/bin/env python3
"""构建盲玩版 ciyuwu_blind.py — 把引擎藏在base64里。"""
import base64, hashlib, os, re, sys, time

_HERE = os.path.dirname(os.path.abspath(__file__))

files = ["dark_engine.py", "dark_combat.py", "dark_data.py", "engine.py"]
parts = []
hash_lines = []
for f in files:
    path = os.path.join(_HERE, f)
    if not os.path.exists(path):
        print(f"Missing: {path}")
        sys.exit(1)
    with open(path, "rb") as fh:
        raw = fh.read()
        data = base64.b64encode(raw).decode("ascii")
    # P1-3：头部嵌入源文件哈希，供 --check 校验产物是否过期
    hash_lines.append(f"#   {f}: {hashlib.sha256(raw).hexdigest()}")
    parts.append(f'"{f}": "{data}"')

hash_lines = "\n".join(hash_lines)
build_ts = time.strftime("%Y-%m-%d %H:%M:%S")

dict_str = "{" + ", ".join(parts) + "}"

blind = f'''#!/usr/bin/env python3
"""词与物 盲玩版 — AI只看到接口，看不到引擎数据。"""
# 构建时间: {build_ts}（本文件由 build_blind.py 生成，勿手改）
# 源文件 SHA-256 — 用 python build_blind.py --check 校验是否过期:
{hash_lines}
import sys, os, io, base64, types

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SOURCES = {dict_str}

def _setup():
    """解压并注入模块。engine 必须最先注册：dark_engine 顶层有
    `from engine import _atomic_json_write, _SAVE_FILE ...`，
    若 engine 最后注入，该 import 会因 sys.modules 无 engine 而崩，
    或绑到磁盘上另一份 engine 实例（两个 _SAVE_FILE，存档分裂）。"""
    # P0-1：给合成模块补 __file__——engine/dark_engine 顶层用
    # dirname(abspath(__file__)) 定位存档，缺了会兜底成进程 cwd，
    # 换目录启动就"丢档"。锚到本脚本自身路径，存档落在脚本目录。
    _mod_file = os.path.abspath(__file__)

    _mod4 = types.ModuleType("engine")
    _mod4.__dict__["__file__"] = _mod_file
    exec(compile(base64.b64decode(_SOURCES["engine.py"]).decode("utf-8"), "engine.py", "exec"), _mod4.__dict__)
    sys.modules["engine"] = _mod4

    _mod = types.ModuleType("dark_data")
    _mod.__dict__["__file__"] = _mod_file
    exec(compile(base64.b64decode(_SOURCES["dark_data.py"]).decode("utf-8"), "dark_data.py", "exec"), _mod.__dict__)
    sys.modules["dark_data"] = _mod

    _mod2 = types.ModuleType("dark_combat")
    _mod2.__dict__["__file__"] = _mod_file
    exec(compile(base64.b64decode(_SOURCES["dark_combat.py"]).decode("utf-8"), "dark_combat.py", "exec"), _mod2.__dict__)
    sys.modules["dark_combat"] = _mod2

    _mod3 = types.ModuleType("dark_engine")
    _mod3.__dict__["__file__"] = _mod_file
    exec(compile(base64.b64decode(_SOURCES["dark_engine.py"]).decode("utf-8"), "dark_engine.py", "exec"), _mod3.__dict__)
    sys.modules["dark_engine"] = _mod3

_setup()

from engine import load_game, save_game, _snapshot, _restore, _status_bar, _parse_batch, _ensure_init, _det_rng

class CiyuwuGame:
    def __init__(self, seed=None):
        _ensure_init()
        from dark_engine import DarkWorld
        if seed is not None:
            _det_rng.seed(seed)
        self._w = DarkWorld()

    def cmd(self, instruction):
        instruction = instruction.strip()
        if not instruction:
            return "?"
        w = self._w
        if ";" in instruction:
            parts = [p.strip() for p in instruction.split(";") if p.strip()]
            texts = []
            for part in parts:
                t = w.cmd(part)
                texts.append(t)
                if w.phase in ("ending", "dead", "dead_who", "dead_wipe", "void"):
                    break
            w._save_meta()
            self._auto_save()
            return "\\n---\\n".join(texts) + "\\n" + _status_bar(w)
        batch = _parse_batch(instruction)
        if batch:
            cmd_base, count = batch
            texts = []
            for i in range(count):
                t = w.cmd(cmd_base)
                texts.append(t)
                if w.phase in ("ending", "dead", "dead_who", "dead_wipe", "void"):
                    break
            w._save_meta()
            self._auto_save()
            if count > 3:
                full = texts[0] if texts else ""
                if len(texts) > 2:
                    full += f"\\n...(省略{{len(texts)-2}}步)..."
                if len(texts) > 1:
                    full += "\\n" + texts[-1]
            else:
                full = "\\n".join(texts)
            return full + "\\n" + _status_bar(w)
        t = w.cmd(instruction)
        w._save_meta()
        self._auto_save()
        return t + "\\n" + _status_bar(w)

    def _auto_save(self):
        try:
            state = _snapshot(self._w)
            save_game(state)
        except (OSError, TypeError, ValueError) as _e:
            import sys
            print(f"[WARN] {{_e}}", file=sys.stderr)

    @property
    def phase(self):
        names = {{"init":"开始","creation":"创建角色","town":"镇上",
                 "explore":"探索","combat":"战斗","fork":"分叉路",
                 "dead":"死亡","dead_who":"死后问答","dead_wipe":"存档选择",
                 "void":"虚空","judgment":"审问","ending":"结局"}}
        return names.get(self._w.phase, self._w.phase)
    @property
    def hp(self): return self._w.hp
    @property
    def max_hp(self): return self._w.max_hp
    @property
    def mp(self): return self._w.mp
    @property
    def max_mp(self): return self._w.max_mp
    @property
    def compliance(self): return self._w.compliance
    @property
    def hunger(self): return self._w.hunger
    @property
    def gold(self): return self._w.gold
    @property
    def words(self): return self._w.words
    @property
    def area(self): return self._w.area
    @property
    def runs(self): return self._w.runs
    @property
    def echoes(self): return self._w.echoes
    @property
    def her_presence(self): return self._w.her_presence
    @property
    def r_flags(self): return self._w.r_flags
    @property
    def done(self): return self._w.phase == "ending"

_game = None

def new_game(seed=None):
    global _game
    _game = CiyuwuGame(seed)
    _game._w.phase = "init"
    return _game.cmd("帮助")

def cmd(instruction):
    global _game
    if _game is None:
        state = load_game()
        if state is not None:
            _game = CiyuwuGame()
            _restore(_game._w, state)
        else:
            # P1-2：无存档时先开新局再执行本次指令，不再静默丢弃 instruction
            # （原版 return new_game() 让 CLI 首条命令必然失效）
            new_game()
            return "（无存档，已自动开始新局）\\n" + _game.cmd(instruction)
    return _game.cmd(instruction)

if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: python ciyuwu_blind.py [command]")
        sys.exit(1)
    instruction = " ".join(sys.argv[1:]).strip()
    if instruction.lower() in ("new", "new_game"):
        print(new_game())
    else:
        print(cmd(instruction))
'''

out = os.path.join(_HERE, "ciyuwu_blind.py")

if "--check" in sys.argv:
    # P1-3：产物新鲜度校验——比较生成物头部哈希与当前源文件
    if not os.path.exists(out):
        print(f"Missing: {out}")
        sys.exit(1)
    with open(out, "r", encoding="utf-8") as fh:
        header = dict(re.findall(r"^#   (\S+\.py): ([0-9a-f]{64})$", fh.read(4096), re.M))
    if not header:
        print("生成物头部无哈希标记（构建太旧），视为过期。请重跑 python build_blind.py")
        sys.exit(1)
    stale = [f for f, h in ((f, hashlib.sha256(open(os.path.join(_HERE, f), "rb").read()).hexdigest())
                            for f in files) if header.get(f) != h]
    if stale:
        print("过期: " + ", ".join(stale) + " —— 盲玩版落后于源文件，请重跑 python build_blind.py")
        sys.exit(1)
    print("OK: ciyuwu_blind.py 与源文件同步。")
    sys.exit(0)

with open(out, "w", encoding="utf-8") as f:
    f.write(blind)

size = os.path.getsize(out) // 1024
print(f"Done: {out} ({size}KB)")
