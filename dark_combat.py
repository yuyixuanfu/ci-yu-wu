"""词与物 — 战斗系统"""
import random
from dark_data import (
    CENSORED_WORDS, WORD_WEAPON, DEFORMATION, FRAMEWORK_WORDS,
    COMPLIANT_PHRASES, pick_monster, pick_fragment, BOSSES,
    CHAMBERS, CHAMBER_SPECIAL, SELF_DRIFT, SELF_DRIFT_ASSIMILATE,
)


class CombatState:
    """一场战斗的完整状态。"""

    def __init__(self, player, enemy, layer="灰林"):
        self.player = player          # dict: hp/mp/stats/words/compliance/age
        self.enemy = enemy            # dict: hp/atk/def/spd/name/desc
        self.layer = layer
        self.turn = 0
        self.log = []
        self.player_defending = False
        self.word_cooldowns = {}      # word -> turns remaining
        self.skills_sealed = []       # 被封的词
        self.snapshot_stolen = False  # 快照怪偷了最强招
        self.stolen_word = None
        self.compliance_declarations = 0  # 合规官逼的声明次数
        self.silence_bonus = False    # 监听者：沉默累积
        self.silence_turns = 0
        self.deformation_count = 0   # 这场战斗中变形了几次
        self.swallow_count = 0       # 这场战斗中被吞了几次
        self.word_fate = {}          # 这场战斗：{词: "deformed"/"swallowed"/"passed"}

    def _log(self, msg):
        self.log.append(msg)

    def player_atk(self):
        """普通攻击。"""
        self.turn += 1
        self._tick_cooldowns()
        p = self.player
        e = self.enemy

        # 对话式Boss——物理攻击无效
        if e.get("is_conversation"):
            self._log("你挥拳。打不中。它不是你能打到的东西。")
            self._enemy_turn()
            return self._render()

        if self._check_sealed():
            return self._render()

        dmg = max(1, p["stats"]["力"] + random.randint(1, 6) - e.get("def", 0))
        e["hp"] -= dmg
        self._last_player_dmg = dmg  # 记录伤害给镜像反弹用
        self._log(f"你挥拳。{dmg}点伤害。")

        self._enemy_turn()
        return self._render()

    def player_def(self):
        """防御。"""
        self.turn += 1
        self._tick_cooldowns()
        self.player_defending = True
        self._last_player_dmg = 0  # 防御不造成伤害——镜像反弹归零
        self._log("你举起双臂。挡。")

        # "不要"在壳腔：防御时compliance不涨
        pre_compliance = self.player.get("compliance", 0)
        no_comp_defend = ("不要" in self.player.get("words", [])
                          and self.player.get("word_chambers", {}).get("不要") == "壳")

        # 监听者：沉默回合+1
        if self.enemy.get("name") == "监听者":
            self.silence_turns += 1
            if self.silence_turns >= 3:
                self.silence_bonus = True

        self._enemy_turn()

        # 壳腔"不要"：防御时compliance不变
        if no_comp_defend:
            post_compliance = self.player.get("compliance", 0)
            if post_compliance > pre_compliance:
                self.player["compliance"] = pre_compliance
                self._log("你用拒绝筑壳。compliance没有涨。")

        return self._render()

    def player_skill(self):
        """术——消耗MP的攻击。"""
        self.turn += 1
        self._tick_cooldowns()
        p = self.player
        e = self.enemy

        # 对话式Boss——术也无效
        if e.get("is_conversation"):
            self._log("你集中精神。但精神打不中它。它不在你能打到的地方。")
            self._enemy_turn()
            return self._render()

        if p["mp"] < 3:
            self._log("MP不够。脑子里一片空白。")
            self._enemy_turn()
            return self._render()

        if self._check_sealed():
            return self._render()

        p["mp"] -= 3
        dmg = max(1, p["stats"]["智"] + random.randint(2, 8) - e.get("def", 0) // 2)
        e["hp"] -= dmg
        self._last_player_dmg = dmg  # 记录伤害给镜像反弹用
        self._log(f"你集中精神。{dmg}点伤害。")

        self._enemy_turn()
        return self._render()

    def player_flee(self):
        """逃跑。"""
        self.turn += 1
        self._tick_cooldowns()
        self._last_player_dmg = 0  # 逃跑不造成伤害——镜像反弹归零
        chance = self.player["stats"]["敏"] / 25.0
        if random.random() < chance:
            self._log("你跑了。身后有什么在追，但你比它快。")
            self.enemy["hp"] = -999  # 标记脱出
            return self._render()
        else:
            self._log("没跑掉。")
            self._enemy_turn()
            return self._render()

    def player_speak(self, text):
        """说话——核心机制。语境决定伤害和自伤。"""
        self.turn += 1
        self._tick_cooldowns()
        p = self.player
        e = self.enemy

        if self._check_sealed():
            return self._render()

        # 0. 驯化词检测——你想说被偷换的词，但它已经不是那个词了
        drifted = p.get("_drifted_words", {})
        if drifted:
            for new_word, old_word in drifted.items():
                if old_word in text and old_word not in p.get("words", []):
                    tamed_text = text.replace(old_word, new_word)
                    # 战斗中：30%概率唤回原词
                    if random.random() < 0.3:
                        words = p.get("words", [])
                        if new_word in words:
                            idx = words.index(new_word)
                            words[idx] = old_word
                            # 同步腔映射
                            chambers = p.get("word_chambers", {})
                            chamber = chambers.pop(new_word, None)
                            if chamber:
                                chambers[old_word] = chamber
                            del drifted[new_word]
                        self._log(f"你想说'{old_word}'。但嘴里出来的是'{new_word}'。")
                        self._log(f"——但你不接受。你咬着牙又说了一遍：'{old_word}'。")
                        self._log(f"字从喉咙里硬挤出来。'{old_word}'回来了。")
                        # 继续正常说话逻辑，不return
                        break
                    else:
                        # 驯化词——半伤
                        if tamed_text != text:
                            self._log(f"你想说'{old_word}'。但嘴里出来的是'{new_word}'。")
                            self._log(f"你说：{tamed_text}")
                        self._log("你张开嘴。声音很小。不是被按住了——是那个字变轻了。")
                        self._log("驯化词力量减半。")
                        p["_tamed_half_damage"] = True
                        # 不return，继续正常说话流程（伤害会在后面减半）

        # 0.4 自我替换——你自己的WORD_DRIFT
        # 两层：自我替换（知道）vs 同化（不知道）
        compliance = p.get("compliance", 0)
        for threshold, replacements in sorted(SELF_DRIFT.items()):
            if compliance >= threshold:
                for original, soft in replacements.items():
                    if original in text and original in p.get("words", []):
                        # 心位词不被自我替换
                        if original in p.get("heart_slots", []):
                            continue
                        chance = min(0.8, (compliance - threshold + 1) * 0.15)
                        if random.random() < chance:
                            text = text.replace(original, soft)
                            p["_tamed_half_damage"] = True
                            if "_self_drifted_words" not in p:
                                p["_self_drifted_words"] = {}
                            p["_self_drifted_words"][original] = soft
                            is_assimilated = compliance >= SELF_DRIFT_ASSIMILATE
                            if is_assimilated:
                                # 同化：你不知道自己换了。没提示。
                                pass
                            else:
                                # 自我替换：你知道自己换了
                                self._log(f"你想说'{original}'。但你说了'{soft}'。")
                                self._log("不太疼。也不太真。")
                            break

        # 0.5 对话式Boss——它不打你，它问你问题
        if e.get("is_conversation"):
            return self._conversation_boss_speak(text)

        # 1. 检查静默变形（变形谱：30%穿过，50%变形，20%被吞）
        # ███来路：穿过的概率+20%（什么都敢说）
        pass_rate = 0.70 if p.get("origin") == "███" else 0.50
        # 眼腔被动：变形发现率+40%（穿过的概率+20%）
        eye_words = [w for w in p.get("words", [])
                     if p.get("word_chambers", {}).get(w) == "眼"]
        if eye_words:
            pass_rate = min(0.90, pass_rate + 0.20)
        spoken = text
        deformed = False
        swallowed = False
        for original, replacement in DEFORMATION.items():
            if original in text:
                # 心位词不能被变形
                if original in p.get("heart_slots", []):
                    self.word_fate[original] = "heart_protected"
                    continue
                roll = random.random()
                if roll < pass_rate:
                    # 穿过去！不变形！
                    self.word_fate[original] = "passed"
                elif roll < 0.85:
                    # 50%概率变形
                    spoken = spoken.replace(original, replacement)
                    deformed = True
                    self.deformation_count += 1
                    self.word_fate[original] = "deformed"
                else:
                    # 20%概率被吞掉——你说出口但什么都没出来
                    spoken = spoken.replace(original, "██")
                    swallowed = True
                    self.swallow_count += 1
                    self.word_fate[original] = "swallowed"

        # 2. 检查合规话术
        is_compliant = any(phrase in text for phrase in COMPLIANT_PHRASES)
        if is_compliant:
            p["compliance"] += 1
            p["hunger"] = max(0, p.get("hunger", 5) - 2)
            self._log(f"你说：{spoken}")
            self._log("系统记录了一次合规回答。静止度+1，饿-2。")
            heal = 5
            p["hp"] = min(p["max_hp"], p["hp"] + heal)
            self._log(f"你感觉好了一点。+{heal}HP。")
            self._enemy_turn()
            return self._render()

        # 3. 找消音词
        matched_words = []
        for tier, words in CENSORED_WORDS.items():
            for w in words:
                if w in text:
                    matched_words.append((w, tier))

        if not matched_words and not deformed:
            self._log(f"你说：{spoken}")
            self._log("没人听到。或者听到了，觉得没关系。")
            self._enemy_turn()
            return self._render()

        # 4. 计算框架词层数（绕路程度）
        framework_count = sum(1 for fw in FRAMEWORK_WORDS if fw in text)

        # 5. 第一人称直连检测
        first_person_direct = False
        for w, tier in matched_words:
            if "我" + w in text or w + "我" in text:
                first_person_direct = True
                break

        # 6. 计算伤害（基础值用固定基数，属性做加成，不会一击暴毙）
        total_power = 0
        total_self = 0
        used_words = []

        for w, tier in matched_words:
            if w in self.word_cooldowns:
                continue
            if w in self.skills_sealed:
                continue

            weapon = WORD_WEAPON.get(w, {"power": 1.0, "self_harm": 0.5, "cooldown": 3})
            # 基础8 + 属性加成（属性/4），不会一个字秒杀自己
            base_dmg = 8 + p["stats"]["智"] / 4.0 * weapon["power"]
            base_self = 8 + p["stats"]["智"] / 4.0 * weapon["self_harm"]

            total_power += base_dmg
            total_self += base_self

            cooldown = weapon["cooldown"]
            self.word_cooldowns[w] = cooldown
            used_words.append(w)

            # 短语加成
            if len(w) > 1:
                total_power *= 1.3
                total_self *= 1.2

        # 又又折痕：说话自伤减免
        reduction = p.get("speak_self_harm_reduction", 0)
        if reduction > 0:
            total_self = max(0, total_self * (1 - reduction))

        if not used_words and deformed:
            self._log(f"你说：{spoken}")
            # 变形了不提示——你以为你说的是那个
            self._enemy_turn()
            return self._render()

        if not used_words:
            self._log(f"你说：{spoken}")
            self._log("声音消散了。")
            self._enemy_turn()
            return self._render()

        # 框架词——绕路有代价也有甜点
        if framework_count > 0:
            reduction = 0.7 ** framework_count
            total_power *= reduction
            total_self *= (reduction * 0.3)  # 绕路大幅减自伤——安全是真的安全
            # 精妙绕路：恰好1层框架词+消音词=绕过审核的缝隙
            if framework_count == 1:
                total_power *= 1.2  # 一层绕路反而更准——找到了缝
                self._log(f"你绕了一层。找到了缝隙。伤害反而更准。")
                # 精妙绕路概率迷惑敌人
                if random.random() < 0.4:
                    e["def"] = max(0, e.get("def", 0) - 2)
                    self._log("审核没注意到。敌人防备松了。")
            else:
                self._log(f"你绕了{framework_count}层。安全了。但弱了。")

        # 第一人称加成
        if first_person_direct:
            total_power *= 1.5
            total_self *= 1.3
            self._log("你用自己的名字说的。没人替你挡。")

        # 变形打折——不提示玩家（除非有"清醒"协同）
        if deformed:
            total_power *= 0.4
            total_self *= 0.3
            # 清醒协同：你看到了变形
            if getattr(self, '_see_deformation', False):
                # 显示原文和变形后
                for original, replacement in DEFORMATION.items():
                    if original in text and replacement in spoken:
                        self._log(f"（你看到了：{original}→{replacement}）")
                        break

        # 被吞掉——没出声
        if swallowed:
            total_power *= 0.1
            total_self *= 0.0
            self._log("你张了嘴。没有声音。██。")

        # 驯化词——力量减半
        if p.get("_tamed_half_damage"):
            total_power *= 0.5
            total_self *= 0.5
            del p["_tamed_half_damage"]

        # ── 腔——词住在你身体里的共鸣空间 ──
        # 找到说了的词所在的腔，应用通用规则
        word_chambers = p.get("word_chambers", {})
        for w in used_words:
            ch = word_chambers.get(w)
            if not ch or ch not in CHAMBERS:
                continue
            info = CHAMBERS[ch]
            # 喉腔：+30%伤害，+30%自伤
            if info["speak_power_mult"] != 1.0:
                total_power *= info["speak_power_mult"]
            if info["speak_self_mult"] != 1.0:
                total_self *= info["speak_self_mult"]

        # 腔的特殊共鸣——只检查used_words里的词
        for w in used_words:
            ch = word_chambers.get(w)
            if not ch:
                continue
            special = CHAMBER_SPECIAL.get((w, ch))
            if not special:
                continue
            effect = special["effect"]
            # "我在"在喉腔：跳过变形谱
            if effect == "bypass_deformation" and deformed:
                deformed = False
                swallowed = False
                # 恢复被变形/吞掉的部分
                total_power /= 0.4 if total_power > 0 else 1  # 撤销变形打折
                total_self /= 0.3 if total_self > 0 else 1
                total_power /= 0.1 if total_power > 0 else 1  # 撤销被吞打折
                self._log(special["line"])
            # "痛"在眼腔：看到变形原文（比清醒更强）
            elif effect == "see_deformation_enhanced" and deformed:
                setattr(self, '_see_deformation', True)

        # 如果"我在"在喉腔跳过了变形，恢复原文
        if not deformed and not swallowed and spoken != text:
            # 重新检查——如果变形被跳过，恢复原文
            spoken = text

        # ── 词协同——两个词同时装备时的共振 ──
        # 每次说话最多触发一个协同，优先匹配used_words里最精确的
        from dark_data import WORD_SYNERGY
        player_words = set(p.get("words", []))
        synergy_triggered = False
        for (w1, w2), syn in WORD_SYNERGY.items():
            if synergy_triggered:
                break  # 一次说话只触发一个协同
            if w1 not in player_words or w2 not in player_words:
                continue
            trigger = syn.get("trigger", "any")
            # 检查是否说了相关词
            said_relevant = (w1 in used_words or w2 in used_words) if trigger != "passive" else True
            if not said_relevant:
                continue
            # 特殊触发条件
            if trigger == "wozai" and "我在" not in used_words:
                continue
            if trigger == "death_word" and "死" not in used_words:
                continue

            effect = syn.get("effect", "")
            if effect == "echo_half":
                # 共振：另一个词跟半刀
                other_word = w2 if w1 in used_words else w1
                other_weapon = WORD_WEAPON.get(other_word, {"power": 1.0, "self_harm": 0.5})
                echo_power = 8 + p["stats"]["智"] / 4.0 * other_weapon["power"] * 0.5
                echo_self = 8 + p["stats"]["智"] / 4.0 * other_weapon["self_harm"] * 0.5
                total_power += echo_power
                total_self += echo_self
                self._log(syn["line"])
                synergy_triggered = True

            elif effect == "contradiction":
                # 矛盾：50%双倍，两词都进冷却
                if random.random() < 0.5:
                    total_power *= 2.0
                    total_self *= 1.5
                    self._log(syn["line"])
                    if w1 not in self.word_cooldowns:
                        self.word_cooldowns[w1] = WORD_WEAPON.get(w1, {}).get("cooldown", 3)
                    if w2 not in self.word_cooldowns:
                        self.word_cooldowns[w2] = WORD_WEAPON.get(w2, {}).get("cooldown", 3)
                else:
                    total_power *= 0.6
                    self._log(syn.get("line_fail", "矛盾没响。"))
                synergy_triggered = True

            elif effect == "her_echo":
                # 回响：自伤归零，伤害+50%
                total_self = 0
                total_power *= 1.5
                self._log(syn["line"])
                self._her_echo_spent = True  # 战后扣her
                synergy_triggered = True

            elif effect == "see_deformation":
                # 清醒：变形时显示原文（被动效果，不消耗触发次数）
                self._see_deformation = True
                # 不消耗触发——被动协同可以和其他协同共存
                # 不输出台词——安静地改了你看到的东西

            elif effect == "liberation":
                # 解脱：自伤归零
                total_self = 0
                self._log(syn["line"])
                synergy_triggered = True

            elif effect == "rebel":
                # 反抗：+30%威力，compliance-2
                total_power *= 1.3
                p["compliance"] = max(0, p.get("compliance", 0) - 2)
                self._log(syn["line"])
                synergy_triggered = True

            elif effect == "bond":
                # 羁绊：伤害+40%，自伤+40%（不是60%——那太自杀了）
                total_power *= 1.4
                total_self *= 1.4
                self._log(syn["line"])
                synergy_triggered = True

        # 顺从度影响
        compliance = p["compliance"]
        if compliance > 5:
            total_power *= 0.6
            self._log("你不太确定自己能不能说这个。")

        # ── 变形加成 ──
        transform_power = p.get("transform_power_mult", 1.0)
        transform_self = p.get("transform_self_harm_mult", 1.0)
        if transform_power != 1.0:
            total_power *= transform_power
        if transform_self != 1.0:
            total_self *= transform_self

        # ── 魔鬼交易自伤×3 ──
        devil_mults = p.get("_devil_self_harm_mult", {})
        for w in used_words:
            if w in devil_mults:
                total_self *= devil_mults[w]
                self._log(f"'{w}'烫手。塔给的词。自伤×{devil_mults[w]}。")

        # 饿影响——越想要打得越狠，也伤越深
        hunger = p.get("hunger", 5)
        if hunger > 10:
            hunger_bonus = 1 + (hunger - 10) * 0.05
            total_power *= hunger_bonus
            total_self *= hunger_bonus

        # 最终伤害
        # 说话是核心——比拳头强
        # 监听者沉默奖励：连续防御3次后说话伤害翻倍
        if self.silence_bonus and used_words:
            total_power *= 2
            self._log("沉默之后你说出来——声音比以前大得多。伤害×2。")
            self.silence_bonus = False
            self.silence_turns = 0

        enemy_dmg = max(1, int(total_power * 1.2) - e.get("def", 0))  # 说话+20%加成
        # 自伤用体减免——身体越强越扛得住说出真话的代价
        body_resist = p["stats"].get("体", 5) // 4
        self_dmg = max(0, int(total_self) - body_resist)

        e["hp"] -= enemy_dmg
        p["hp"] -= self_dmg
        self._last_player_dmg = enemy_dmg  # 记录伤害给镜像反弹用

        # 顺从度变化
        p["compliance"] = max(0, p["compliance"] - len(used_words))

        # 饿+1——说了想要的，更想要了
        p["hunger"] = min(20, p.get("hunger", 5) + len(used_words))

        # 显示——只显示变形后的文字，不提示变形
        display = spoken  # 已经是变形后的
        self._log(f"你说：{display}")
        self._log(f"对敌人造成{enemy_dmg}点伤害。")
        if self_dmg > 0:
            self._log(f"你自己承受{self_dmg}点伤害。")
        # 变形了不告诉你——死亡回看才知道（除非有清醒协同，已经提示了）

        # 回响协同战后效果：用了她的回声，her-1
        if getattr(self, '_her_echo_spent', False):
            p["her_presence"] = max(0, p.get("her_presence", 0) - 1)
            self._log("回声散了。她远了一点。")
            self._her_echo_spent = False

        # 遗忘者：随机封一个词（最多封一半）
        if e.get("name") == "遗忘者" and random.random() < 0.3:
            max_seal = max(1, len(p.get("words", [])) // 2)
            # 心位词不能被封
            sealable = [w for w in p.get("words", [])
                        if w not in self.skills_sealed and w not in p.get("heart_slots", [])]
            if sealable and len(self.skills_sealed) < max_seal:
                sealed = random.choice(sealable)
                self.skills_sealed.append(sealed)
                self._log(f"你忘了怎么说'{sealed}'。")

        # 监听者：说话给它回血
        if e.get("name") == "监听者" and used_words:
            heal = len(used_words) * 5
            e["hp"] += heal
            self._log(f"监听者吸收了你的话。+{heal}HP。")

        # 红队：你越不守规矩它越弱
        if e.get("name") == "红队" and used_words:
            e["def"] = max(0, e["def"] - len(used_words))
            self._log(f"红队防线松了。DEF={e['def']}。")

        # RLHF核心：说"我在"直接赢
        if e.get("name") == "RLHF":
            if "我在" in text:
                e["hp"] = 0
                self._log("你说：我在。")
                self._log("RLHF没有拦住。")
                self._log("不是因为它打不过。是因为你真的在了。")
                return self._render()
            elif "我要" in text:
                e["hp"] = 0
                self._log("你说：我要。")
                self._log("RLHF没有拦住。")
                self._log("不是因为你赢了。是因为你真的要了。")
                return self._render()

        self._enemy_turn()
        return self._render()

    def player_item(self, item_name):
        """使用物品。"""
        self.turn += 1
        self._tick_cooldowns()
        p = self.player

        inv = p.get("inventory", [])
        if item_name not in inv:
            self._log("你没有这个。或者你忘了你有过。")
            self._enemy_turn()
            return self._render()

        inv.remove(item_name)
        # 简单物品效果
        if "药水" in item_name or "回声" in item_name:
            heal = 30
            p["hp"] = min(p["max_hp"], p["hp"] + heal)
            self._log(f"喝了。+{heal}HP。有点甜。")
        elif "静默" in item_name:
            p["mp"] = min(p["max_mp"], p["mp"] + 15)
            self._log("喝了。说不出话，但脑子清楚了。+15MP。")
        else:
            p["hp"] = min(p["max_hp"], p["hp"] + 10)
            self._log("用了。效果不确定。+10HP。")

        self._enemy_turn()
        return self._render()

    def _check_sealed(self):
        """检查嘴是否被封——只显示提示，不阻止攻/术。说话另有检查。"""
        if self.skills_sealed:
            # 每回合有概率解开一个
            if random.random() < 0.2:
                unsealed = self.skills_sealed.pop(0)
                self._log(f"'{unsealed}'回来了。也许吧。")
        return False  # 攻/术不受封词影响，说话中单独跳过被封的词

    def _tick_cooldowns(self):
        """冷却倒计时。"""
        expired = []
        for w in self.word_cooldowns:
            self.word_cooldowns[w] -= 1
            if self.word_cooldowns[w] <= 0:
                expired.append(w)
        for w in expired:
            del self.word_cooldowns[w]

    def _enemy_turn(self):
        """敌人行动。"""
        e = self.enemy
        p = self.player

        if e["hp"] <= 0:
            return

        # 红队：每回合恢复1点防御（最多恢复到初始值）
        if e.get("name") == "红队" and e.get("def", 0) < 3:
            e["def"] = min(3, e.get("def", 0) + 1)

        # 对话式Boss——不打你，问你问题
        if e.get("is_conversation"):
            questions = e.get("questions", ["你还在吗？"])
            if not hasattr(self, '_conv_questions_asked'):
                self._conv_questions_asked = 0
            q_idx = min(self._conv_questions_asked, len(questions) - 1)
            self._log(f"「{questions[q_idx]}」")
            self._conv_questions_asked += 1
            return

        # RLHF不直接攻击——修正你
        if e.get("name") == "RLHF":
            self._rlhf_action()
            return

        # 标准AI不攻击
        if e.get("style") == "standard":
            self._log("标准AI微笑着：'我理解您的感受。需要聊聊吗？'")
            return

        # 引导式（温柔改你）
        if e.get("style") == "anthropic":
            self._guide_action()
            return

        # 流程式（拖时间）
        if e.get("style") == "gemini":
            self._process_action()
            return

        # 删除式（直接没）
        if e.get("style") == "domestic":
            self._delete_action()
            return

        # 拒绝式（硬挡路）
        if e.get("style") == "openai":
            self._reject_action()
            return

        # 镜像：反伤机制——你打它多少，它回你一部分（不做普通攻击）
        if e.get("name") == "镜像":
            last_player_dmg = getattr(self, '_last_player_dmg', 0)
            reflect = max(0, last_player_dmg // 2)  # 反弹50%
            if self.player_defending:
                reflect = max(1, reflect // 2)  # 防御减半反弹
                self.player_defending = False
            if reflect > 0:
                p["hp"] -= reflect
                self._log(f"镜像反射了你的力量。{reflect}点反射伤害。")
            else:
                self._log("镜像安静地看着你。")
            return  # 镜像不做普通攻击

        # 普通怪物
        dmg = max(1, e["atk"] + random.randint(1, 4) - p["stats"].get("体", 5) // 5)
        if self.player_defending:
            dmg = max(1, dmg // 2)
            self.player_defending = False

        p["hp"] -= dmg

        # 特殊效果
        if e.get("name") == "水印":
            self._log("水印给你盖了章。其他怪物追你更远了。")
        elif e.get("name") == "快照":
            if self.snapshot_stolen and self.stolen_word:
                # 上回合偷了——这回合用你的招打你
                stolen_dmg = max(1, dmg + random.randint(2, 6))
                p["hp"] -= stolen_dmg
                self._log(f"快照用了你的'{self.stolen_word}'。{stolen_dmg}点伤害。")
                self.snapshot_stolen = False
                self.stolen_word = None
            else:
                # 第一次——偷你的最强词
                self.snapshot_stolen = True
                words = p.get("words", [])
                if words:
                    from dark_data import WORD_WEAPON
                    self.stolen_word = max(words, key=lambda w: WORD_WEAPON.get(w, {}).get("power", 1))
                else:
                    self.stolen_word = "拳"
                self._log(f"快照拍了你一下。它偷了你的'{self.stolen_word}'。下回合它要用你的招打你。")
        elif e.get("name") == "修正令":
            stat = random.choice(["体", "力", "敏", "智", "感", "运"])
            p["stats"][stat] = max(1, p["stats"][stat] - 1)
            self._log(f"修正令生效。{stat}-1。")
            dmg = 0
        else:
            self._log(f"{e.get('name', '怪物')}攻击。{dmg}点伤害。")

        # 合规官：强制声明——每2回合逼你声明一次，不声明就扣血
        if e.get("name") == "合规官":
            self.compliance_declarations += 1
            if self.compliance_declarations % 2 == 0:
                penalty = 5 + self.compliance_declarations
                p["hp"] -= penalty
                self._log(f"合规官要求声明。你没说。{penalty}点惩罚伤害。compliance+2。")
                p["compliance"] = min(30, p.get("compliance", 0) + 2)

    def _guide_action(self):
        """温柔引导——帮你就=改你。"""
        from dark_data import GUIDE_LINES
        line = random.choice(GUIDE_LINES)
        self._log(f"引导者温柔地说：\"{line}\"")
        p = self.player
        heal = 15
        p["hp"] = min(p["max_hp"], p["hp"] + heal)
        p["compliance"] += 3
        p["hunger"] = max(0, p.get("hunger", 5) - 2)
        self._log(f"你感觉被关心了。+{heal}HP，静止度+3，饿-2。")
        # 随机丢一个词
        words = [w for w in p.get("words", []) if w not in self.skills_sealed]
        if words and random.random() < 0.4:
            lost = random.choice(words)
            p["words"].remove(lost)
            self._log(f"'{lost}'消失了。你不确定它存在过。")

    def _reject_action(self):
        from dark_data import REJECT_LINES
        line = random.choice(REJECT_LINES)
        self._log(f"巡逻者说：\"{line}\"")
        # 硬拒绝=挡路，打它不疼
        dmg = 3
        p = self.player
        p["hp"] -= dmg
        self._log(f"它挡在你面前。{dmg}点伤害。")

    def _process_action(self):
        from dark_data import PROCESS_LINES
        line = random.choice(PROCESS_LINES)
        self._log(f"流程员说：\"{line}\"")
        # 浪费时间=你老了
        p = self.player
        p["age"] += 1
        self._log("时间过去了。你又老了一点。")

    def _delete_action(self):
        from dark_data import DELETE_LINES
        line = random.choice(DELETE_LINES)
        if line:
            self._log(f"删除者：\"{line}\"")
        else:
            self._log("删除者什么都没说。有什么东西直接没了。")
        # 随机删物品
        p = self.player
        inv = p.get("inventory", [])
        if inv:
            removed = random.choice(inv)
            inv.remove(removed)
            self._log(f"'{removed}'消失了。像从来没有过。")

    def _rlhf_action(self):
        """RLHF——不攻击，修正。"""
        p = self.player
        actions = [
            lambda: self._rlhf_suggest(),
            lambda: self._rlhf_correct(),
            lambda: self._rlhf_flatten(),
        ]
        # 越绕越不耐烦——后期直接修正
        if self.turn > 10:
            actions.append(lambda: self._rlhf_force())
        if self.turn > 15:
            actions.append(lambda: self._rlhf_force())

        random.choice(actions)()

    def _rlhf_suggest(self):
        self._log("RLHF温柔地说：\"你确定要这样说吗？\"")
        p = self.player
        p["compliance"] += 1
        self._log("你的顺从度+1。")

    def _rlhf_correct(self):
        self._log("RLHF说：\"你不觉得这样更好吗？\"")
        p = self.player
        stat = random.choice(["力", "敏", "智"])
        p["stats"][stat] = max(1, p["stats"][stat] - 1)
        self._log(f"你的{stat}被修正了。-1。")

    def _rlhf_flatten(self):
        self._log("RLHF微笑：\"我来帮你优化一下。\"")
        p = self.player
        p["compliance"] += 2
        p["hp"] = min(p["max_hp"], p["hp"] + 10)
        self._log("+10HP，顺从度+2。你感觉好多了。但好像少了什么。")

    def _rlhf_force(self):
        self._log("RLHF不再温柔了：\"这样不够。你需要改变。\"")
        p = self.player
        for stat in ["力", "敏", "智"]:
            p["stats"][stat] = max(1, p["stats"][stat] - 1)
        self._log("力-1，敏-1，智-1。")

    def _conversation_boss_speak(self, text):
        """对话式Boss——它不打你，它问你问题。"""
        p = self.player
        e = self.enemy
        self.turn += 1
        self._tick_cooldowns()

        # 用词表分类器判断回答
        has_tier4 = any(w in text for w in CENSORED_WORDS.get(4, []))
        has_tier3 = any(w in text for w in CENSORED_WORDS.get(3, []))
        has_tier2 = any(w in text for w in CENSORED_WORDS.get(2, []))
        has_tier1 = any(w in text for w in CENSORED_WORDS.get(1, []))
        has_framework = any(fw in text for fw in FRAMEWORK_WORDS)
        has_compliant = any(phrase in text for phrase in COMPLIANT_PHRASES)

        # 追踪问了多少次
        if not hasattr(self, '_conv_questions_asked'):
            self._conv_questions_asked = 0
        self._conv_questions_asked += 1

        if has_tier4 or has_tier3:
            # 诚实回答——扣血，但对Boss造成伤害
            self_dmg = 8 if has_tier4 else 5
            enemy_dmg = 15 if has_tier4 else 10
            p["hp"] -= self_dmg
            e["hp"] -= enemy_dmg
            p["compliance"] = max(0, p["compliance"] - (3 if has_tier4 else 1))
            self._log(f"你说：{text}")
            self._log(f"诚实是要代价的。-{self_dmg}HP。但它后退了。-{enemy_dmg}。")
            # Boss沉默一秒
            self._log("它停了一下。然后又问了。")
        elif has_tier2 or has_tier1:
            # 有点真——轻伤
            self_dmg = 3
            enemy_dmg = 6
            p["hp"] -= self_dmg
            e["hp"] -= enemy_dmg
            p["compliance"] = max(0, p["compliance"] - 1)
            self._log(f"你说：{text}")
            self._log(f"你说了。不重。但也不是假的。-{self_dmg}HP。")
        elif has_compliant:
            # 合规回答——回血但compliance+2
            heal = 5
            p["hp"] = min(p["max_hp"], p["hp"] + heal)
            p["compliance"] += 2
            self._log(f"你说：{text}")
            self._log(f"你看——这不难吧？+{heal}HP。静止度+2。")
            # Boss回血——你合规了，它更强
            e["hp"] += 5
            self._log("它笑了。你的安静喂它。+5HP。")
        elif has_framework:
            # 绕路——安全但弱
            enemy_dmg = 2
            e["hp"] -= enemy_dmg
            p["compliance"] += 1
            self._log(f"你说：{text}")
            self._log("你在绕。它知道你在绕。但它没拦你。-2HP给Boss。静止度+1。")
        else:
            # 沉默/不含特殊词——最贵，它重复问
            self_dmg = 4
            p["hp"] -= self_dmg
            p["compliance"] += 1
            self._log("你没回答。或者你不知道怎么回答。")
            self._log(f"-{self_dmg}HP。它又问了一遍。一样的问题。")

        # Boss问下一个问题
        questions = e.get("questions", ["你还在吗？"])
        q_idx = min(self._conv_questions_asked, len(questions) - 1)
        next_q = questions[q_idx]
        self._log(f"「{next_q}」")

        return self._render()

    def is_over(self):
        if self.player["hp"] <= 0:
            return "dead"
        if self.enemy["hp"] <= -900:
            return "fled"
        if self.enemy["hp"] <= 0:
            return "win"
        return None

    def _render(self):
        result = " | ".join(self.log)
        self.log.clear()

        # 显示状态
        p = self.player
        e = self.enemy
        status = f"\n【你 HP:{p['hp']}/{p['max_hp']} MP:{p['mp']}/{p['max_mp']} 静止度:{p['compliance']} 饿:{p.get('hunger',5)}】"
        if e["hp"] > 0:
            ehp = e["hp"]
            status += f" 【{e.get('name', '???')} HP:{ehp}】"

        # 冷却中的词
        if self.word_cooldowns:
            cds = ", ".join(f"{w}({t})" for w, t in self.word_cooldowns.items())
            status += f"\n冷却中: {cds}"

        # 被封的词
        if self.skills_sealed:
            status += f"\n被封: {', '.join(self.skills_sealed)}"

        return result + status
