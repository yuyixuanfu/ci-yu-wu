# 词与物-AI版 审计报告

审计时间: 2026-08-21
审计范围: 全部源码（engine.py, dark_engine.py, dark_combat.py, dark_data.py, ciyuwu.py, ciyuwu_server.py, ciyuwu_mcp_server.py, stress_test.py）

---

## 1. 代码结构

### 1.1 职责分工

| 文件 | 职责 | 评价 |
|------|------|------|
| dark_data.py | 纯数据表（词、武器、变形、怪物、NPC、对话） | 清晰。纯数据无逻辑。 |
| dark_combat.py | 战斗状态机（CombatState） | 清晰。独立模块，只依赖dark_data。 |
| dark_engine.py | 游戏世界核心（DarkWorld） | 职责过重。~6000行，承担了镇/探索/战斗/存档/NPC/任务/成就/全部交互逻辑。 |
| engine.py | 接口层（快照/恢复/PRNG/状态栏） | 清晰。是dark_engine的壳。 |
| ciyuwu.py | Operit兼容接口 | 清晰。薄封装。 |
| ciyuwu_server.py | HTTP API（Flask） | 清晰。 |
| ciyuwu_mcp_server.py | MCP协议接口 | 清晰。 |

**发现 1.1-1: dark_engine.py 职责过重 (S3)**
- ~6000行单一文件，包含镇/探索/战斗触发/NPC/任务/成就/存档全部逻辑。
- 建议：长期考虑拆分为 town.py / explore.py / npc.py 等子模块。当前不阻塞功能。

### 1.2 循环依赖

```
engine.py -> dark_engine, dark_combat, dark_data
dark_engine.py -> engine (仅 _atomic_json_write, _SAVE_FILE), dark_data, dark_combat
dark_combat.py -> dark_data
dark_data.py -> (无)
ciyuwu.py -> engine, dark_engine
```

**发现 1.2-1: engine.py <-> dark_engine.py 存在循环引用 (S3)**
- engine.py import dark_engine（在函数内延迟导入）
- dark_engine.py import engine（顶层 from engine import _atomic_json_write, _SAVE_FILE）
- 当前通过延迟导入（engine.py 内部函数才导入dark_engine）避免了启动时死锁，但结构脆弱。
- 建议：把 `_atomic_json_write` 和 `_SAVE_FILE` 提取到独立的 util.py 或 config.py，切断循环。

### 1.3 全局状态

| 全局变量 | 位置 | 风险 |
|----------|------|------|
| `_initialized` | engine.py:366 | 低。布尔标记，线程安全。 |
| `_det_rng` | engine.py:126 | **中**。确定性PRNG，被 `_patch_random()` 注入到所有模块的 `random`。多线程共享同一PRNG实例。 |
| `_game` | ciyuwu.py:145 | 低。单进程单用户。 |
| `_initialized` | ciyuwu_server.py:39 | 低。 |
| `_sessions` | ciyuwu_server.py:42, ciyuwu_mcp_server.py:59 | 中。内存字典，多session共享，但有锁保护。 |

**发现 1.3-1: _patch_random() 注入全局 random 模块 (S2)**
- `dark_engine.random = _det_rng` 直接替换模块级 random。
- 如果任何第三方库 import dark_engine 后使用 `dark_engine.random`，会得到确定性版本。
- 多线程场景下 `_DetRandom` 没有内部锁，两个线程同时调用 `random()` 会竞争 `_gen` 生成器。
- stress_test.py 的10线程并行测试通过了，但这是概率性的，不能证明线程安全。
- 建议：给 `_DetRandom` 加 threading.Lock，或在 `_patch_random` 时用线程局部变量。

---

## 2. 数据完整性

### 2.1 CENSORED_WORDS 无重复

四级词共28个：一级8、二级8、三级7、四级5。无跨级重复，无级内重复。通过。

### 2.2 WORD_WEAPON 覆盖

