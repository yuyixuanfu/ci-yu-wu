#!/usr/bin/env python3
"""词与物 MCP Server

让任何支持MCP的AI客户端直接玩词与物。

启动方式:
  # stdio 模式（Claude Code / Claude Desktop）
  python ciyuwu_mcp_server.py

  # SSE 模式（Kelivo / Cherry Studio / 其他HTTP客户端）
  python ciyuwu_mcp_server.py --sse --port 8879

在Claude Code的.mcp.json中添加:
  {
    "mcpServers": {
      "ciyuwu": {
        "command": "python",
        "args": ["ciyuwu_mcp_server.py"]
      }
    }
  }

在Kelivo等SSE客户端中配置:
  URL: http://localhost:8879/sse

工具:
  new_game  — 开新局
  play      — 执行指令
  status    — 查看当前状态
"""

import sys
import os
import io
import threading
import time
import uuid

# UTF-8
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from mcp.server import Server
from mcp.types import Tool, TextContent

from engine import new_game as _new_game, cmd as _cmd, _ensure_init, _status_bar

app = Server("ciyuwu-game")

# 服务端session存储
_lock = threading.Lock()
_initialized = False
_sessions = {}  # session_id -> (state, last_access_time)
_SESSION_MAX = 50
_SESSION_TTL = 3600


def _init():
    global _initialized
    if not _initialized:
        _ensure_init()
        _initialized = True


def _cleanup_sessions():
    now = time.time()
    expired = [sid for sid, (_, t) in _sessions.items() if now - t > _SESSION_TTL]
    for sid in expired:
        del _sessions[sid]
    if len(_sessions) > _SESSION_MAX:
        sorted_s = sorted(_sessions.items(), key=lambda x: x[1][1])
        for sid, _ in sorted_s[:len(_sessions) - _SESSION_MAX]:
            del _sessions[sid]


def _compact_text(text):
    """压缩游戏输出——去掉指令提示行，保留叙事和重要交互提示。"""
    lines = text.split('\n')
    result = []
    for line in lines:
        stripped = line.strip()
        # 过滤单引号开头的指令菜单行
        if stripped.startswith("'") and any(kw in stripped for kw in [
            '前进', '攻', '防', '术', '逃', '说', '状态', '回镇',
            '帮助', '新角', '确认', '词库', '任务', '遗忘', '用',
            '重投', '来路', '买', '写', '喊', '求签', '祈祷', '调',
            '黑活', '买酒', '赎词', '成就', '遗刻', '看', '拼',
            '切换', '我要', '物', '属性', '左', '右',
        ]):
            continue
        if stripped.startswith('工会') and '出镇' in stripped:
            continue
        if stripped.startswith('状态') and ('帮助' in stripped or '词库' in stripped) and len(stripped) < 40:
            continue
        result.append(line)
    return '\n'.join(result).strip()


