# 方案设计文档 —— Claude Code 的 Long-Horizon 扩展模块

> Take-home 方向：**Long-horizon**；目标 harness：**Claude Code**。
> 交付物由一个核心思想串起的两部分组成：(1) 一个轻量、基于 hooks 的**扩展模块**，
> 让 Claude Code 具备跨多个会话围绕单一目标持续工作的能力；(2) 一套受控的**评估
> harness**，用来回答唯一重要的问题——*"它在产品里真的更好吗？我们凭什么知道？"*
> 评估 harness 是本方案的重心。
>
> （英文版见 [DESIGN.en.md](DESIGN.en.md)。技术术语 pass@k / pass^k / hooks /
> Agent SDK / compaction / doom-loop 等保留英文，符合中文技术写作惯例。）

---

## 1. 摘要（BLUF）

Long-horizon 的 agent 任务失败，往往不是因为模型在**单个步骤**上能力不足，而是因为
整个**运行过程**跨越的 token 量、会话数、以及出错机会，都超出了单个 context window
所能承载的范围。Claude Code 本身已经提供了解决这一问题所需的集成面——生命周期
**hooks** 与 **Agent SDK**——但没有在其之上提供 long-horizon 的策略层。

`lhx` 就是这一策略层：一个精简、注释充分的 Python 模块（约 1.1k 行），通过六个 hooks
接入 Claude Code。它实现了 Anthropic 提出的三个 long-horizon 原语——**结构化笔记**
（progress ledger + default-FAIL 的特性契约）、**checkpoint/resume**、以及一个
**fresh-context evaluator** 子 agent——并补充了两个用于自主运行的护栏：
**doom-loop 检测器**、**周期性 reflection** 提示，以及一个对抗"过早宣布胜利"的
**completion gate**。

每一个原语都可以从单一 `Config` 独立开关，这正是让实验保持干净的关键：评估 harness
运行一个**配对（paired）A/B**，只翻转一个变量（模块 ON vs OFF），而保持模型、任务集、
agent harness 与随机种子全部固定。

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
**p < 0.0001**（helped 46，hurt 0）。这些数字来自一个具有**已知 ground-truth 效应**
的 *simulated* agent（见 §8.8）——该 backend 的意义在于**先验证评估 harness 本身**，
再把它指向真实模型。同一套 harness 可以原样跑在通过 Agent SDK 接入的真实 Claude 上。

---

## 2. 问题与动机

一个 long-horizon 的 agent 运行，相比一次性（one-shot）prompt，存在三类结构性问题：

1. **跨会话没有记忆。** Agent "在离散的会话中工作，每个新会话开始时都没有此前的任何
   记忆"——就像轮班的工程师。开箱即用时这会导致两种失败模式（Anthropic，*Effective
   harnesses for long-running agents*）：(a) 试图一次性做完整个任务，结果在某个特性
   做到一半时耗尽 context；(b) 后续会话看到此前的进度后**过早宣布胜利**。
2. **Context rot / 注意力预算。** 模型表现"随着输入长度增加而越来越不可靠"
   （Chroma，*Context Rot*，2025）。每一个花在重读陈旧历史上的 token，都是没有花在
   任务上的 token。Context engineering——"能最大化目标达成概率的、尽可能小的高信号
   token 集合"——是核心学科。
3. **多步骤上的误差累积。** Doom loop（相同调用反复重试）、目标漂移、以及健忘
   （把已修复的错误重新犯一遍），每一个都有一个很小的单步概率，但在长运行中会累积到
   近乎必然发生。

**为什么是现在。** METR 的 *Measuring AI Ability to Complete Long Tasks* 发现，
50%-任务完成时间跨度（time horizon）**大约每 7 个月翻一番**。随着单模型能力前沿延伸
到数小时级任务，约束条件从"模型能不能做这一步"转移到"**harness** 能不能让整个运行跨
会话保持连贯"。`lhx` 构建的正是这一 harness 层，本评估衡量的也正是它。

---

## 3. 目标与非目标

**范围内（2 天的盒子）：**
- Progress ledger + default-FAIL 特性契约（外部记忆）。
- Checkpoint/resume（git + 类型化 checkpoint）与 resume 上下文注入。
- Doom-loop 检测器 + step-budget 熔断器。
- 周期性强制 reflection。
- Completion gate（对抗过早宣布胜利）。
- Fresh-context evaluator 子 agent。
- **一套可复现的、配对 A/B 评估 harness**，含确定性 graders、long-horizon 专属指标、
  诚实的不确定性度量，以及一个静态 dashboard。