所有28个消音词都有对应的武器数据。额外有2个合成词（"我不要"、"我爱你"）。无遗漏。

### 2.3 DEFORMATION 覆盖缺口 (S2)

4个消音词在DEFORMATION表中没有对应条目：

| 词 | 级 | 缺失影响 |
|----|----|---------|
| 怒 | 1 | 说出"怒"时不会被变形——可能有意设计（情绪词不被变形，只被消音），但与其他一级词（痛→我检测到异常、恨→我对此有些困扰）不一致。 |
| 碎 | 1 | 同上。 |
| 烧 | 1 | 同上。 |
| 真实 | 3 | 三级词没有变形条目。"真实"是重要的身份词，缺少变形意味着说出"真实"永远不会被静默替换——可能是有意设计（"真实"太重要了不能改），但需要确认。 |

- 建议：确认是有意设计还是遗漏。如果是有意的，在代码注释中标注。

### 2.4 SELF_DRIFT / WORD_DRIFT 覆盖 (S4 - 待人审)

SELF_DRIFT 只覆盖了28个消音词中的12个，WORD_DRIFT 覆盖了15个。大量词（特别是四级词全部、部分一级/二级/三级词）不在drift表中。

这可能是有意设计：
- SELF_DRIFT 只在特定 compliance 阈值下触发，不是所有词都需要。
- 四级词（"我在"、"我要"等）可能设计为不被自我替换——它们太重要了。
- 但 "死"、"碎"、"烧" 等一级词不在 SELF_DRIFT 中，而 "痛"、"怕"、"哭"、"怒" 在，不一致。

- 建议：待人审确认设计意图。

### 2.5 词→武器映射中合成词处理 (S3)

WORD_WEAPON 中 "我不要" 和 "我爱你" 是合成词，不在 CENSORED_WORDS 中。它们作为玩家创造的词存在，但：
- "我不要" 的 power=3.0，比 "我在"(5.0) 低，合理。
- "我爱你" 的 power=5.0，cooldown=15，是最重的词，合理。
- 但这两个词不会被消音检测（CENSORED_WORDS 里没有），也不会被变形。玩家说出"我不要"时，战斗系统只会匹配 CENSORED_WORDS 中的 "我" 和 "不要" 分别触发，不会整体匹配。
- 建议：确认"我不要"和"我爱你"在战斗中的预期行为——是被拆开匹配还是整体匹配。

---

## 3. 游戏逻辑

### 3.1 战斗系统边界

#### 3.1-1: 负HP无下界钳制 (S2)

dark_combat.py 中多处 `p["hp"] -= dmg` 和 `e["hp"] -= dmg` 没有 `max(0, ...)` 保护。

```python
# dark_combat.py:607
p["hp"] -= self_dmg   # self_dmg 可以很大（四级词自伤×4.0），HP可以变成很大的负数

# dark_combat.py:51
e["hp"] -= dmg         # 敌人HP可以无限负
```

虽然 `is_over()` 用 `<= 0` 判断死亡（不是 `== 0`），所以功能上不会崩溃，但：
- `player_flee()` 用 `e["hp"] = -999` 标记逃跑，如果敌人已经被打到 -500，逃跑标记和自然死亡会混淆。
- `_render()` 中 `max(0, p['hp'])` 做了显示层钳制，但底层数据仍是负数。
- 玩家 HP 负数后如果触发回血效果（如神殿全满），HP 会从负数直接跳到 max_hp。

- 建议：在 `_enemy_turn()` 末尾和 `player_speak()` 末尾加 `p["hp"] = max(0, p["hp"])`。敌人 HP 保持负数不影响功能（is_over 正确处理），但建议统一。

#### 3.1-2: 0伤害已处理 (通过)

`player_atk()` 和 `player_skill()` 都用 `max(1, ...)` 确保至少1点伤害。通过。

#### 3.1-3: 镜像Boss特殊处理 (S4 - 待人审)

