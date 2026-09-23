#!/usr/bin/env python3
"""暴力冒烟测试：随机指令轰炸 + 边界用例扫描。"""
import sys, os, random, traceback, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import _ensure_init, new_game, cmd as _cmd, _snapshot, _restore
_ensure_init()

PASS = FAIL = 0
CRASHES = []

def report(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        CRASHES.append(f"[FAIL] {name}: {detail}")

# 1. 大量随机种子全流程
print("=== 1. 100 个种子全流程 ===")
for seed in range(100):
    try:
        random.seed(seed)
        state, _ = new_game(seed=seed)
        for _ in range(60):
            state, t = _cmd(state, random.choice([
                '新角', '确认', '出镇 灰林', '前进', '前进', '前进',
                '状态', '词库', '遗刻', '任务', '回镇', '不捡', '说 我在',
                '离开', '前进5', '前进3', '攻', '防', '术', '逃',
                '状态;状态', '前进;状态',
                '', '   ', 'xyz不存在', '💀', '\x00',
            ]))
    except Exception as e:
        report(f'seed={seed}', False, f'{type(e).__name__}: {e}')
        if len(CRASHES) <= 3:
            traceback.print_exc()
report("100 种子随机流程", FAIL == 0, f"{FAIL} crashes")

# 2. 边界输入
print("\n=== 2. 边界输入 ===")
state, _ = new_game(seed=1)
s, t = _cmd(state, '')
report("空字符串", t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
s, t = _cmd(state, '   ')
report("仅空白", t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
s, t = _cmd(state, '\x00\x01\x02')
report("控制字符", t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
s, t = _cmd(state, 'A' * 500)
report("超长字符串(500)", "新角" in t or t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
s, t = _cmd(state, '前进0')
report("前进0", "新角" in t or t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
s, t = _cmd(state, '前进-5')
report("前进-5", "新角" in t or t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
s, t = _cmd(state, '前进99999')
report("前进99999 (超上限)", "新角" in t or t.startswith("?"), t[:50])

state, _ = new_game(seed=1)
try:
    s, t = _cmd(state, None)
    report("None 输入", True, t[:50])
except Exception as e:
    report("None 输入", False, str(e))

# 3. 战斗内指令
print("\n=== 3. 战斗内指令 ===")
combat_found = False
for _seed in (42, 1, 7, 99, 123, 256, 7):
    state, _ = new_game(seed=_seed)
    s, t = _cmd(state, '新角')
    s, t = _cmd(s, '确认')
    s, t = _cmd(s, '出镇 灰林')
    for i in range(50):
        s, t = _cmd(s, '前进')
        bar_str = t.strip().split('\n')[-1] if t else '{}'
        try:
            bar = json.loads(bar_str)
        except json.JSONDecodeError:
            bar = {}
        if bar.get('sub') == 'pickup':
            _cmd(s, '不捡')
            continue
        if bar.get('phase') == '战斗':
            combat_found = True
            for inst in ['攻', '防', '术', '逃', '说 痛', '说 我在', '说 不', '状态', '物', '不捡']:
                try:
                    s2, t2 = _cmd(s, inst)
                except Exception as e:
                    report(f"战斗内 {inst}", False, str(e))
            break
    if combat_found:
        break
report("战斗内指令", combat_found, "no combat triggered" if not combat_found else "")

# 4. 跨进程状态恢复
print("\n=== 4. 状态恢复 ===")
state, _ = new_game(seed=10)
s, t = _cmd(state, '新角')
s, t = _cmd(s, '确认')
# round-trip snapshot
w = __import__('dark_engine').DarkWorld()
_restore(w, json.loads(json.dumps(s)))
snap = _snapshot(w)
try:
    json_str = json.dumps(snap, ensure_ascii=False)
    report("snapshot 序列化", True, f"{len(snap)} 字段")
except Exception as e:
    report("snapshot 序列化", False, str(e))

# 5. 老年死亡路径
print("\n=== 5. 老年死亡路径 ===")
state, _ = new_game(seed=1)
w = __import__('dark_engine').DarkWorld()
_restore(w, json.loads(json.dumps(state)))
w.age = 75  # 强制触发
try:
    w._apply_aging()
    report("老年死亡不崩", w.phase in ('dead', 'dead_who', 'void'), f"phase={w.phase}")
except Exception as e:
    report("老年死亡不崩", False, str(e))

# 6. 死亡后状态
print("\n=== 6. 死亡流程 ===")
state, _ = new_game(seed=1)
s, t = _cmd(state, '新角')
s, t = _cmd(s, '确认')
# 强制老年死亡，可靠进入死亡流程（打工只在镇上可用）
# 轻负者/折痕等镇上交互会拦截指令——先清掉，否则打工不触发衰老
for k, v in (('_light_bearer_active', False), ('_crease_active', False),
             ('_square_sit', 0), ('_square_active', False),
             ('current_sage', None), ('current_special', None),
             ('current_broken', None), ('_pending_pickup', None),
             ('_four_o_active', False), ('_angel_deal_active', False),
             ('_devil_deal_active', False), ('_tavern_regular_active', False)):
    s[k] = v
s['age'] = 69
s['runs'] = 0
s, t = _cmd(s, '打工')
bar_str = t.strip().split('\n')[-1] if t else '{}'
try:
    bar = json.loads(bar_str)
except json.JSONDecodeError:
    bar = {}
died = bar.get('phase') in ('死亡', '死后问答', '存档选择', '虚空') or bar.get('sub') in ('dead_who', 'dead_wipe')
if died:
    # 死后的指令
    for cmd_i in ['说 我在', '新角', '脱出']:
        try:
            s2, t2 = _cmd(s, cmd_i)
        except Exception as e:
            report(f"死后 {cmd_i}", False, str(e))
report("死亡流程", died, "" if died else f"未进入死亡 phase={bar.get('phase')}")

# 7. 100 次快照-恢复往返
print("\n=== 7. 快照往返 ===")
state, _ = new_game(seed=1)
s = state
for i in range(100):
    try:
        s, t = _cmd(s, '前进')
        w = __import__('dark_engine').DarkWorld()
        _restore(w, json.loads(json.dumps(s)))
        snap = _snapshot(w)
        json.dumps(snap, ensure_ascii=False)
    except Exception as e:
        report(f"快照往返 #{i}", False, str(e))
        break
else:
    report("100 次快照往返", True)

# 8. 并行多 DarkWorld 实例（模拟多 session）
print("\n=== 8. 并行 DarkWorld ===")
import threading
errors = []
def worker(seed):
    try:
        s, t = new_game(seed=seed)
        for _ in range(20):
            s, t = _cmd(s, '前进')
    except Exception as e:
        errors.append((seed, str(e)))
threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
for t in threads: t.start()
for t in threads: t.join()
report("10 线程并行", len(errors) == 0, str(errors[:2]))

print(f"\n=== 总计: PASS={PASS}, FAIL={FAIL} ===")
if CRASHES:
    print("\n崩溃列表:")
    for c in CRASHES[:10]:
        print(f"  {c}")
sys.exit(0 if FAIL == 0 else 1)