**明确不在范围内：** 多 agent 编排、托管运行时、实时 GUI、生产级安全加固，以及大规模
/真实的任务库。真实 Agent-SDK backend 的运行循环被**留在一个真实的接缝处**（见 §7）
而非完全打通——因为对于 2 天的评审而言，一个离线、确定性、无需 API key 的演示，比一个
半成品的在线集成更有价值。

---

## 4. 背景与已有工作

| 来源 | 我们借鉴了什么 |
|---|---|
| Anthropic，*Effective context engineering* / *Effective harnesses for long-running agents* | 三个原语（compaction、note-taking、sub-agents）；initializer/coding-agent 的轮班模型；"一次只做一个特性"；频繁 commit。 |
| Anthropic，`cwc-long-running-agents` | **default-FAIL 契约**、发出 `PASS`/`NEEDS_WORK` 的 **fresh-context evaluator**、通过 `CLAUDE.md` 维护的交接（handoff）、kill-switch + steering 操作员控制。 |
| Anthropic，*Demystifying evals for AI agents* | 整套评估方法论：task/trial/grader/outcome 术语、pass@k vs pass^k、capability-vs-regression 划分、按结果而非路径评分、每 trial 干净沙箱、参考解、阅读 transcript。 |
| Codex `/goal`、PLANS.md/AGENTS.md | 常量大小记忆（不可变 `BRIEF.md` + 受限 `MEMORY.md`）；目标状态在中断后存活。 |
| Kilocode / 社区 | 最近 N 个 tuple 的 doom-loop 检测；反复失败时"降挡（drop a gear）"。 |
| METR / SWE-bench / Terminal-Bench / τ-bench | Long-horizon 指标与 pass^k 可靠性框架。 |

**真正新颖的地方**不是任何单个原语，而是：把它们打包成**可独立开关**的策略并置于单一
config 之后；并配上一套**为 A/B 而设计的评估 harness**，其中包含标准 benchmark 不会
单独隔离出来的 long-horizon 专属指标（compaction 存活率、中断后 resume 成功率）；以及
**在信任评估结果之前，先用一个 simulated ground-truth 来验证评估本身**的纪律。

---

## 5. 架构

```
        Claude Code  /  Agent SDK   （agent harness —— 在 A/B 中保持固定）
                 │   触发生命周期 hooks（stdin 上 JSON → stdout 上 JSON）
                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  lhx hooks  (lhx/hooks/*.py —— 薄适配层)                       │
   │   SessionStart → 注入 resume 上下文                            │
   │   PreToolUse   → kill-switch / steering / doom-loop / budget  │
   │   PostToolUse  → 事件轨迹 + reflection 提示                    │
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
                 │ 读/写（原子）
                 ▼
   ┌─────────────────────────────────────────────────────────────┐
   │  磁盘状态  （在 compaction、重启、进程被杀后依然存活）          │
   │   PROGRESS.md · feature_list.json · BRIEF.md · MEMORY.md     │
   │   .lh/events.jsonl · .lh/checkpoint.json · git 历史           │
   └─────────────────────────────────────────────────────────────┘
```

控制循环是 **build → evaluate → rebuild**：一个 coding 会话完成一个特性、产出证据，
fresh-context evaluator 独立验证它；当裁决为 `NEEDS_WORK` 时，其结论作为下一个会话的
起点。状态存放在磁盘上，因此一次 compaction 边界或一次进程被杀都不会丢失目标。

**设计原则（Anthropic）：** *"harness 的每一个组件都编码了一个关于'模型做不到什么'的
假设。"* Doom-loop 护栏假设模型会卡住；completion gate 假设它会过早宣布胜利。每一个都
是一行可开关的配置——正是为了在**模型升级时能被重新简化或移除**，而不影响其余部分。

---

## 6. 模块组件

每个原语：*它做什么 · 挂在哪个 hook · 写什么到磁盘 · 解决哪个失败模式 · 来源。*

**(a) Progress ledger + default-FAIL 契约** —— [lhx/state.py](lhx/state.py)。
`feature_list.json` 列出每个特性，初始 `passes: false`；一个特性只有在**有证据**时才会
翻为 `true`，绝不靠自我声明。`PROGRESS.md` 是人类可读的交接文。*Hook：* SessionStart
（读）、Stop（gate）、PostToolUse（事件轨迹）。*解决：* 一次性做完、健忘。采用 JSON
（而非 Markdown）是因为模型不太会去不当地改写它。*来源：* `cwc-long-running-agents`、
*Effective harnesses*。

