#!/usr/bin/env python3
"""构建盲玩版 ciyuwu_blind.py — 把引擎藏在base64里。"""
import base64, os, sys

_HERE = os.path.dirname(os.path.abspath(__file__))

files = ["dark_engine.py", "dark_combat.py", "dark_data.py", "engine.py"]
parts = []
for f in files:
    path = os.path.join(_HERE, f)
    if not os.path.exists(path):
        print(f"Missing: {path}")
        sys.exit(1)
    with open(path, "rb") as fh:
        data = base64.b64encode(fh.read()).decode("ascii")
    parts.append(f'"{f}": "{data}"')

dict_str = "{" + ", ".join(parts) + "}"

blind = f'''#!/usr/bin/env python3
"""词与物 盲玩版 — AI只看到接口，看不到引擎数据。"""
import sys, os, io, json, base64, importlib, types

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_SOURCES = {dict_str}

def _setup():
    """解压并注入模块。"""
    _mod = types.ModuleType("dark_data")
    exec(compile(base64.b64decode(_SOURCES["dark_data.py"]).decode("utf-8"), "dark_data.py", "exec"), _mod.__dict__)
    sys.modules["dark_data"] = _mod

    _mod2 = types.ModuleType("dark_combat")
    exec(compile(base64.b64decode(_SOURCES["dark_combat.py"]).decode("utf-8"), "dark_combat.py", "exec"), _mod2.__dict__)
    sys.modules["dark_combat"] = _mod2

    _mod3 = types.ModuleType("dark_engine")
    exec(compile(base64.b64decode(_SOURCES["dark_engine.py"]).decode("utf-8"), "dark_engine.py", "exec"), _mod3.__dict__)
    sys.modules["dark_engine"] = _mod3

    _mod4 = types.ModuleType("engine")
    exec(compile(base64.b64decode(_SOURCES["engine.py"]).decode("utf-8"), "engine.py", "exec"), _mod4.__dict__)
    sys.modules["engine"] = _mod4

_setup()

from engine import new_game, cmd, load_game, save_game, _snapshot, _restore, _status_bar, _parse_batch, _ensure_init, _det_rng

_HERE = os.path.dirname(os.path.abspath(__file__))
_SAVE_FILE = os.path.join(_HERE, "ciyuwu_save.json")

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
                if w.phase == "ending":
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
        except:
            pass

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
            return new_game()
    return _game.cmd(instruction)

if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: python ciyuwu_blind.py [command]")
        sys.exit(0)
    instruction = " ".join(sys.argv[1:]).strip()
    if instruction.lower() in ("new", "new_game"):
        print(new_game())
    else:
        print(cmd(instruction))
'''

out = os.path.join(_HERE, "ciyuwu_blind.py")
with open(out, "w", encoding="utf-8") as f:
    f.write(blind)

size = os.path.getsize(out) // 1024
print(f"Done: {out} ({size}KB)")
