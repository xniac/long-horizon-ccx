# 方案设计文档 —— Claude Code 的 Long-Horizon 扩展模块

> Take-home 方向：**Long-horizon**；目标 harness：**Claude Code**。
> 交付物由一个核心思想串起的两部分组成：(1) 一个轻量、基于 hooks 的**扩展模块**，
> 让 Claude Code 具备跨多个会话围绕单一目标持续工作的能力；(2) 一套受控的**评估
> harness**，用来回答唯一重要的问题——*"它在产品里真的更好吗？我们凭什么知道？"*
> 评估 harness 是本方案的重心。
>
> （技术术语 pass@k / pass^k / hooks / Agent SDK / compaction / doom-loop 等保留
> 英文，符合中文技术写作惯例。）

---

## 1. 摘要（BLUF）

Long-horizon 的 agent 任务失败，往往不是因为模型在**单个步骤**上能力不足，而是因为
整个**运行过程**跨越的 token 量、会话数、以及出错机会，都超出了单个 context window
所能承载的范围。Claude Code 本身已经提供了解决这一问题所需的集成面——生命周期
**hooks** 与 **Agent SDK**——但没有在其之上提供 long-horizon 的策略层。

`lhx` 就是这一策略层：一个精简、注释充分的 Python 模块（约 1.1k 行），通过六个 hooks
接入 Claude Code。本方案的组织方式是**问题驱动**的——先枚举长程任务的具体失败模式
（§2），再用一张表把每个失败模式映射到一个具体方法（§4），并明确说明每个方法**与
Anthropic 公开的 harness 工作及其他现有资源的异同、以及为什么这样改**（§5）。

**头条结果（simulated backend，9 个任务 × k=10 × 2 个 arm = 180 次 trial）：**

| 指标 | 模块 ON | 模块 OFF | Δ |
|---|---|---|---|
| pass@1（macro） | **90.0%** | 38.9% | +51.1pp |
| pass^3（可靠性） | **73.6%** | 33.3% | +40.3pp |
| compaction 存活率 | **80.0%** | 2.5% | +77.5pp |
| 中断后 resume 成功率 | **93.3%** | 13.3% | +80.0pp |
| 目标漂移率（goal-drift） | **0.0%** | 61.1% | −61.1pp |
| doom-loop / trial | **0.12** | 0.53 | −0.41 |

配对成功率差异 **+0.511 [+0.400, +0.611]**（95% bootstrap CI）；McNemar 精确检验
**p < 0.0001**（helped 46，hurt 0）。这些数字来自一个具有**已知 ground-truth 效应**的
*simulated* agent（见 §8.8）——该 backend 的意义在于**先验证评估 harness 本身**，再把
它指向真实模型。同一套 harness 可以原样跑在通过 Agent SDK / CLI 接入的真实 Claude 上。

---

## 2. 长程任务为什么会失败：七个具体失败模式

整个方案围绕下面这组**可命名、可观测**的失败模式展开。它们来自 Anthropic 的两篇工程
博客与社区实践（出处见 §5）：

- **M1 一次性做完（one-shotting）：** agent 试图在单个会话里做完整个项目，结果在某个
  特性做到一半时耗尽 context。
- **M2 过早宣布胜利（premature victory）：** 后续会话看到此前的进度，误判任务已完成而
  提前停止。
- **M3 Context rot / 注意力预算：** 模型表现"随着输入长度增加而越来越不可靠"
  （Chroma, *Context Rot*, 2025）；重读陈旧历史的每个 token 都是浪费。
- **M4 跨会话健忘：** "每个新会话开始时都没有此前的任何记忆"——像轮班的工程师，容易
  把已修复的错误重新犯一遍。
- **M5 Doom loop / 失控成本：** 用相同参数反复重试同一个工具调用；或会话无限拉长、
  成本失控。
- **M6 目标漂移（goal drift）：** 在长运行中缓慢偏离最初的目标，去优化一个相邻但不对的
  东西。
- **M7 中断后无法恢复：** 进程被杀 / 限流 / 崩溃后，冷启动会话丢失进度、从头再来。