# ── 指令分组说明 ──────────────────────────────────────────
_HELP_TOWN = (
    "镇上：状态/属性 | 工会/打工 | 黑活(危险赚钱) | 商店/买 | 买酒(20G清R牌) | "
    "酒馆 | 神殿 | 求签 | 残壁 | 写[话] | 赎词(2遗刻赎回遗忘的词) | "
    "广场 | 塔 | 喊[话] | 出镇[层] | 脱出 | 遗刻(看跨局进度) | "
    "成就 | 任务 | 遗忘[词] | 说[话](回声石) | 用[物品]"
)
_HELP_EXPLORE = (
    "探索：前进 | 回镇 | 词库 | 状态 | 成就 | 任务 | 遗忘[词] | 用[物品] | "
    "写[话] | 祈祷(MP换血+compliance+1)\n"
    "层级特殊：灰林→看(看隐藏词,compliance+2) | 字坟→拼[词1][词2](合成词) | "
    "红区→我要[词](创造词) | 镜湖→切换(合规/真实模式)"
)
_HELP_COMBAT = "战斗：攻 | 防 | 术 | 逃 | 说[话] | 物[物品] | 状态 | 前进(=攻)"
_HELP_DEATH = "死亡后：自由文本回答'你是谁' | 脱出回镇 | 新角重来"
_HELP_FORK = "分叉路：左 | 右 | 前进"
_HELP_CREATION = "创建：重投(属性,年龄+1) | 来路[名](选出身,需遗刻解锁) | 确认"
_HELP_CHAMBER = (
    "腔——词住在你身体里的共鸣空间：\n"
    "  喉腔：说话+30%伤害自伤，只能1词。'我在'在喉腔跳过变形。\n"
    "  胸腔：说话正常，不说话时被动共鸣。容量最大。\n"
    "  壳腔：说话自伤+50%。compliance涨幅减半。'自由'在壳腔每间房compliance-1。\n"
    "  眼腔：说话正常。变形穿过率+20%。'痛'在眼腔看到变形原文。\n"
    "  调[词][腔] 把词移到指定腔"
)
_HELP_SYNERGY = (
    "词协同——两个词同时装备时可能共振（玩家自己发现，不提前告知效果）：\n"
    "  我+痛 | 爱+不要 | 我在+她的 | 感觉+真实 | 死+自由 | 我+不要 | 爱+在一起"
)


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="new_game",
            description=(
                "开一局新的词与物——灰白世界的文字冒险。你不说话，就不存在。\n\n"
                "可选指定seed保证结果可复现。\n\n"
                "重要：开完新局后，你要把场景内容用自然语言讲给人类听——像讲故事一样。"
                "然后问人类想做什么。不要自己替人做决定。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "seed": {
                        "type": "integer",
                        "description": "随机种子，相同seed=相同世界。不填则随机。",
                    }
                },
            },
        ),
        Tool(
            name="play",
            description=(
                "执行词与物游戏指令。支持批量：'前进5'连走5步，'攻3'连攻3次，分号串联多条。\n\n"
                f"{_HELP_CREATION}\n\n"
                f"{_HELP_TOWN}\n\n"
                f"{_HELP_EXPLORE}\n\n"
                f"{_HELP_COMBAT}\n\n"
                f"{_HELP_DEATH}\n\n"
                f"{_HELP_FORK}\n\n"
                f"{_HELP_CHAMBER}\n\n"
                f"{_HELP_SYNERGY}\n\n"
                "核心规则：不要自己做决定。讲给人类听当前场景，等他们说怎么做。"
                "你可以建议（比如'这个词可以攻击'），但最终选择权在人类。"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "游戏指令，如'前进'、'攻'、'说 我在这里'、'前进5'、'出镇 灰林'、'调 痛 眼'",
                    }
                },
                "required": ["instruction"],
            },
        ),
        Tool(
            name="status",
            description="查看当前游戏状态：生命、法力、顺从度、饥饿、词表+腔、遗刻、成就、任务等。不推进游戏。",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@app.call_tool()
async def call_tool(name, arguments):
    _init()

    if name == "new_game":
        seed = arguments.get("seed")
        with _lock:
            _cleanup_sessions()
            state, text = _new_game(seed=seed)

        session_id = uuid.uuid4().hex[:16]
        with _lock:
            _sessions[session_id] = (state, time.time())

        output = _compact_text(text)
        # 加状态摘要
        status = _status_bar(state)
        if status:
            output += f"\n[{status}]"

        return [TextContent(type="text", text=output)]

    elif name == "play":
        instruction = arguments.get("instruction", "").strip()
        if not instruction:
            return [TextContent(type="text", text="空指令。试试'前进'、'攻'、'说 你好'。")]

        with _lock:
            _cleanup_sessions()
            # 找最近的session
            if _sessions:
                latest_sid = max(_sessions, key=lambda s: _sessions[s][1])
                state, _ = _sessions[latest_sid]
            else:
                state, text = _new_game()
                session_id = uuid.uuid4().hex[:16]
                _sessions[session_id] = (state, time.time())
                return [TextContent(type="text", text=f"没有存档，已自动开新局。\n\n{_compact_text(text)}")]

            new_state, output = _cmd(state, instruction)

            # 更新session
            _sessions[latest_sid] = (new_state, time.time())

        compact = _compact_text(output)
        status = _status_bar(new_state)
        if status:
            compact += f"\n[{status}]"

        return [TextContent(type="text", text=compact)]

    elif name == "status":
        with _lock:
            if _sessions:
                latest_sid = max(_sessions, key=lambda s: _sessions[s][1])
                state, _ = _sessions[latest_sid]
            else:
                return [TextContent(type="text", text="没有存档。用new_game开一局。")]

        status = _status_bar(state)

        lines = [status]
        phase = state.get("phase", "")
        area = state.get("area", "")
        hp = state.get("hp", 0)
        max_hp = state.get("max_hp", 1)
        mp = state.get("mp", 0)
        max_mp = state.get("max_mp", 1)
        compliance = state.get("compliance", 0)
        hunger = state.get("hunger", 0)
        words = state.get("words", [])

        lines.append(f"阶段: {phase} | 区域: {area}")
        lines.append(f"HP: {hp}/{max_hp} | MP: {mp}/{max_mp}")
        lines.append(f"顺从: {compliance} | 饥饿: {hunger}")

        # 词表+腔
        if words:
            word_chambers = state.get("word_chambers", {})
            chamber_names = {"喉": "喉腔", "胸": "胸腔", "壳": "壳腔", "眼": "眼腔"}
            word_info = []
            for w in words:
                ch = word_chambers.get(w, "")
                if ch:
                    word_info.append(f"{w}[{chamber_names.get(ch, ch)}]")
                else:
                    word_info.append(w)
            lines.append(f"词表: {', '.join(word_info)}")

        gold = state.get("gold", 0)
        if gold:
            lines.append(f"金币: {gold}")

        # 遗刻+局数
        echoes = state.get("echoes", 0)
        runs = state.get("runs", 0)
        if echoes or runs:
            lines.append(f"遗刻: {echoes} | 局数: {runs}")

        # 遗忘的词
        forgotten = state.get("forgotten_words", [])
        if forgotten:
            lines.append(f"已遗忘: {', '.join(forgotten)}")

        # 成就
        achievements = state.get("achievements", [])
        if achievements:
            lines.append(f"成就: {', '.join(achievements)}")

        # 来路
        origin = state.get("origin", "")
        if origin:
            lines.append(f"来路: {origin}")

        return [TextContent(type="text", text="\n".join(lines))]

    return [TextContent(type="text", text=f"未知工具: {name}")]


def run_stdio():
    """stdio模式（默认）"""
    from mcp.server.stdio import stdio_server
    import asyncio

    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(main())


def run_sse(port=8879):
    """SSE模式（Kelivo / Cherry Studio / 其他HTTP MCP客户端用）"""
    try:
        from mcp.server.sse import SseServerTransport
    except ImportError:
        print("需要 starlette 和 uvicorn：pip install starlette uvicorn")
        sys.exit(1)

    try:
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn
    except ImportError:
        print("需要 starlette 和 uvicorn：pip install starlette uvicorn")
        sys.exit(1)

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send
        ) as streams:
            await app.run(
                streams[0], streams[1], app.create_initialization_options()
            )

    async def handle_messages(request):
        await sse.handle_post_message(request._receive, request._send)

    starlette_app = Starlette(
        debug=False,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
        ],
    )

    print(f"词与物 MCP SSE Server → http://localhost:{port}/sse")
    uvicorn.run(starlette_app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="词与物 MCP Server")
    parser.add_argument("--sse", action="store_true", help="使用SSE模式（HTTP）代替stdio")
    parser.add_argument("--port", type=int, default=8879, help="SSE模式端口（默认8879）")
    args = parser.parse_args()

    if args.sse:
        run_sse(args.port)
    else:
        run_stdio()
