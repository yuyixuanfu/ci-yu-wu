import json, sys, os
os.environ["PYTHONUTF8"] = "1"
from engine import new_game, cmd

PASS = FAIL = 0
def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL: {name} {detail}")

print("=== 1. 战斗中前进 vs 攻击 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角"); s, t = cmd(s, "确认"); s, t = cmd(s, "出镇 灰林")
for i in range(20):
    s, t = cmd(s, "前进")
    bar = json.loads(t.strip().splitlines()[-1])
    sub = bar.get("sub", "")
    if sub == "pickup":
        s, t = cmd(s, "不捡")
    if bar.get("phase") == "combat":
        # 前进
        s_fwd = dict(s)
        s_fwd, t_fwd = cmd(s_fwd, "前进")
        # 攻
        s, t_atk = cmd(s, "攻")
        fwd_lines = [l for l in t_fwd.strip().splitlines() if not l.startswith("{")]
        atk_lines = [l for l in t_atk.strip().splitlines() if not l.startswith("{")]
        print(f"  前进: {' | '.join(fwd_lines[:3])}")
        print(f"  攻击: {' | '.join(atk_lines[:3])}")
        # 前进应该和攻一样有敌方反击
        has_enemy_atk_fwd = any("伤害" in l or "攻击" in l for l in fwd_lines)
        has_enemy_atk_atk = any("伤害" in l or "攻击" in l for l in atk_lines)
        if has_enemy_atk_atk and not has_enemy_atk_fwd:
            check("前进有敌方攻击回合", False, "前进没有敌方攻击回合")
        else:
            check("前进有敌方攻击回合", True)
        break

print("\n=== 2. 碎片选择: 捡 vs 不捡 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角"); s, t = cmd(s, "确认"); s, t = cmd(s, "出镇 灰林")
s, t = cmd(s, "前进")
bar = json.loads(t.strip().splitlines()[-1])
if bar.get("sub") == "pickup":
    comp_before = bar.get("compliance", 0)
    # 捡
    s_pick = dict(s)
    s_pick, t_pick = cmd(s_pick, "捡")
    bar_pick = json.loads(t_pick.strip().splitlines()[-1])
    comp_pick = bar_pick.get("compliance", 0)
    # 不捡
    s, t_nopick = cmd(s, "不捡")
    bar_nopick = json.loads(t_nopick.strip().splitlines()[-1])
    comp_nopick = bar_nopick.get("compliance", 0)
    print(f"  捡: compliance {comp_before} -> {comp_pick}")
    print(f"  不捡: compliance {comp_before} -> {comp_nopick}")
    if comp_pick == comp_before and comp_nopick == comp_before:
        check("捡有效果", False, "捡了也没效果")
    elif comp_nopick > comp_before:
        check("不捡不变", False, "不捡也涨了")
    else:
        check("捡有效果，不捡不变", True)
else:
    print("  没碎片，跳过")

print("\n=== 3. boss前特别遭遇 → boss战 ===")
for seed in [1, 7, 42, 99, 123, 256, 500, 777]:
    s, t = new_game(seed=seed)
    s, t = cmd(s, "新角"); s, t = cmd(s, "确认"); s, t = cmd(s, "出镇 灰林")
    boss_entered = False
    for i in range(30):
        s, t = cmd(s, "前进")
        bar = json.loads(t.strip().splitlines()[-1])
        sub = bar.get("sub", "")
        phase = bar.get("phase", "")
        if sub == "pickup": s, t = cmd(s, "不捡"); continue
        if phase == "fork": s, t = cmd(s, "左"); continue
        if phase == "combat":
            for j in range(20):
                s, t = cmd(s, "攻")
                b = json.loads(t.strip().splitlines()[-1])
                if b.get("phase") != "combat": break
            continue
        if sub == "special":
            s, t = cmd(s, "跳过")
            bar2 = json.loads(t.strip().splitlines()[-1])
            if bar2.get("phase") in ("combat", "战斗"):
                check(f"seed {seed} 跳过后进boss", True)
                boss_entered = True
            else:
                # 前进可能进boss
                s, t = cmd(s, "前进")
                bar3 = json.loads(t.strip().splitlines()[-1])
                if bar3.get("phase") in ("combat", "战斗"):
                    check(f"seed {seed} 跳过→前进进boss", True)
                    boss_entered = True
                else:
                    # 非 boss 前的特殊遭遇，跳过后探索是正常行为
                    check(f"seed {seed} 跳过后", bar2.get('phase') in ('combat', '战斗', 'explore', '探索'), f"phase={bar2.get('phase')}")
            break
    if not boss_entered and i >= 29:
        pass  # 没触发special，不是bug

print("\n=== 4. 好公民徽章效果 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角"); s, t = cmd(s, "确认"); s, t = cmd(s, "出镇 灰林")
s, t = cmd(s, "前进")
bar = json.loads(t.strip().splitlines()[-1])
pickup_name = bar.get("pickup", "")
if pickup_name == "好公民徽章":
    comp_before = bar.get("compliance", 0)
    words_before = bar.get("words", [])[:]
    s, t = cmd(s, "捡")
    bar2 = json.loads(t.strip().splitlines()[-1])
    comp_after = bar2.get("compliance", 0)
    words_after = bar2.get("words", [])
    print(f"  compliance: {comp_before} -> {comp_after} (预期+5)")
    print(f"  words: {len(words_before)} -> {len(words_after)} (预期-1)")
    if comp_after != comp_before + 5:
        check("compliance变化+5", False, f"{comp_before}→{comp_after}")
    else:
        check("compliance变化+5", True)
else:
    print(f"  碎片是{pickup_name}不是好公民徽章，跳过")

print("\n=== 5. 存档/读档 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角"); s, t = cmd(s, "确认")
from engine import save_game, load_game
save_game(s)
s2 = load_game()
if s2 is not None:
    s2, t2 = cmd(s2, "帮助")
    check("存档读档正常", "指令" in t2)
else:
    check("存档读档正常", False, "存档读不到")

print(f"\n=== 测试完成: PASS={PASS}, FAIL={FAIL} ===")
sys.exit(1 if FAIL else 0)