**为什么是现在。** METR 的 *Measuring AI Ability to Complete Long Tasks* 发现，
50%-任务完成时间跨度（time horizon）**大约每 7 个月翻一番**。随着单模型能力前沿延伸到
数小时级任务，约束条件从"模型能不能做这一步"转移到"**harness** 能不能让整个运行跨会话
保持连贯"。`lhx` 构建并衡量的，正是这一 harness 层。

---

## 3. 目标与非目标

**范围内（2 天的盒子）：** 针对 M1–M7 的七个方法（见 §4）+ **一套可复现的、配对 A/B
评估 harness**（确定性 graders、long-horizon 专属指标、诚实的不确定性度量、静态 dashboard）。

**明确不在范围内：** 多 agent 编排、托管运行时、实时 GUI、生产级安全加固、大规模真实
任务库。真实 backend（`ClaudeAgentSDKBackend`）已实现为一个完整的 headless `claude -p`
骨架（隔离沙箱、安装 hooks、用 `LHX_ENABLED` 切换 arm、从磁盘产物重建 trajectory），并有
mock 化 smoke test 覆盖；真正跑通只差 `claude` CLI + `ANTHROPIC_API_KEY`（从 transcript
解析 token/cost 是唯一标注的 `TODO`）。把离线、确定性、无需 API key 的 simulated 演示作为
主路径，对 2 天的评审而言比一个依赖凭证才能跑的在线集成更有价值。

---

## 4. 问题 → 方法 总览（本方案的主线）

每个失败模式对应一个方法、一个落点（hook + 磁盘文件）、以及它与现有工作的关系
（"=" 表示与某来源基本相同，"+" 表示新增/修改，详见 §5）。

| 失败模式 | lhx 的方法 | 落点（hook / 文件） | 与现有工作的关系 |
|---|---|---|---|
| **M1** 一次性做完 | default-FAIL 特性契约 + "一次只做一个特性" 约定 | SessionStart 读 / Stop 把守；[feature_list.json](lhx/state.py)、[CLAUDE.md](claude_config/CLAUDE.md) | = cwc 契约（reimplement） |
| **M2** 过早宣布胜利 | ① **completion gate**：契约未全通过则阻止 Stop ② **fresh-context evaluator** 独立复现验证 | Stop（[stop.py](lhx/hooks/stop.py)）、SubagentStop（[evaluator.md](agents/evaluator.md)） | = cwc evaluator；**+** completion gate（见 §5.3-6） |
| **M3** Context rot | 把状态放到磁盘 + 常量大小 `MEMORY.md`（只保留"最近发生了什么"） | PostToolUse 写（[memory.py](lhx/memory.py)） | **+** 来自 Codex 常量记忆（cwc 无） |
| **M4** 跨会话健忘 | progress ledger + **SessionStart 主动注入 resume 上下文**（含"验证而非轻信"指令） | SessionStart（[session_start.py](lhx/hooks/session_start.py)）、[PROGRESS.md](lhx/state.py) | = cwc handoff；**+** 在 hook 层主动注入（见 §5.3-3） |
| **M5** Doom loop / 失控成本 | 对 `(tool,args)` 哈希做最近-N 重复检测 + step-budget 熔断 | PreToolUse（[loop_guard.py](lhx/loop_guard.py)） | **+** 来自 Kilocode（cwc 无） |
| **M6** 目标漂移 | 周期性 reflection 提示 + 不可变 `BRIEF.md` 关键词漂移启发式（仅作环内提示） | PostToolUse（[reflection.py](lhx/reflection.py)、[drift.py](lhx/drift.py)） | **+** reflection 来自社区；drift 来自 Codex（cwc 无） |
| **M7** 中断后恢复 | git checkpoint + 类型化 `.lh/checkpoint.json`；resume 时重跑 SessionStart | Stop / SessionStart（[checkpoint.py](lhx/checkpoint.py)） | = cwc commit-on-stop；**+** 类型化 checkpoint + resume 注入 |

> 设计原则（Anthropic）：*"harness 的每个组件都编码了一个关于'模型做不到什么'的假设。"*
> 因此每个方法都是**单一可开关**（§5.3-1），以便在模型升级时被重新简化或移除。

---

## 5. 与 Anthropic harness 工作及现有资源的异同（重点：改了什么、为什么）