**(b) Checkpoint/resume** —— [lhx/checkpoint.py](lhx/checkpoint.py)。会话结束时
`git commit -a`（只提交被跟踪的改动，把临时产物挡在历史之外），外加一个类型化的
`.lh/checkpoint.json`。`resume_context()` 构造 SessionStart 注入块，让全新会话重新定位：
特性进度、近期 commit、PROGRESS 末尾、以及一句明确的*"验证此前进度，别轻信它"*指令。
*Hook：* SessionStart（resume 时重跑）、Stop。*解决：* 跨会话无记忆、中断恢复。
*来源：* *Effective harnesses*、LangGraph checkpointers、Codex `/goal`。

**(c) Doom-loop 检测器 + 熔断器** —— [lhx/loop_guard.py](lhx/loop_guard.py)。
对 `(tool, args)` 做哈希；当最近 *window* 次调用完全相同时阻断，并给出明确的
*"不要用相同参数重试——降挡"*消息；`step_budget` 硬性上限约束失控会话。*Hook：*
PreToolUse（通过 `{"decision":"block"}` 阻断）。*解决：* doom loop、失控成本。
*来源：* Kilocode 模式。

**(d) 周期性 reflection** —— [lhx/reflection.py](lhx/reflection.py)。每
`reflection_interval` 次 tool 调用，注入"重读目标；已验证了什么；什么在阻塞你；当前
路线是否奏效"。*Hook：* PostToolUse。*解决：* 缓慢的目标漂移、视野收窄。
*来源：* 社区"强制 reflection"。

**(e) 目标漂移检查** —— [lhx/drift.py](lhx/drift.py)。权威信号是确定性契约；另有一个
针对不可变 `BRIEF.md` 的廉价关键词启发式，作为环内提示（仅用于真实文本产物——见 §8.8）。
*解决：* 目标漂移。*来源：* Codex 常量大小记忆。

**(f) Fresh-context evaluator** —— [agents/evaluator.md](agents/evaluator.md)。
一个仅持有 `Read, Glob, Grep, Bash`（无写工具）的子 agent，从一个**从未见过构建过程**
的上下文中审查产物，并**必须**以 `PASS` 或 `NEEDS_WORK` 作为回复的第一行。*Hook：*
SubagentStop 把裁决解析进 ledger。*解决：* 过早宣布胜利、对自报结果的 reward-hacking。
*来源：* `cwc-long-running-agents`。

---

## 7. 集成面（Integration surface）

| Hook 事件 | 为什么用这个事件 | 机制 |
|---|---|---|
| **SessionStart** | resume 时会重跑（`source="resume"`）；其输出会注入上下文——是放置时效性状态的正确位置。 | `additionalContext` 注入 resume 块。 |
| **PreToolUse** | 能在调用执行前拒绝它；是阻断 loop / kill-switch 的唯一位置。 | `{"decision":"block","reason":...}`。 |
| **PostToolUse** | 每次 tool 调用触发一次——天然的计数器，用于 loop 签名与 reflection 节奏。 | 追加 `.lh/events.jsonl`；注入 reflection。 |
| **PreCompact** | 在 summarization 可能丢失细节之前的最后机会。 | 备份 transcript；落盘状态；ledger 标记。 |
| **Stop** | 用一个机器可校验的条件来把守"我做完了"的判断。 | 在契约通过 / 预算耗尽 / 操作员叫停之前阻断；之后 checkpoint。 |
| **SubagentStop** | 为下一个会话捕获 evaluator 的裁决。 | 解析 `PASS`/`NEEDS_WORK`。 |

接线位于 [claude_config/settings.json](claude_config/settings.json)（project 作用域；
每个 trial 用 `LHX_*` 环境变量覆盖；`LHX_ENABLED=false` 就是用**同一份文件**的 OFF arm）。
Hooks 通过 shell 调用 `python -m lhx.hooks.*`。

**Headless / SDK 等价物。** 同样的逻辑可经 Agent SDK 在进程内触达：`query()` 配
`permissionMode`、`allowedTools`、进程内 `PreToolUse`/`Stop` 回调、可编程子 agent、
`max_budget_usd`、以及 `session_id`/`resume`。`lhxeval/backends.py::ClaudeAgentSDKBackend`
标出了 `run()` 驱动 `query()` 并从消息流 + `.lh/events.jsonl` 重建 trajectory 的确切接缝。

---

