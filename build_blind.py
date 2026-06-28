#!/usr/bin/env python3
"""词与物 — 盲玩版构建脚本

把引擎代码打包成base64，AI只能看到cmd()接口，看不到游戏数据。

用法:
  python build_blind.py       → 生成 ciyuwu_blind.py

盲玩版用法:
  from ciyuwu_blind import new_game, cmd
  state, text = new_game()
  state, text = cmd(state, "新角")
"""
import os, base64, sys

_HERE = os.path.dirname(os.path.abspath(__file__))

FILES = ["dark_engine.py", "dark_combat.py", "dark_data.py", "engine.py"]

HEADER = '''#!/usr/bin/env python3
"""词与物 (Word and Thing) — 盲玩版

AI可玩的暗黑文字Roguelike。关于审查、沉默和说出真话。

接口:
  new_game(seed=None)  → (state, text)   开新局
  cmd(state, inst)     → (state, text)   执行指令

核心指令:
  新角        建新角色
  确认        确认角色
  前进        走向下一个房间
  说 [话]     说话（核心机制！消音词伤敌也伤己）
  攻/防/术/逃  战斗指令
  出镇 [层名]  进入区域
  回镇        返回镇上
  帮助        查看所有指令

批量: 前进5 = 连走5步, 攻3 = 连攻3次
串联: 前进;说 我在;前进 = 依次执行

每次输出末尾有JSON状态栏，显示当前阶段、HP、词库等。
"""
import sys, os, io, tempfile, importlib, json, types

# 确保UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 解码引擎
_ENGINE_CODE = """

{engine_b64}

"""

def _setup():
    """解码引擎文件并创建临时模块。"""
    import base64
    decoded = base64.b64decode(_ENGINE_CODE.strip()).decode('utf-8')

    # 创建临时目录存放引擎文件
    tmpdir = tempfile.mkdtemp(prefix="ciyuwu_")

    # 拆分各文件
    files = {}
    current_file = None
    current_lines = []

    for line in decoded.split('\\n'):
        if line.startswith("###FILE:"):
            if current_file:
                files[current_file] = '\\n'.join(current_lines)
            current_file = line[8:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_file:
        files[current_file] = '\\n'.join(current_lines)

    # 写入临时文件
    for fname, content in files.items():
        fpath = os.path.join(tmpdir, fname)
        with open(fpath, 'w', encoding='utf-8') as f:
            f.write(content)

    # 把临时目录加到sys.path最前面
    sys.path.insert(0, tmpdir)
    return tmpdir

_initialized = False
_tmpdir = None

def _ensure():
    global _initialized, _tmpdir
    if not _initialized:
        _tmpdir = _setup()
        _initialized = True

def new_game(seed=None):
    """开新局。返回 (state, text)。"""
    _ensure()
    from engine import new_game as _ng
    return _ng(seed)

def cmd(state, instruction):
    """执行指令。返回 (state, text)。"""
    _ensure()
    from engine import cmd as _cmd
    return _cmd(state, instruction)

def load_game():
    """从文件读存档。返回 state 或 None。"""
    _ensure()
    from engine import load_game as _lg
    return _lg()

def save_game(state):
    """存档到文件。"""
    _ensure()
    from engine import save_game as _sg
    return _sg(state)

if __name__ == "__main__":
    _ensure()
    from engine import main
    main()
'''

FOOTER = '''
# 清理临时文件
import atexit, shutil
if _tmpdir:
    atexit.register(lambda: shutil.rmtree(_tmpdir, ignore_errors=True))
'''

def build():
    """构建盲玩版。"""
    all_code = []

    for fname in FILES:
        fpath = os.path.join(_HERE, fname)
        if not os.path.exists(fpath):
            print(f"  缺少: {fname}")
            return False
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()
        all_code.append(f"###FILE:{fname}")
        all_code.append(content)

    combined = "\n".join(all_code)
    encoded = base64.b64encode(combined.encode('utf-8')).decode('ascii')

    output = HEADER.replace("{engine_b64}", encoded)
    # 不加FOOTER——atexit在盲玩场景可能不触发

    out_path = os.path.join(_HERE, "ciyuwu_blind.py")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(output)

    size_kb = os.path.getsize(out_path) / 1024
    print(f"Done: {out_path} ({size_kb:.0f}KB)")
    return True

if __name__ == "__main__":
    build()