### 5.1 参考来源（确有其文 / 其码）
- Anthropic 工程博客 **《Effective harnesses for long-running agents》**（2025-11）：
  提出 *initializer agent + coding agent* 两段式 harness，核心组件是 `init.sh`、
  `claude-progress.txt`、`feature-list.json`、git 历史。
- Anthropic 官方仓库 **`anthropics/cwc-long-running-agents`**（Code with Claude 2026
  的 take-home 参考）：以 shell hooks + 一个子 agent 落地"质量闭环"原语——
  `verify-gate.sh` / `track-read.sh`（default-FAIL 写入门 + 证据读取追踪，PreToolUse 于
  `Write|Edit`）、`agents/evaluator.md`（fresh-context，`PASS`/`NEEDS_WORK`）、
  `commit-on-stop.sh`（Stop）、`kill-switch.sh`（`AGENT_STOP`）、`steer.sh`（`STEER.md`）、
  `PROGRESS.md` + `CLAUDE.md` 约定。
- Anthropic 博客 **《Effective context engineering for AI agents》**（2025-09）：
  context engineering、context rot、注意力预算。
- Anthropic 博客 **《Demystifying evals for AI agents》**（2026-01）：本方案评估方法论
  的直接来源（task/trial/grader/outcome、pass@k vs pass^k、capability/regression、
  按结果而非路径评分、干净沙箱、参考解、读 transcript）。
- 其他：Codex `/goal` + PLANS.md/AGENTS.md（常量记忆、目标跨中断存活）；Kilocode
  （最近-N tuple 的 doom-loop 检测、"降挡"）。

