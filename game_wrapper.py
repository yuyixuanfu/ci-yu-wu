#!/usr/bin/env python3
"""词与物 — Operit入口"""
import sys, os, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dark_engine import DarkWorld

_HERE = os.path.dirname(os.path.abspath(__file__))

_SKIP_ATTRS = {
    'combat',  # CombatState不能直接JSON，手动处理
}

def _to_jsonable(obj):
    if isinstance(obj, set):
        return {'__type__': 'set', 'data': [_to_jsonable(x) for x in obj]}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    return obj

def _from_jsonable(obj):
    if isinstance(obj, dict):
        if obj.get('__type__') == 'set':
            return set(_from_jsonable(x) for x in obj.get('data', []))
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
        }
    return state

def _restore(w, state):
    """恢复快照。"""
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
        w.combat = c

def _auto_step(w):
    """自动决策一步——处理需要选择的状态。返回结果文字。"""
    # 战斗中：自动攻
    if w.phase == "combat":
        return w.cmd("攻")

    # 智者：选第1个选项（最保守）
    if w.current_sage is not None:
        return w.cmd("1")

    # 分叉：随机选
    if w.phase == "explore" and w.current_room_type == "fork":
        return w.cmd(random.choice(["左", "右"]))

    # 特别遭遇：选第1个
    if getattr(w, 'current_special', None) is not None:
        return w.cmd("1")

    # 轻负者：选"不了"
    if getattr(w, '_light_bearer_active', False):
        return w.cmd("2")

    # 残句：试着说第一个词
    if getattr(w, 'current_broken', None) is not None:
        if w.words:
            return w.cmd(f"说 {w.words[0]}")
        return w.cmd("前进")

    # 死后你是谁
    if w.phase == "dead_who":
        # 说当前词库最重的词
        if w.words:
            return w.cmd(f"说 {w.words[-1]}")
        return w.cmd("不知道")

    # 死后删存档选择
    if w.phase == "dead_wipe":
        return w.cmd("留")

    return None  # 不需要自动处理


def _state_header(w):
    """当前状态摘要——让AI知道在哪。"""
    parts = []
    # 阶段
    phase_names = {
        "init": "开始", "creation": "创建角色", "town": "镇上",
        "explore": "探索", "combat": "战斗", "fork": "分叉路",
        "dead": "死亡", "dead_who": "死后问答", "dead_wipe": "存档选择",
        "void": "虚空", "judgment": "审问", "ending": "结局",
    }
    phase = phase_names.get(w.phase, w.phase)
    parts.append(f"[{phase}")

    if w.area:
        parts[0] += f"·{w.area}"
    if w.runs > 0:
        parts[0] += f" 第{w.runs}局"
    parts[0] += "]"

    # 属性
    if w.phase not in ("init", "creation", "ending"):
        parts.append(f"HP:{w.hp}/{w.max_hp} MP:{w.mp}/{w.max_mp}")
        if w.compliance > 0:
            parts.append(f"静止度:{w.compliance}")
        parts.append(f"饿:{w.hunger}")
        if w.gold > 0:
            parts.append(f"金:{w.gold}")
        if w.her_presence > 0:
            parts.append(f"her:{w.her_presence}")

    # 词库
    if w.words:
        parts.append(f"词:{','.join(w.words)}")

    # 战斗额外
    if w.phase == "combat" and w.combat:
        e = w.combat.enemy
        parts.append(f"敌人:{e.get('name','?')} HP:{e.get('hp','?')}")

    return " ".join(parts)


def main():
    import random
    state_file = os.path.join(_HERE, "dark_state.json")

    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("?")
        return
    instruction = " ".join(sys.argv[1:]).strip()

    w = DarkWorld()
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
            _restore(w, state)
        except:
            pass

    # 如果指令是"自动"，执行自动决策
    if instruction == "自动":
        auto_result = _auto_step(w)
        if auto_result:
            result = auto_result
        else:
            # 不需要自动处理，正常前进
            result = w.cmd("前进")
    else:
        result = w.cmd(instruction)

    # 持久化跨局数据
    w._save_meta()

    # 持久化当局状态
    try:
        state = _snapshot(w)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except:
        pass

    # 输出：状态头 + 游戏内容
    header = _state_header(w)
    print(header)
    if result:
        print(result)

if __name__ == "__main__":
    main()