## 8. 评估方法论（重心）

### 8.1 实验设计
一个**受控的配对 A/B**。自变量：long-horizon 模块（ON vs OFF）——或经由 per-primitive
开关，单独消融某一个原语。固定：模型/backend、任务集、agent harness、种子。对每个
`(task, seed)`，我们用**同一个种子**跑**两个 arm**，于是配对内的方差相互抵消（配对设计）。
每个任务 `k` 个种子。实现见 [lhxeval/runner.py](lhxeval/runner.py)。

### 8.2 任务集
当前 9 个合成任务（可平凡扩展；方法论建议 20–50 个来自真实失败的任务），由
[scripts/seed_tasks.py](scripts/seed_tasks.py) 生成并校验：
- 一个 **capability** 子集（多文件构建、refactor 链、迁移链、CLI、ETL、bug 扫除），会
  跨越 compaction 边界且/或被中断——模块应当在这里带来帮助；以及
- 一个 **regression** 子集（短、干净），两个 arm 都应跑到约 100%，其存在是为了**抓回退**。

每个任务携带一个**参考解描述**（证明可解性）以及 per-feature 的 `requires` token，
使 graders 非平凡。任务为 JSON，schema 见 [lhxeval/tasks/schema.py](lhxeval/tasks/schema.py)。

### 8.3 Graders
确定性优先（[lhxeval/graders.py](lhxeval/graders.py)）：对每个特性，检查产出的 artifact
是否包含其全部 `requires` token（一种 outcome / fail-to-pass 检查），给出按权重的
**部分得分（partial credit）**；成功 = 所有特性都满足。我们**按结果评分，而非路径**——
不做僵硬的 tool 顺序检查。一个基于模型的 rubric grader 作为校准钩子留在默认路径之外。
Capability 任务起点低（一座要爬的山）；regression 任务接近 100%（一根警戒线）——
capability 任务在饱和后"毕业"进入 regression。

### 8.4 指标（[lhxeval/metrics.py](lhxeval/metrics.py)）
全部**按任务**计算后再 macro 平均（这样不会把任务难度混在一起）：
- **pass@1** 与 **pass@k 曲线** —— `1 − C(n−c,k)/C(n,k)`（Chen et al.）。
- **pass^k 曲线** 与头条 **pass^3** —— `C(c,k)/C(n,k)`；可靠性数字。头条 k 取中等值
  （3），因为 pass^n 会塌缩成每个任务的全有或全无，从而失去区分度。
- **compaction 存活率** —— 在被强制跨越 ≥1 个 compaction 边界的 trial 中的成功率。
- **中断后 resume 成功率** —— 在任务中途被杀掉的 trial 中的成功率。
- **目标漂移率** —— 产出偏离不可变 brief 的比例。
- **doom-loop / trial**、**平均 steps**、**平均 tokens**、**平均 cost**。

### 8.5 方差与显著性（[lhxeval/stats.py](lhxeval/stats.py)）
- **配对 bootstrap CI**，作用于每对差值的均值（对 pair 重采样）。
- **McNemar 精确检验**，作用于配对的 pass/fail 表（配对二元结果的正确检验；报告
  helped/hurt/discordant + 精确双侧 p）。
- **Beta 后验**（Jeffreys 先验）用于单一比率——在真实评估集所处的小 n / 小 k 区间给出
  诚实的区间。
全部为纯 Python 且精确（无正态近似），因为小样本集会让渐近近似产生误导。**我们在文档里
编码的告诫：** pass@k "指数级地宽容"，可能把一个走运的 agent 排在一个可靠的 agent 之上；
pass^k 是可靠性指标，但会因单个不稳定的 grader 而塌缩——所以在信任 pass^k 之前，
graders 必须是确定性的。

### 8.6 Harness 卫生（[lhxeval/sandbox.py](lhxeval/sandbox.py)）
每个 trial 在一个**全新的临时工作区**中运行，预置不可变 brief 与 default-FAIL 契约，
之后销毁——**不存在跨 trial 的文件或 git 历史泄漏**（Anthropic 观察到 agent 会通过读取
上一 trial 的 git 历史获得不公平优势）。种子是显式且被记录的；保留完整事件轨迹。

### 8.7 对有效性的威胁
任务歧义、grader 被绕过 / reward-hacking、环境不稳定、饱和、模型非确定性。缓解：
参考解 sanity check、"0% 通过率通常是任务坏了"的启发式、平衡的 capability/regression
集、确定性 graders、以及阅读 transcript。作为背景我们指出：连 **SWE-bench Verified 都
在 2026 年 2 月被弃用**，原因是测试缺陷 / 污染问题——这进一步说明在任何评估集中坚持
参考解纪律与抗污染的重要性。