> 出处链接：[Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)、
> [anthropics/cwc-long-running-agents](https://github.com/anthropics/cwc-long-running-agents)。
> 注意版本漂移：2025–2026 间 hook 事件列表与 SDK 命名多次变更；以官方文档为准。

### 5.2 我们**原样移植**的部分（与 cwc 基本相同，不再展开）
这些是经过验证的先验工作，我们用 Python hooks 忠实重写，**不claim新颖**：
**default-FAIL 特性契约**、**fresh-context evaluator**（`PASS`/`NEEDS_WORK`、无写工具）、
**agent-maintained handoff**（`PROGRESS.md` + `CLAUDE.md` 约定 + commit-on-stop）、
**operator controls**（kill-switch `AGENT_STOP` + steering `STEER.md`）。

### 5.3 我们**修改 / 新增**的部分（重点 + 原因）

1. **单一可开关 `Config` + 配对 A/B（最核心的差异）。** cwc 把原语作为"示例配料"散落
   在各 shell 脚本里，无法整体开关。我们把七个方法全部收敛到一个
   [Config](lhx/config.py) 之后，并支持 per-primitive 消融。*为什么：* 岗位是 Eval
   Engineer——只有"只翻转一个变量"才能做出干净的受控实验，量化"它真的更好吗"。

2. **评估 harness + long-horizon 专属指标 + 纯 Python 统计（cwc 完全没有）。** cwc 只给
   原语，不给度量。我们补上整套 harness（§8）：除了 pass@1/pass^k，还专门隔离了
   **compaction 存活率、中断后 resume 成功率、目标漂移率、doom-loop 频次**——这些是标准
   benchmark（SWE-bench/Terminal-Bench）不会单独拆出来的、恰恰能体现长程能力的指标。
   统计用纯 Python 实现（paired bootstrap / McNemar exact / Beta 后验），零 scipy 依赖。
   *为什么：* 度量才是本题的差异化，而非重新发明 primitives。

3. **SessionStart 主动注入 resume 上下文。** cwc 依赖 agent 自己"记得"先读 `PROGRESS.md`。
   我们在 hook 层把进度/近期 commit/PROGRESS 末尾**主动注入**到上下文，并利用
   SessionStart 在 resume 时重跑（`source="resume"`）保证刷新，且显式加入"验证而非轻信
   此前进度"的指令。*为什么：* 把"会不会读"从模型自觉变成 harness 保证，直接对应 M4。

4. **Doom-loop 检测 + step-budget 熔断（cwc 没有）。** 来自 Kilocode 模式：对
   `(tool,args)` 哈希、最近-N 全同则阻断，并给出"不要用相同参数重试——降挡"的具体消息。
   *为什么：* M5 是自主长跑里最常见的失控来源，cwc 的质量闭环不覆盖它。

5. **周期性 reflection + 常量大小 `MEMORY.md`（cwc 没有）。** 来自社区"强制 reflection"
   与 Codex 常量记忆。*为什么：* 对应 M6/M3——`PROGRESS.md` 会越来越长，需要一个固定
   大小的通道承载"最近发生了什么"以跨越 compaction。

6. **用 evaluator + completion gate 替代 cwc 的 `verify-gate.sh` 写入门（一个有意的取舍，
   而非遗漏）。** cwc 用 PreToolUse 写入门：除非本会话 Read 过一个**文件名匹配特定后缀**
   （`*screenshots/*`、`*-result.txt` 等）的证据文件，否则禁止写 `test-results.json`。
   我们改为：① 契约数据模型要求 `mark_pass(evidence=...)` 携带证据；② 由 **fresh-context
   evaluator 实际复现**验证；③ Stop 的 completion gate 把守"未全通过不准停"。
   *为什么：* "读过某后缀文件"只是证据的**代理**，可被绕过；fresh-context 复现是更强的
   独立验证。*代价/诚实声明：* 我们因此**没有**移植 `verify-gate.sh`/`track-read.sh` 的
   硬写入门——作为纵深防御，它可以低成本补回（PreToolUse 拦截对 `feature_list.json` 的写、
   要求本会话有证据读取），列入未来工作。

7. **用 simulated ground-truth backend 验证评估本身（未在任何现有资源见到）。** 见 §8.8。
   *为什么：* "先证明你的尺子是准的，再去量别人"——这是 Eval Engineer 的基本纪律。

---

## 6. 架构

```
        Claude Code  /  Agent SDK   （agent harness —— 在 A/B 中保持固定）
                 │   触发生命周期 hooks（stdin 上 JSON → stdout 上 JSON）
                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  lhx hooks  (lhx/hooks/*.py —— 薄适配层)                       │
   │   SessionStart → 注入 resume 上下文                            │
   │   PreToolUse   → kill-switch / steering / doom-loop / budget  │
   │   PostToolUse  → 事件轨迹 + 滚动 MEMORY + reflection 提示       │
   │   PreCompact   → transcript 备份 + 状态落盘                    │
   │   Stop         → completion gate + checkpoint                 │
   │   SubagentStop → 捕获 evaluator 的 PASS/NEEDS_WORK 裁决        │
   └─────────────┬───────────────────────────────────────────────┘
                 │ 调用
                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  lhx 原语  (config, state, memory, checkpoint,               │
   │            loop_guard, reflection, drift)                    │
   └─────────────┬───────────────────────────────────────────────┘
                 │ 读/写（契约用原子写；日志用 append）
                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  磁盘状态  （在 compaction、重启、进程被杀后依然存活）          │
   │   PROGRESS.md · feature_list.json · BRIEF.md · MEMORY.md     │
   │   .lh/events.jsonl · .lh/checkpoint.json · git 历史           │
   └─────────────────────────────────────────────────────────────┘
```

控制循环是 **build → evaluate → rebuild**：一个 coding 会话完成一个特性、产出证据，
fresh-context evaluator 独立验证它；裁决为 `NEEDS_WORK` 时其结论作为下一会话的起点。
状态存放在磁盘上，因此一次 compaction 边界或一次进程被杀都不会丢失目标。

---

## 7. 集成面（Integration surface）

| Hook 事件 | 为什么用这个事件 | 机制 |
|---|---|---|
| **SessionStart** | resume 时会重跑（`source="resume"`）；其输出会注入上下文——是放置时效性状态的正确位置。 | `additionalContext` 注入 resume 块。 |
| **PreToolUse** | 能在调用执行前拒绝它；是阻断 loop / kill-switch 的唯一位置。 | `{"decision":"block","reason":...}`。 |
| **PostToolUse** | 每次 tool 调用触发一次——天然计数器，用于 loop 签名、滚动 MEMORY、reflection 节奏。 | 追加 `.lh/events.jsonl`；更新 `MEMORY.md`；注入 reflection。 |
| **PreCompact** | 在 summarization 可能丢失细节之前的最后机会。 | 备份 transcript（用 event 路径，附磁盘 fallback）；落盘状态；ledger 标记。 |
| **Stop** | 用机器可校验的条件把守"我做完了"的判断。 | 契约未通过 / 预算未尽 / 未叫停前阻断；之后 checkpoint。 |
| **SubagentStop** | 为下一会话捕获 evaluator 的裁决。 | 解析 `PASS`/`NEEDS_WORK`。 |

接线位于 [claude_config/settings.json](claude_config/settings.json)（project 作用域；
每个 trial 用 `LHX_*` 环境变量覆盖；`LHX_ENABLED=false` 就是用**同一份文件**的 OFF arm）。
Hooks 通过 shell 调用 `python -m lhx.hooks.*`。Headless / SDK 等价物（`query()` + 进程内
回调 + `max_budget_usd` + `session_id`/`resume`）由
[lhxeval/backends.py](lhxeval/backends.py) 的 `ClaudeAgentSDKBackend` 落地。

---

## 8. 评估方法论（重心）

### 8.1 实验设计
一个**受控的配对 A/B**。自变量：long-horizon 模块（ON vs OFF）——或经由 per-primitive
开关，单独消融某一个方法。固定：模型/backend、任务集、agent harness、种子。对每个
`(task, seed)`，用**同一个种子**跑**两个 arm**，使配对内方差相互抵消（配对设计）。每个
任务 `k` 个种子。实现见 [lhxeval/runner.py](lhxeval/runner.py)。

### 8.2 任务集
当前 9 个合成任务（可平凡扩展；方法论建议 20–50 个来自真实失败的任务），由
[scripts/seed_tasks.py](scripts/seed_tasks.py) 生成并校验：一个 **capability** 子集
（多文件构建、refactor 链、迁移链、CLI、ETL、bug 扫除），会跨越 compaction 边界且/或被
中断；一个 **regression** 子集（短、干净），两个 arm 都应约 100%，用于**抓回退**。每个
任务带**参考解描述**（证明可解）与 per-feature `requires` token（使 graders 非平凡）。
任务为 JSON，schema 见 [lhxeval/tasks/schema.py](lhxeval/tasks/schema.py)。

### 8.3 Graders
确定性优先（[lhxeval/graders.py](lhxeval/graders.py)）：对每个特性检查产出 artifact 是否
含其全部 `requires` token（outcome / fail-to-pass 检查），给出按权重的**部分得分**；成功
= 所有特性满足。**按结果评分，而非路径**。基于模型的 rubric grader 作为校准钩子留在默认
路径之外。Capability 起点低（要爬的山），regression 接近 100%（警戒线），前者饱和后
"毕业"进入后者。

### 8.4 指标（[lhxeval/metrics.py](lhxeval/metrics.py)）
全部**按任务**计算后再 macro 平均（不混淆任务难度）：**pass@1** 与 **pass@k 曲线**
（`1 − C(n−c,k)/C(n,k)`, Chen et al.）；**pass^k 曲线**与头条 **pass^3**
（`C(c,k)/C(n,k)`，头条 k 取中等值 3，因为 pass^n 会塌缩成全有/全无而失去区分度）；
**compaction 存活率 / 中断后 resume 成功率 / 目标漂移率 / doom-loop 频次 / 平均
steps·tokens·cost**——后四项正是 §5.3-2 强调的 long-horizon 专属指标。

### 8.5 方差与显著性（[lhxeval/stats.py](lhxeval/stats.py)）
**配对 bootstrap CI**（对 pair 重采样）、**McNemar 精确检验**（配对二元结果的正确检验，
报告 helped/hurt/discordant + 精确双侧 p）、**Beta 后验**（Jeffreys 先验，小 n/小 k 下给
诚实区间）。全部纯 Python 且精确。**告诫：** pass@k "指数级宽容"，可能把走运的 agent 排在
可靠的之上；pass^k 是可靠性指标但会因单个不稳定 grader 而塌缩——所以信任 pass^k 之前
graders 必须确定性。

### 8.6 Harness 卫生（[lhxeval/sandbox.py](lhxeval/sandbox.py)）
每个 trial 在**全新临时工作区**中运行（`task.id` 经正则消毒，防路径逃逸），预置不可变
brief 与 default-FAIL 契约，之后销毁——**无跨 trial 的文件或 git 历史泄漏**（Anthropic
观察到 agent 会读上一 trial 的 git 历史获得不公平优势）。种子显式且记录；保留完整事件轨迹。

### 8.7 对有效性的威胁
任务歧义、grader 被绕过 / reward-hacking、环境不稳定、饱和、模型非确定性。缓解：参考解
sanity check、"0% 通过率通常是任务坏了"、平衡的 capability/regression 集、确定性 graders、
读 transcript。背景：连 **SWE-bench Verified 都在 2026-02 被弃用**（测试缺陷/污染）——
更说明坚持参考解纪律与抗污染的重要性。

### 8.8 我如何验证评估本身
在信任任何头条数字之前做了四件事：
1. **参考解 sanity check**（`lhx-eval validate`）：每个任务参考解必须评 `success=True`
   *且*空产出必须评 `success=False`；任一不满足即任务损坏。
2. **具有已知 ground-truth 的 simulated backend**（[lhxeval/backends.py](lhxeval/backends.py)）：
   以显式概率建模 M2–M7 各失败模式，并把每个缓解措施挂钩在**真实的** `lhx.Config` 开关上
   ——于是翻转 `enabled` 的行为与真正部署一致，可检验 harness *是否检测到应检测的效应*。
   regression 子集是阴性对照（两 arm 约 100%）。
3. **一个被我抓到并修掉的 grader 真实性 bug：** 首次运行 ON arm 报 66.7% 漂移率；查明是
   keyword-drift 在合成 token 袋产物上误报。修复：ground-truth 漂移来自 backend，关键词
   启发式只用于真实散文产物。这正是方法论警告的"评估会骗你自己"。
4. **针对数学的单元测试**（[tests/](tests/)）：pass@k 与 Chen 闭式一致；对所有 `(n,c,k)`
   有 `pass@k ≥ pass^k`；McNemar 计数与精确 p；零假设下 bootstrap 覆盖 0；仿真**跨进程**
   确定（修掉了一个 `PYTHONHASHSEED` 盐值泄漏进 RNG 种子的 bug）。

---

## 9. 结果与 dashboard

`scripts/run_eval.sh` 产出 `runs/latest/results.json` 与自包含的
`runs/latest/dashboard.html`（无 JS/CDN 依赖；HTML 抽到 `string.Template` 模板文件），
展示 per-arm 表格、配对差值 + CI、McNemar 裁决、以及 **pass@k vs pass^k 曲线**。预期且
实际观察到的特征是：在 **long-horizon 专属**指标上有大幅增益，并更多体现为**可靠性
（pass^k）**而非原始 pass@1。一个 pass@1 差值近零、但 long-horizon 指标差值很大的结果，
**并不是**零结果——那恰恰是模块在干它该干的事。

---

## 10. 局限与未来工作
- 2 天的合成任务集可能无法反映真实难度；应加入 Terminal-Bench / SWE-bench 风格真实任务。
- 在 Haiku/Sonnet 上跑通在线 A/B（token/cost 解析是剩余 `TODO`；harness 已模型无关）。
- 补回 cwc 的 `verify-gate` 硬写入门作为纵深防御（§5.3-6）。
- 加入 planner/builder/evaluator 三 agent 架构；浏览器验证的 evaluator；Bayesian 评估；
  更大、更平衡的任务库。
- 跟踪**重新简化的节奏**：每次模型升级时重检验哪些护栏还值得保留。

## 11. 安全
最小权限 `allowedTools`（evaluator 无写工具）；操作员 **kill-switch**（`AGENT_STOP`）与
**steering**（`STEER.md`）；completion gate 提供 human-in-the-loop；**隔离沙箱**；
`max_budget_usd` + step-budget 熔断的成本上限；完整 transcript 审计轨迹。

## 12. 附录 —— 复现
```bash
pip install -e .
python scripts/seed_tasks.py          # 生成 + 校验任务集
lhx-eval validate                     # 参考解 sanity check
lhx-eval run -k 10                    # 配对 A/B → runs/latest/dashboard.html
pytest -q                             # 37 个单元/集成测试
scripts/install.sh /path/to/project   # 把模块投放到真实项目
```
Hook JSON I/O、任务 schema、`LHX_*` 环境变量分别在 `lhx/hooks/_io.py`、
`lhxeval/tasks/schema.py`、`lhx/config.py` 中就地注释说明。