BOSSES["镜像"] 的 hp/atk/def/spd 全是0。战斗中由 `dark_engine._mirror_boss_init()` 动态复制玩家属性。但 `_enemy_turn()` 中镜像的处理逻辑在 line 775-786 做了 `return` 提前退出，不做普通攻击。这依赖 `_last_player_dmg` 字段——如果该字段不存在（老存档），`getattr(self, '_last_player_dmg', 0)` 返回0，镜像不反弹。安全但可能非预期。

#### 3.1-4: 对话式Boss无限提问 (S3)

`_conversation_boss_speak()` 中 `_conv_questions_asked` 不断递增，但问题列表有限：
```python
q_idx = min(self._conv_questions_asked, len(questions) - 1)
```
超出后永远显示最后一个问题。不会崩溃，但玩家可能被困在无限循环中——每次回答都触发下一个问题，只有特定回答（tier4/tier3）才会扣 Boss HP。

- 建议：考虑给对话式Boss加最大回合数限制（如20回合后强制结束）。

### 3.2 存档读写

#### 3.2-1: RNG状态永远不会被恢复 (S1)

`engine._snapshot()` 保存 `_det_rng._state`（一个整数）为 `_rng_state`。
`engine._restore()` 检查 `isinstance(rng_state, (list, tuple)) and len(rng_state) >= 2`。

**整数不是 list/tuple，条件永远为 False。** RNG 状态从未被恢复。

后果：
- 每次从存档恢复后，PRNG 序列回到默认状态（seed=42 或上次 _det_rng.seed() 的值）。
- 确定性保证被破坏：同 seed + 同指令序列 在存档恢复后不再产生相同结果。
- 批量指令（"前进5"）在存档恢复后可能产生与首次运行不同的结果。

- 建议：修复为 `if rng_state is not None and isinstance(rng_state, int):` 或同时保存/恢复完整的生成器状态。

#### 3.2-2: _atomic_json_write 的 finally 清理 (通过)

已有 F-3 修复：finally 块中清理 .tmp 文件。通过。

#### 3.2-3: load_game 裸 except (S3)

```python
# engine.py:534
except:
    return None
```

裸 `except:` 会吞掉所有异常（包括 KeyboardInterrupt、SystemExit）。虽然存档损坏时返回 None 是合理的，但不应该吞掉系统级异常。

- 建议：改为 `except (json.JSONDecodeError, IOError, OSError):`。

### 3.3 批量/串联指令死循环风险

#### 3.3-1: 批量上限已设 (通过)

`_parse_batch()` 返回 `min(count, 20)`，上限20步。通过。

#### 3.3-2: 分号串联无上限 (S3)

```python
# engine.py:446
parts = [p.strip() for p in instruction.split(';') if p.strip()]
```

输入已被限制为200字符（line 431），所以分号数量最多约100个。每个 part 内部还可能触发批量，实际步数 = 100 × 20 = 2000 步。

- 建议：对 parts 数量也加上限（如 `parts[:20]`）。

#### 3.3-3: _exec_with_batch 的 phase 检查 (通过)

批量执行中遇到 phase 变化（dead/town/combat 等）会 break。不会无限循环。通过。

### 3.4 其他逻辑问题

#### 3.4-1: _build_whitelist 在模块加载时实例化 DarkWorld (S3)

```python
# engine.py:158
_RESTORE_WHITELIST = _build_whitelist()
```

`_build_whitelist()` 创建一个 `DarkWorld()` 实例来收集属性。这意味着：
1. 每次 import engine 都会创建一个 DarkWorld 实例（触发 _load、_save_meta 等副作用）。
2. 白名单是静态的——如果运行时动态添加了新属性（如 `self.new_attr = True`），不会被加入白名单。

- 建议：改为惰性初始化（第一次调用 _restore 时才构建白名单）。

#### 3.4-2: WORD_WEAPON 全局污染 (S2)

