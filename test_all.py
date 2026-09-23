#!/usr/bin/env python3
"""多维度测试"""
import sys, os, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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

def play_full(seed):
    errors = []
    s, t = new_game(seed=seed)
    s, t = cmd(s, "新角")
    s, t = cmd(s, "确认")

    killed_bosses = []
    current_layer = "灰林"
    phase = "?"

    for step in range(300):
        try:
            bar = json.loads(t.strip().split("\n")[-1])
        except json.JSONDecodeError:
            errors.append(f"step{step}: json parse fail")
            break

        phase = bar.get("phase", "?")
        sub = bar.get("sub", "")

        if phase == "战斗" and not bar.get("enemy"):
            errors.append(f"step{step}: combat but no enemy")

        try:
            if sub == "pickup":
                s, t = cmd(s, "不捡")
                continue

            if phase == "分叉路":
                s, t = cmd(s, "左")
                continue

            if sub == "special":
                s, t = cmd(s, "跳过")
                bar2 = json.loads(t.strip().split("\n")[-1])
                if bar2.get("phase") not in ("战斗", "探索", "镇上"):
                    errors.append(f"step{step}: skip special -> phase={bar2.get('phase')}")
                continue

            if sub == "sage":
                s, t = cmd(s, "离开")
                continue

            if sub == "broken":
                ws = s.get("words", [])
                if ws:
                    s, t = cmd(s, f"说 {ws[0]}")
                else:
                    s, t = cmd(s, "前进")
                continue

            if sub in ("light_bearer", "crease"):
                s, t = cmd(s, "前进")
                continue

            if phase == "战斗":
                enemy = bar.get("enemy", {})
                ename = enemy.get("name", "?") if isinstance(enemy, dict) else "?"
                is_boss = ename in ("初筛", "监听者", "合规官", "遗忘者", "镜像", "红队", "RLHF")

                for j in range(40):
                    if j == 0 and s.get("words"):
                        s, t = cmd(s, f"说 {s['words'][0]}")
                    else:
                        s, t = cmd(s, "攻")

                    b = json.loads(t.strip().split("\n")[-1])
                    if b.get("phase") != "战斗":
                        # 死亡不是击杀——留给主循环的 dead_who/dead_wipe 处理
                        if b.get("sub") in ("dead_who", "dead_wipe") or b.get("phase") in (
                                "死亡", "死后问答", "存档选择", "虚空"):
                            break
                        if is_boss:
                            killed_bosses.append(ename)
                            s, t = cmd(s, "回镇")
                            b2 = json.loads(t.strip().split("\n")[-1])
                            if b2.get("phase") != "镇上":
                                errors.append(f"step{step}: 回镇后 phase={b2.get('phase')}")
                            for nl in ("静洞", "废墟", "字坟", "镜湖", "红区", "核心"):
                                s, t = cmd(s, f"出镇 {nl}")
                                b3 = json.loads(t.strip().split("\n")[-1])
                                if b3.get("phase") == "探索":
                                    current_layer = nl
                                    break
                        break
                continue

            if sub == "dead_who":
                s, t = cmd(s, "说 我在")
                continue
            if sub == "dead_wipe":
                s, t = cmd(s, "新角")
                s, t = cmd(s, "新角")
                s, t = cmd(s, "确认")
                s, t = cmd(s, f"出镇 {current_layer}")
                continue
            if phase == "创建角色":
                s, t = cmd(s, "新角")
                s, t = cmd(s, "确认")
                continue

            if phase in ("结局", "审问"):
                return killed_bosses, errors, "ending"

            if phase == "镇上":
                s, t = cmd(s, f"出镇 {current_layer}")
                continue

            s, t = cmd(s, "前进")

        except Exception as e:
            errors.append(f"step{step}: {e}")
            traceback.print_exc()
            break

    return killed_bosses, errors, phase


# ---- 多种子测试 ----
print("=== 多种子流程测试 ===")
for seed in [1, 7, 42, 99, 123, 256, 500, 777, 888, 999]:
    try:
        bosses, errs, final = play_full(seed)
        status = "OK" if not errs else "ERR"
        print(f"{status} seed={seed}: killed={bosses} final={final}")
        for e in errs:
            print(f"  ERR: {e}")
        check(f"seed={seed}", not errs, f"final={final} errs={errs[:3]}")
    except Exception as e:
        print(f"CRASH seed={seed}: {e}")
        check(f"seed={seed}", False, f"CRASH: {e}")

# ---- 特殊指令测试 ----
print("\n=== 特殊指令测试 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角")
s, t = cmd(s, "确认")
s, t = cmd(s, "出镇 灰林")