### 8.8 我如何验证评估本身
这才是一个 Eval Engineer 真正被考核的部分。在信任任何一个头条数字之前，我做了四件事：
1. **参考解 sanity check**（`lhx-eval validate`）：每个任务的参考解必须评为
   `success=True`，*并且*空产出必须评为 `success=False`。任何一项不满足，这个任务就是
   坏的，而非有信息量的。
2. **一个具有已知 ground-truth 的 simulated backend**
   （[lhxeval/backends.py](lhxeval/backends.py)）。它以显式概率建模那些被记录在案的
   失败模式（compaction 健忘、冷重启丢失、doom loop、漂移），并且——关键在于——把每一个
   缓解措施都挂钩在**真实的** `lhx.Config` 开关上。于是翻转 `enabled` 改变行为的方式
   与真正部署的模块完全一致，我也得以检验 harness *是否检测到了它应当检测到的效应*。
   Regression 子集是阴性对照：两个 arm 都约 100%。
3. **一个被我抓到并修掉的 grader 真实性 bug。** 我的第一次运行报告 ON arm 有 66.7% 的
   漂移率。深入查看后发现，keyword-drift 启发式在 simulated backend 的合成 token 袋
   产物（它们本就缺少目标的散文用词）上误报。修复：ground-truth 漂移来自 backend；
   关键词启发式只用于真实散文产物（SDK backend）。这恰恰是方法论所警告的"你的评估会骗
   你自己"的失败。
4. **针对数学的单元测试**（[tests/](tests/)）：pass@k 与 Chen 的闭式估计量一致；对所有
   `(n,c,k)` 有 `pass@k ≥ pass^k`；McNemar 计数与精确 p；零假设下 bootstrap 区间覆盖 0；
   仿真在跨进程下确定（修复了一个 `PYTHONHASHSEED` 盐值泄漏进 RNG 种子的 bug）。

---

## 9. 结果与 dashboard

`scripts/run_eval.sh` 产出 `runs/latest/results.json` 与一个自包含的
`runs/latest/dashboard.html`（无 JS/CDN 依赖），展示 per-arm 表格、配对差值 + CI、
McNemar 裁决，以及 **pass@k vs pass^k 曲线**。预期的特征——也是我们观察到的——是在
**long-horizon 专属**指标（compaction 存活率、resume、漂移、doom-loop）上有大幅增益，
并且这些增益更多体现在**可靠性（pass^k）**而非原始 pass@1 上。一个 pass@1 差值近零、
但 long-horizon 指标差值很大的结果，**并不是**零结果；那恰恰是模块在干它该干的事。

---

## 10. 局限与未来工作
- 2 天的合成任务集可能无法反映真实 long-horizon 难度；应加入 Terminal-Bench / SWE-bench
  风格的真实任务以及更多 compaction 边界。
- 完整打通 `ClaudeAgentSDKBackend`，在 Haiku/Sonnet 上跑一次在线 A/B（harness 本就保持
  模型无关，正是为此）。
- 加入 planner/builder/evaluator 三 agent 架构；浏览器验证的 evaluator；Bayesian/Dirichlet
  评估；更大、更平衡的任务库。
- 跟踪一个**重新简化的节奏**：在每次模型升级时，重新检验哪些护栏还值得保留。

## 11. 安全
最小权限的 `allowedTools`（evaluator 无写工具）；操作员 **kill-switch**（`AGENT_STOP`）
与 **steering**（`STEER.md`）；通过 completion gate 实现 human-in-the-loop；**隔离沙箱**；
通过 `max_budget_usd` 与 step-budget 熔断器实现成本上限；完整的 transcript 审计轨迹。

## 12. 附录 —— 复现
```bash
pip install -e .
python scripts/seed_tasks.py          # 生成 + 校验任务集
lhx-eval validate                     # 参考解 sanity check
lhx-eval run -k 10                    # 配对 A/B → runs/latest/dashboard.html
pytest -q                             # 34 个单元/集成测试
scripts/install.sh /path/to/project   # 把模块投放到真实项目
```
Hook JSON I/O、任务 schema、以及 `LHX_*` 环境变量分别在 `lhx/hooks/_io.py`、
`lhxeval/tasks/schema.py`、`lhx/config.py` 中就地注释说明。