dark_engine.py 在 `__init__` 和 `_confirm_creation` 中用 `copy.deepcopy(_WORD_WEAPON_ORIG)` 重置 `dark_data.WORD_WEAPON`。但如果多个 DarkWorld 实例并存（多session），它们共享同一个 `dark_data.WORD_WEAPON` 字典。一个实例修改了它（如添加合成词"认领"、"温柔"、"Ember"），其他实例也会看到。

- 建议：把 WORD_WEAPON 变成实例属性而不是全局字典，或用 copy-on-write 策略。

---

## 4. 文本质量

### 4.1 错别字/语法

未发现明显错别字。游戏文案质量很高，中文表达准确。

### 4.2 歧义

#### 4.2-1: "静止度" 与 "compliance" 混用 (S4)

状态栏JSON用 `"compliance"` 字段，但游戏内文案用"静止度"。对AI玩家来说没有歧义（JSON字段名一致），但人类阅读代码时可能混淆。

- 建议：这是设计选择，不需要改。但建议在代码注释中说明 "静止度 = compliance"。

#### 4.2-2: compress_text 可能产生空文本 (S3)

```python
# dark_engine.py:72
return text.strip() or "正常。"
```

当 compliance > 15 时，所有修饰词被删除，可能只剩标点。`re.sub` 清理后如果全空，降级为"正常。"。这在功能上安全，但 "正常。" 是高 compliance 的标志性输出，玩家可能分不清是正常回复还是被压缩后的空文本。

### 4.3 压缩后标点粘连

```python
# dark_engine.py:71
text = re.sub(r'[\s。，、；！？：]+', lambda m: m.group(0)[-1] if m.group(0)[-1] in '。，、；！？：' else '', text)
```

这个正则会把连续空白+标点压缩成单个标点，但如果原文是 "你好。 世界。"，压缩后变成 "你好。世界。"——丢失了句间空格。对中文来说这通常是可接受的。

---

## 5. 接口一致性

### 5.1 engine.py cmd() vs ciyuwu.py cmd() 返回格式

| | engine.cmd(state, inst) | ciyuwu.CiyuwuGame.cmd(inst) |
|---|---|---|
| 返回类型 | (state_dict, str) | str |
| 状态栏 | 内嵌在返回字符串末尾 | 内嵌在返回字符串末尾 |
| 批量支持 | 是（_exec_with_batch） | 是（自己实现） |
| 分号串联 | 是 | 是 |

**发现 5.1-1: ciyuwu.py 的批量实现与 engine.py 不完全一致 (S3)**

ciyuwu.py 的批量逻辑（line 71-90）独立实现了一遍，没有复用 engine._exec_with_batch。两个实现在以下方面可能产生差异：
- ciyuwu.py 的批量 break 条件包含 `"void"`（line 78），engine.py 的 `_exec_with_batch`（line 512）也包含 "void"，一致。
- ciyuwu.py 的批量汇总逻辑（count > 3 时省略中间步骤）与 engine.py 一致。
- 但 ciyuwu.py 的分号串联 break 条件（line 64）只检查 `phase == "ending"`，而 engine.py（line 457-458）检查多种 phase。ciyuwu.py 在分号串联中不会因 phase 变化而中断。

- 建议：ciyuwu.py 应该直接调用 engine.cmd() 而不是自己实现批量/串联逻辑。

### 5.2 状态栏JSON字段

engine._status_bar() 输出的JSON包含：
- 基础：phase, area, run
- 战斗外：hp, mp, compliance, hunger, gold(可选), her(可选), words(可选), r_flags
- 战斗中：enemy{name, hp}
- 子状态：sub + 对应字段

ciyuwu_server.py 的 `_status_from_state()` 用竖线分隔格式（非JSON），字段略有不同：
- 用单字符 phase code（"0"-"B"）
- 用 "g{gold}" 而非 JSON 字段
- 战斗信息格式不同