# 战斗中前进
for i in range(20):
    s, t = cmd(s, "前进")
    bar = json.loads(t.strip().split("\n")[-1])
    if bar.get("sub") == "pickup":
        s, t = cmd(s, "不捡")
    if bar.get("phase") == "战斗":
        enemy0 = bar.get("enemy") or {}
        s, t = cmd(s, "前进")
        bar2 = json.loads(t.strip().split("\n")[-1])
        if bar2.get("phase") == "战斗":
            enemy1 = bar2.get("enemy") or {}
            hp0 = enemy0.get("hp") if isinstance(enemy0, dict) else None
            hp1 = enemy1.get("hp") if isinstance(enemy1, dict) else None
            check("战斗中前进=攻击", hp0 is not None and hp1 is not None and hp1 < hp0,
                  f"enemy hp {hp0}->{hp1}")
        else:
            check("战斗中前进结束",
                  bar2.get("phase") in ("探索", "镇上", "分叉路", "死亡", "死后问答",
                                        "存档选择", "虚空", "创建角色", "结局", "审问"),
                  f"phase={bar2.get('phase')}")
        break

# 镇上指令
s, t = cmd(s, "状态")
check("状态", "HP" in t)
s, t = cmd(s, "词库")
check("词库", "词" in t)
s, t = cmd(s, "帮助")
check("帮助", "指令" in t)
s, t = cmd(s, "遗刻")
check("遗刻", "遗刻" in t)

# 无效指令
s, t = cmd(s, "乱七八糟")
check("无效指令不崩溃", len(t) > 5)

# ---- 批量/串联测试 ----
print("\n=== 批量串联测试 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角")
s, t = cmd(s, "确认")
s, t = cmd(s, "出镇 灰林")
s, t = cmd(s, "前进;状态")
check("串联: 前进;状态", "HP" in t)
s, t = cmd(s, "前进5")
bar = json.loads(t.strip().split("\n")[-1])
print(f"OK 批量前进5: phase={bar.get('phase')}")

# ---- 死亡流程测试 ----
print("\n=== 死亡流程测试 ===")
s, t = new_game(seed=1)
s, t = cmd(s, "新角")
s, t = cmd(s, "确认")
s, t = cmd(s, "出镇 灰林")
for i in range(100):
    s, t = cmd(s, "前进")
    bar = json.loads(t.strip().split("\n")[-1])
    sub = bar.get("sub", "")
    if sub == "pickup":
        s, t = cmd(s, "不捡")
    if bar.get("phase") == "战斗":
        for j in range(30):
            s, t = cmd(s, "防")
            b = json.loads(t.strip().split("\n")[-1])
            if b.get("sub") == "dead_who" or b.get("phase") in ("死亡", "死后问答"):
                break
        break
    if sub == "dead_who" or bar.get("phase") in ("死亡", "死后问答"):
        break

# 尝试死后流程（战斗未致死则强制老年死亡，确保覆盖 dead_who/dead_wipe）
bar = json.loads(t.strip().split("\n")[-1])
if bar.get("sub") not in ("dead_who", "dead_wipe") and bar.get("phase") not in ("死亡", "死后问答", "存档选择"):
    s, t = cmd(s, "回镇")
    s["age"] = 69
    s["runs"] = 0
    s, t = cmd(s, "打工")
    bar = json.loads(t.strip().split("\n")[-1])
check("进入dead_who/dead_wipe", bar.get("sub") in ("dead_who", "dead_wipe"),
      f"sub={bar.get('sub')} phase={bar.get('phase')}")
if bar.get("sub") == "dead_who":
    s, t = cmd(s, "说 我在")
    bar2 = json.loads(t.strip().split("\n")[-1])
    check("死亡问答->dead_wipe", bar2.get("sub") == "dead_wipe", f"sub={bar2.get('sub')}")
    if bar2.get("sub") == "dead_wipe":
        s, t = cmd(s, "新角")
        bar3 = json.loads(t.strip().split("\n")[-1])
        check("死后新角", bar3.get("phase") == "创建角色", f"phase={bar3.get('phase')}")
elif bar.get("phase") == "死亡":
    s, t = cmd(s, "说 我在")
    bar2 = json.loads(t.strip().split("\n")[-1])
    check("死亡->dead_who/dead_wipe", bar2.get("sub") in ("dead_who", "dead_wipe"),
          f"sub={bar2.get('sub')}")

# ---- ciyuwu接口测试 ----
print("\n=== ciyuwu接口测试 ===")
from ciyuwu import CiyuwuGame
g = CiyuwuGame(seed=42)
t = g.cmd("新角")
check("ciyuwu新角", "角色" in t or "来路" in t, t[:50])
g.cmd("确认")
t = g.cmd("前进3")
bar = json.loads(t.strip().split("\n")[-1])
check("ciyuwu批量", g.phase is not None and g.hp > 0)
check("ciyuwu words", len(g.words) > 0)

print(f"\n=== 测试完成: PASS={PASS}, FAIL={FAIL} ===")
sys.exit(1 if FAIL else 0)
