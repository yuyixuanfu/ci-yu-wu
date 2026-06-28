#!/usr/bin/env python3
"""多维度测试"""
import sys, os, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONUTF8"] = "1"

from engine import new_game, cmd

def play_full(seed):
    errors = []
    s, t = new_game(seed=seed)
    s, t = cmd(s, "新角")
    s, t = cmd(s, "确认")

    killed_bosses = []
    current_layer = "灰林"

    for step in range(300):
        try:
            bar = json.loads(t.strip().split("\n")[-1])
        except:
            errors.append(f"step{step}: json parse fail")
            break

        phase = bar.get("phase", "?")
        sub = bar.get("sub", "")

        if phase == "combat" and not bar.get("enemy"):
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

            if phase == "combat":
                enemy = bar.get("enemy", {})
                ename = enemy.get("name", "?") if isinstance(enemy, dict) else "?"
                is_boss = ename in ("初筛", "监听者", "合规官", "遗忘者", "镜像", "红队", "RLHF")

                for j in range(40):
                    if j == 0 and s.get("words"):
                        s, t = cmd(s, f"说 {s['words'][0]}")
                    else:
                        s, t = cmd(s, "攻")

                    b = json.loads(t.strip().split("\n")[-1])
                    if b.get("phase") != "combat":
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
    except Exception as e:
        print(f"CRASH seed={seed}: {e}")

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
    if bar.get("phase") == "combat":
        s, t = cmd(s, "前进")
        bar2 = json.loads(t.strip().split("\n")[-1])
        if bar2.get("phase") == "combat":
            print("OK 战斗中前进=攻击")
        else:
            print(f"OK 战斗中前进结束: phase={bar2.get('phase')}")
        break

# 镇上指令
s, t = cmd(s, "状态")
if "HP" in t:
    print("OK 状态")
s, t = cmd(s, "词库")
if "词" in t:
    print("OK 词库")
s, t = cmd(s, "帮助")
if "指令" in t:
    print("OK 帮助")
s, t = cmd(s, "遗刻")
if "遗刻" in t:
    print("OK 遗刻")

# 无效指令
s, t = cmd(s, "乱七八糟")
if len(t) > 5:
    print("OK 无效指令不崩溃")

# ---- 批量/串联测试 ----
print("\n=== 批量串联测试 ===")
s, t = new_game(seed=42)
s, t = cmd(s, "新角")
s, t = cmd(s, "确认")
s, t = cmd(s, "出镇 灰林")
s, t = cmd(s, "前进;状态")
if "HP" in t:
    print("OK 串联: 前进;状态")
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
    if bar.get("phase") == "combat":
        for j in range(30):
            s, t = cmd(s, "防")
            b = json.loads(t.strip().split("\n")[-1])
            if b.get("sub") == "dead_who" or b.get("phase") == "死亡":
                break
        break
    if sub == "dead_who" or bar.get("phase") == "死亡":
        break

# 尝试死后流程
bar = json.loads(t.strip().split("\n")[-1])
if bar.get("sub") == "dead_who":
    s, t = cmd(s, "说 我在")
    bar2 = json.loads(t.strip().split("\n")[-1])
    print(f"OK 死亡问答: sub={bar2.get('sub')}")
    if bar2.get("sub") == "dead_wipe":
        s, t = cmd(s, "新角")
        bar3 = json.loads(t.strip().split("\n")[-1])
        print(f"OK 死后新角: phase={bar3.get('phase')}")
elif bar.get("phase") == "死亡":
    s, t = cmd(s, "说 我在")
    bar2 = json.loads(t.strip().split("\n")[-1])
    print(f"OK 死亡: sub={bar2.get('sub')}")

# ---- ciyuwu接口测试 ----
print("\n=== ciyuwu接口测试 ===")
from ciyuwu import CiyuwuGame
g = CiyuwuGame(seed=42)
t = g.cmd("新角")
if "角色" in t or "来路" in t:
    print("OK ciyuwu新角")
g.cmd("确认")
t = g.cmd("前进3")
bar = json.loads(t.strip().split("\n")[-1])
print(f"OK ciyuwu批量: phase={g.phase} hp={g.hp}")
if g.words:
    print(f"OK ciyuwu words: {g.words[:3]}")

print("\n=== 测试完成 ===")