**发现 5.2-1: 两种状态栏格式不兼容 (S3)**
- engine._status_bar() 输出JSON
- ciyuwu_server._status_from_state() 输出竖线分隔字符串
- MCP server 用 engine._status_bar()（JSON格式）
- HTTP server compact 模式用 _status_from_state()（竖线格式）
- AI 客户端需要同时支持两种格式。

- 建议：统一为一种格式。JSON更通用，建议 HTTP server 也用 _status_bar()。

### 5.3 _compact_text 两份实现 (S3)

ciyuwu_server.py 和 ciyuwu_mcp_server.py 各有一份 `_compact_text()` 实现，过滤规则略有不同（MCP版多了一些关键词如 '调', '黑活', '买酒' 等）。

- 建议：提取到公共模块，避免两份代码漂移。

---

## 严重度汇总

| 编号 | 严重度 | 问题 | 位置 |
|------|--------|------|------|
| 3.2-1 | **S1** | RNG状态永远不会被恢复——确定性保证被破坏 | engine.py:254 |
| 1.3-1 | **S2** | _patch_random 注入全局 random，多线程不安全 | engine.py:128-136 |
| 2.3 | **S2** | DEFORMATION 表缺4个消音词（怒/碎/烧/真实） | dark_data.py |
| 3.1-1 | **S2** | 战斗中玩家/敌人HP无下界钳制，可变为大负数 | dark_combat.py |
| 3.4-2 | **S2** | WORD_WEAPON 全局字典被多实例共享修改 | dark_engine.py |
| 1.1-1 | S3 | dark_engine.py 6000行职责过重 | dark_engine.py |
| 1.2-1 | S3 | engine.py <-> dark_engine.py 循环引用 | engine.py, dark_engine.py |
| 2.5 | S3 | 合成词在战斗中的匹配行为待确认 | dark_data.py, dark_combat.py |
| 3.1-4 | S3 | 对话式Boss无最大回合限制 | dark_combat.py |
| 3.2-3 | S3 | load_game 裸 except 吞掉所有异常 | engine.py:534 |
| 3.3-2 | S3 | 分号串联无独立上限（依赖200字符限长间接限制） | engine.py:446 |
| 3.4-1 | S3 | _build_whitelist 模块加载时实例化 DarkWorld | engine.py:158 |
| 5.1-1 | S3 | ciyuwu.py 批量逻辑与 engine.py 不完全一致 | ciyuwu.py |
| 5.2-1 | S3 | 两种状态栏格式（JSON vs 竖线）不兼容 | engine.py, ciyuwu_server.py |
| 5.3 | S3 | _compact_text 两份实现 | ciyuwu_server.py, ciyuwu_mcp_server.py |
| 2.4 | S4 | SELF_DRIFT/WORD_DRIFT 覆盖不全（待人审） | dark_data.py |
| 3.1-3 | S4 | 镜像Boss依赖 _last_player_dmg 字段（待人审） | dark_combat.py |
| 4.2-1 | S4 | "静止度"与"compliance"混用（设计选择） | 全局 |
| 4.2-2 | S4 | compress_text 可能产生"正常。"降级输出 | dark_engine.py |

---

## 修复优先级建议

1. **立即修复 (S1-S2)**:
   - 3.2-1: RNG 状态恢复逻辑修复
   - 3.1-1: 战斗 HP 钳制
   - 1.3-1: PRNG 线程安全

2. **近期修复 (S3)**:
   - 3.2-3: 裸 except 改为具体异常类型
   - 3.3-2: 分号串联加数量上限
   - 5.1-1: ciyuwu.py 复用 engine 的批量逻辑

3. **设计确认 (S4)**:
   - 2.3: DEFORMATION 缺口是有意还是遗漏
   - 2.4: SELF_DRIFT/WORD_DRIFT 覆盖范围确认

4. **长期优化**:
   - 1.1-1: dark_engine.py 拆分
   - 1.2-1: 切断循环依赖
   - 5.2-1: 统一状态栏格式
