#!/usr/bin/env python3
"""词与物 — Operit兼容接口

用法（跟菜场游戏一样）：
    from ciyuwu import CiyuwuGame, new_game, cmd

    # 类方式
    g = CiyuwuGame()
    print(g.cmd("新角"))
    print(g.cmd("确认"))
    print(g.phase)       # "镇上"
    print(g.words)       # ["痛","怕","感觉","不要"]
    print(g.hp)          # 42

    # 全局函数方式
    print(new_game())    # 开局，返回文字
    print(cmd("前进"))   # 执行指令，返回文字
    print(cmd("前进5"))  # 批量
    print(cmd("前进;说 我在;前进"))  # 串联

    # 命令行
    python ciyuwu.py new
    python ciyuwu.py "前进"
    python ciyuwu.py "前进5"
"""
import sys, os, time

# P1-12：reconfigure 就地改编码，不再新建 TextIOWrapper——新旧包装共享同一
# buffer，宿主替换 sys.stdout 后旧包装被回收会连带关闭底层 buffer；
# stdout 无 reconfigure（被宿主换成 StringIO 等）时跳过，不再 AttributeError
_reconfigure = getattr(sys.stdout, "reconfigure", None)
if _reconfigure is not None and (getattr(sys.stdout, "encoding", "") or "").lower() != "utf-8":
    _reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from engine import load_game, save_game, _snapshot, _restore, _status_bar, _parse_batch, _ensure_init, _det_rng, _exec_with_batch, _MAX_PARTS, _BATCH_MAX, LOAD_CORRUPT


class CiyuwuGame:
    """词与物游戏——Operit兼容接口。"""

    def __init__(self, seed=None):
        _ensure_init()
        from dark_engine import DarkWorld
        if seed is not None:
            _det_rng.seed(seed)
        else:
            # C-4 同款：无 seed 也重置随机流，不继承上一局残留
            _det_rng.seed(int(time.time() * 1000))
        self._w = DarkWorld()
        if seed is not None:
            # ISSUE-2 同款：_load 恢复的生涯局数会让 _start_creation 跳过
            # runs*7 个随机数，破坏"同 seed 同指令=同结果"，指定 seed 时归零
            self._w.runs = 0

    def cmd(self, instruction):
        """执行指令，返回文字。"""
        instruction = instruction.strip()
        if not instruction:
            return "？"

        w = self._w
        notes = []

        # 分号串联
        # P1-6：每个 part 走 _exec_with_batch——原来直接 w.cmd(part)，
        # "前进5" 的数量后缀在子批量里静默失效（engine.cmd 的 BUG-12 同款）；
        # P0-2：终止集合对齐 engine.cmd——原来只在 ending 停，死亡/虚空后
        # 残留指令会被误当死后问答/存档选择执行
        if ';' in instruction:
            raw_parts = [p.strip() for p in instruction.split(';') if p.strip()]
            parts = raw_parts[:_MAX_PARTS]
            if len(raw_parts) > _MAX_PARTS:
                notes.append(f"?已截断：丢弃{len(raw_parts) - _MAX_PARTS}条超限指令（串联上限{_MAX_PARTS}）")
            texts = []
            for part in parts:
                w, t = _exec_with_batch(w, part)
                texts.append(t)
                if w.phase in ("dead", "dead_who", "dead_wipe", "void", "town",
                               "judgment", "creation", "init", "ending"):
                    break
            w._save_meta()
            self._auto_save()
            body = "\n---\n".join(texts)
            if notes:
                body = "\n".join(notes) + "\n" + body
            return body + "\n" + _status_bar(w)

        # 批量
        # P1-7：count<1 明确拒绝（_parse_batch 返回 (base,0) 就是为此），
        # 截断上限复用 _BATCH_MAX 并像 engine 一样声明截断；
        # P1-8：终止集合补齐 town/combat/judgment/creation/init，
        # 对齐 engine._exec_with_batch "遇战斗/交互暂停" 的既定语义
        batch = _parse_batch(instruction)
        if batch is not None:
            cmd_base, count = batch
            if count < 1:
                w._save_meta()
                return f"?无效次数，拒绝执行: {instruction}\n" + _status_bar(w)
            rest = instruction[len(cmd_base):].strip()
            if rest.isdigit() and int(rest) > count:
                notes.append(f"?次数超限，已截断为{count}次（上限{_BATCH_MAX}）")
            texts = []
            for i in range(count):
                t = w.cmd(cmd_base)
                texts.append(t)
                if w.phase in ("ending", "dead", "dead_who", "dead_wipe", "void",
                               "town", "judgment", "creation", "init", "combat"):
                    break
            w._save_meta()
            self._auto_save()
            # P1-11：按实际执行步数汇总——提前停下时，中间真实发生的步骤
            # 不许被"省略"藏掉（原来按请求次数 count 判断，5 步停在第 3 步
            # 时会隐藏第 2 步）
            if len(texts) > 3:
                full = texts[0] + f"\n...（省略{len(texts)-2}步）...\n" + texts[-1]
            else:
                full = "\n".join(texts)
            if notes:
                full = "\n".join(notes) + "\n" + full
            return full + "\n" + _status_bar(w)

        # 单条
        t = w.cmd(instruction)
        w._save_meta()
        self._auto_save()
        return t + "\n" + _status_bar(w)

    def _auto_save(self):
        try:
            state = _snapshot(self._w)
            save_game(state)
            return True
        except (OSError, TypeError, ValueError) as e:
            print(f"[WARN] 自动存档失败: {e}", file=sys.stderr)
            return False

    # ── 可读属性 ──
    @property
    def phase(self):
        names = {"init":"开始","creation":"创建角色","town":"镇上",
                 "explore":"探索","combat":"战斗","fork":"分叉路",
                 "dead":"死亡","dead_who":"死后问答","dead_wipe":"存档选择",
                 "void":"虚空","judgment":"审问","ending":"结局"}
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


# ── 全局函数 ──

_game = None

def new_game(seed=None):
    """开新局，返回开场文字。"""
    global _game
    _game = CiyuwuGame(seed)
    _game._w.phase = "init"
    return _game.cmd("帮助")

def cmd(instruction):
    """执行指令，返回文字。"""
    global _game
    if _game is None:
        state = load_game()
        if state is LOAD_CORRUPT:
            # P1-9：三态契约——损坏≠无档。损坏档已备份到 *.corrupt，
            # 不能当有效档恢复（会在全新世界上继续跑并覆盖损坏档）
            return "存档损坏（已备份到 ciyuwu_save.json.corrupt）。指令未执行，已中止以避免覆盖。请先 new_game() 开新局。"
        if state is not None:
            _game = CiyuwuGame()
            _restore(_game._w, state)
        else:
            return "没有存档，指令未执行。请先调用 new_game() 开新局。"
    return _game.cmd(instruction)


# ── 命令行 ──

if __name__ == "__main__":
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("用法: python ciyuwu.py \"指令\"")
        print("  python ciyuwu.py new      — 开新局")
        print("  python ciyuwu.py 前进      — 继续")
        print("  python ciyuwu.py 前进5     — 连走5步")
        sys.exit(1)

    instruction = " ".join(sys.argv[1:]).strip()

    if instruction.lower() in ("new", "新局", "new_game"):
        print(new_game())
    else:
        print(cmd(instruction))
