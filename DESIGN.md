# 方案设计文档：一套面向 Long-Horizon Agent 扩展的评估 Harness

> Take-home 方向：**Long-horizon**；目标 harness：**Claude Code**。交付两部分：(1) 轻量扩展模块
> `lhx`；(2) 一套受控的 A/B 评估 harness。本报告更多笔墨放在 harness 上，不是因为规模（几十个
> trial 级别），而是因为在 long-horizon 这个方向上，"数字可信吗"比"数字多大"更需要先答清楚。
> 一套能诚实分出 v05 正 delta、v04 shortcut 绕过、v03 净开销的小 harness，比一份只报头条数字、
> 不说 grader 会不会被 reward-hack 的大报告更可用；也是后续把同一套设计接到 Terminal-Bench /
> SWE-bench 等真实长程基准的前提（§5.10）。

---

## 1. 摘要（BLUF）

Long-horizon agent 任务失败，多半不是单步能力不足，而是整个运行跨越的 token、会话与出错
机会超出了单个 context window。`lhx` 用六个 hooks 给 Claude Code 补上一层跨会话策略
（§2）。本方案的重点在**如何严肃地评估这层策略**：一套配对 A/B harness，解耦
模型 / Harness / Tasks / Eval（§5.10），支持两种 backend（确定性仿真 / 真实 Claude）、多种
grader（可执行 F2P / token / model-judge / 以及被明确拒绝的"自报"反模式）。规模有限，
但把从"仿真校准"到"真实 A/B 出正负 delta"的完整链路打通了。

评估分两层，各回答不同问题：

**仿真 A/B（尺子校准，§5.8）**：13 任务 × k=10 × 2 arms = 260 trial。ON pass@1 92.3% vs OFF 50.8%，
配对 Δ +41.5pp [+33.1pp, +50.0pp] 95% CI，McNemar p≈1e-16。效应是 by-construction 的（缓解挂在
真实 `Config` 开关上），证明 harness 能检测效应、统计/沙箱正确。仿真不是业界意义的 eval，
这里只是"尺子准不准"的证据。

**真实 A/B（Haiku 4.5，可执行验证，§5.9）**：模块效果分场景，三种都被评估如实捕获：

| 任务类型 | 例子 | ON vs OFF 结论 |
|---|---|---|
| 单会话量级 | v01–v03 | ON≈OFF success；ON 多付 30–50% token 换协议开销 |
| 跨会话协调 | v05（build，k=3）· v06（debug，k=3） | 两任务各自 ON 3/3 vs OFF 0/3，Δ +1.00 [+1.00, +1.00]；同一 completion-gate 机制在 build/debug 两形态复现 |
| agent shortcut | v04（178k-token audit） | 模块被绕过：Haiku 用 `sed`/`python3 -c` 批处理，178k tokens 从未进入 context |

交付的重点不是某个漂亮数字，而是这套能分清"尺子准不准"与"模块好不好"、能诚实报出模块何时有用 /
何时添乱的 A/B 装置。规模从"仿真 260 trial + 真实 ~40 trial"起步，设计上可扩到 §5.10 的现成
长程基准。

---

## 2. 被测问题与对应方法

`lhx` 针对一组**可命名、可观测**的长程失效模式。下表既是问题清单，也是方法映射；
"=" 表示与某来源基本相同（忠实移植），"+" 表示新增/修改（理由见 §3）。

| 失效模式 | lhx 的方法 | 落点（hook / 文件） | 关系 |
|---|---|---|---|
| **M1** 一次性做完、中途耗尽 context | default-FAIL 特性契约 + "一次一个特性"约定 | SessionStart / Stop；[feature_list.json](lhx/state.py)、[CLAUDE.md](claude_config/CLAUDE.md) | = cwc |
| **M2** 过早宣布胜利 | completion gate + fresh-context evaluator | Stop（[stop.py](lhx/hooks/stop.py)）、SubagentStop（[evaluator.md](agents/evaluator.md)） | = cwc evaluator / **+** gate |
| **M3** context rot / 注意力预算 | 状态落盘 + 常量大小 `MEMORY.md` | PostToolUse（[memory.py](lhx/memory.py)） | **+** Codex 常量记忆 |
| **M4** 跨会话健忘 | progress ledger + SessionStart 主动注入 resume 上下文 | SessionStart（[session_start.py](lhx/hooks/session_start.py)） | = cwc handoff / **+** 主动注入 |
| **M5** doom loop / 失控成本 | `(tool,args)` 哈希的最近-N 重复检测 + step-budget 熔断 | PreToolUse（[loop_guard.py](lhx/loop_guard.py)） | **+** Kilocode |
| **M6** 目标漂移 | 周期性 reflection + 不可变 `BRIEF.md` 关键词启发式 | PostToolUse（[reflection.py](lhx/reflection.py)、[drift.py](lhx/drift.py)） | **+** 社区 / Codex |
| **M7** 中断后无法恢复 | git checkpoint + 类型化 `.lh/checkpoint.json` + resume 注入 | Stop / SessionStart（[checkpoint.py](lhx/checkpoint.py)） | = cwc commit-on-stop / **+** 类型化 checkpoint |

**为什么是现在**：METR 的 *Measuring AI Ability to Complete Long Tasks* 发现 50%-任务完成
时间跨度约每 7 个月翻一番。约束从"模型能否做这一步"转向"harness 能否让运行跨会话保持连贯"。
每个方法都**单一可开关**（Anthropic 原则：harness 组件编码假设，应随模型升级被重新简化）；
这也是干净 A/B 的前提（§5.1）。

---

## 3. 与 Anthropic 及现有工作的异同

**参考来源：** Anthropic 博客 *Effective harnesses for long-running agents*
（initializer + coding agent 两段式）、官方仓库
[`anthropics/cwc-long-running-agents`](https://github.com/anthropics/cwc-long-running-agents)
（`verify-gate.sh`/`evaluator.md`/`commit-on-stop.sh`/`kill-switch`/`steer`，自述"示例配料"）、
*Effective context engineering*、以及 *Demystifying evals for AI agents*（本方案评估方法学的
直接来源）；外加 Codex `/goal`（常量记忆）与 Kilocode（doom-loop 检测）。

**原样移植（不主张新颖性）：** default-FAIL 契约、fresh-context evaluator、handoff
（PROGRESS + CLAUDE.md + commit-on-stop）、operator controls（kill-switch / steer）。

**修改 / 新增（与评估直接相关的重点）：**
1. **单一可开关 `Config` + 配对 A/B。** cwc 的原语散在各 shell 脚本里无法整体开关；收敛到
   一个 [Config](lhx/config.py) 后，ON/OFF 两臂共用一切、只差一个布尔量，效应才可归因。
2. **整套评估 harness + long-horizon 专属指标（cwc 没有）。** 见 §5。
3. **用 evaluator + completion gate 替代 cwc 的 `verify-gate.sh` 写入门**（有意取舍）：
   "读过某后缀文件"只是证据代理、可被绕过；fresh-context 复现是更强验证。代价是没移植硬
   写入门，列入未来工作。
4. **用仿真 ground-truth 验证评估器本身**（§5.8）+ **可执行验证做真实评分**（§5.9）。两者
   在现有资源里都没见到现成方案。

---

## 4. 架构与集成面

```
 Claude Code / Agent SDK   （agent harness，A/B 中保持固定）
        │  生命周期 hooks（stdin JSON → stdout JSON）
        ▼
 lhx/hooks/*  薄适配层：SessionStart(注入resume) · PreToolUse(kill/steer/doom/budget)
              PostToolUse(事件轨迹+MEMORY+reflection) · PreCompact(备份) · Stop(gate+checkpoint)
              SubagentStop(evaluator 裁决)
        ▼ 调用
 lhx 原语 (config/state/memory/checkpoint/loop_guard/reflection/drift)
        ▼ 读写（契约原子写；日志 append）
 磁盘状态: PROGRESS.md · feature_list.json · BRIEF.md · MEMORY.md · .lh/{events,checkpoint}.json · git
```

控制循环 **build → evaluate → rebuild**：会话做一个特性、产出证据，evaluator 独立验证，
`NEEDS_WORK` 时其结论作为下一会话起点。状态在磁盘上，故 compaction 或进程被杀都不丢目标。
接线见 [claude_config/settings.json](claude_config/settings.json)；`LHX_*` 环境变量按 trial
覆盖，`LHX_ENABLED=false` 即用同一份文件的 OFF arm。

> 一个真实集成坑：Claude Code 2.x **默认不加载** project 作用域的 hooks，必须
> 显式 `--setting-sources project`，否则模块完全 inert（详见 §5.8）。

---

## 5. 评估方法论

### 5.1 实验设计：受控配对 A/B
自变量：long-horizon 模块（ON vs OFF），或经 per-primitive 开关单独消融某方法。固定：
模型/backend、任务集、agent harness、种子。对每个 `(task, seed)` 用**同一个种子**跑**两个
arm**，配对内方差相互抵消（paired design）；每任务 `k` 个种子。实现见
[lhxeval/runner.py](lhxeval/runner.py)。一次 trial 的流水线：`backend.run() → RunOutcome →
grade() → TrialResult → 聚合 → 统计 → dashboard`。

### 5.2 评估模式

三条正交的轴组合出每个数字的含义：**backend** × **grader** × **metric**。

| 轴 | 模式 | 含义 / 何时用 | 代价 |
|---|---|---|---|
| **Backend** | simulated | 以已知概率建模失效模式，验证评估器本身；产出 token 即 artifact | 离线、确定性、零成本，不反映真实能力 |
| | real（CLI / SDK） | 真实 Claude 在隔离沙箱里跑、hooks 真跑 | 需 key、计费、非确定性 |
| **Grader** | 确定性 token / 状态检查 | 检查 artifact 含必需 token（sim 用），快、客观 | 仅对结构化 artifact 有意义 |
| | 可执行 F2P/P2P | 对产出工作区跑真实测试，按退出码判（real 用，§5.9） | 任务规格必须机器可验证 |
| | model-judge（LLM rubric） | 主观/开放式产出，成对或带参考打分 | 需校准、自身有方差 |
| **Metric** | pass@1 / pass@k | 能力：k 次中至少一次成功；随 k 上升 | 会把走运排在可靠之上 |
| | pass^k | 可靠性：k 次全过；随 k 下降 | 单个不稳定 grader 即塌缩 |
| | capability vs regression | 前者起点低（要爬的山），后者≈100%（警戒线） | regression 抓回退 |

**明确拒绝的反模式**：agent 自报。信任 agent 写回 `feature_list.json` 的声明会被 reward-hack。
本方案的 grader 一律绕过 agent 自评（仿真读 backend 结构化输出，真实读磁盘产物 + `verify` 检查）。

**贯穿原则**（Anthropic *Demystifying evals*）：按结果而非路径评分；确定性 grader 优先；先有
参考解证明可解；读 transcript。

### 5.3 任务集
当前任务集分两部分。**13 个由 [scripts/seed_tasks.py](scripts/seed_tasks.py) 生成并校验的合成任务**：
**capability** 子集（多文件构建、refactor/迁移链、CLI、ETL、bug 扫除，跨 compaction 边界且/或被
中断）与 **regression** 子集（短、干净，两臂≈100%，抓回退），仿真 A/B（§5.8）即跑这 13 个。**外加
3 个独立编写的 executable-verified 任务**（`v05-incremental-app`、`v06-debug-session-scoped`、`v07-debug-amnesiac-pytest`）。
带 `verify` 可执行检查、在 §5.9 详评的共 6 个：`v01-slugify` / `v02-health-endpoint`（regression）、
`v03-tasklib`（4 模块+pytest）、`v04-bigrepo-audit`（`setup` 种入 50 模块施加 context 压力）、
**`v05-incremental-app`（10 模块 build，首个正 delta 例）** 与 **`v06-debug-session-scoped`（8 模块
debug，第二个正 delta 例，§5.9.3–5.9.4）**。每个任务带参考解（证明可解）；schema 见
[lhxeval/tasks/schema.py](lhxeval/tasks/schema.py)。

### 5.4 指标（[lhxeval/metrics.py](lhxeval/metrics.py)）
全部**按任务**计算后 macro 平均：**pass@1**、**pass@k 曲线**（`1−C(n−c,k)/C(n,k)`, Chen et
al.）、**pass^k 曲线**与头条 **pass^3**（`C(c,k)/C(n,k)`；头条 k 取中等值 3，因 pass^n 会
塌缩成全有/全无）；以及 long-horizon 专属的 **compaction 存活率 / 中断后 resume 成功率 /
目标漂移率 / doom-loop 频次**，外加平均 steps·tokens·cost。

> **关于仪表盘上的 "—"：** compaction-survival / resume-after-intr. 是**条件性指标**，分母分别是
> 「真触发了 compaction 的 trial 数」与「真被中断的 trial 数」。`_safe_rate(num, den)`
> 在 `den=0` 时返回 `None`、CLI 渲染为 "—"（[metrics.py:130-131](lhxeval/metrics.py)）。在
> verified-only 真实跑里，如果任务没触发真正的 compaction（如 §5.9 v04 被 sed 绕过的情形），
> 这两栏自然是 "—"，即"分母为零"而非"模块失效"。要让它们出数，必须先制造真实的 compaction /
> 中断（§5.9 的 `--tools` 受限路线 + multi-session 即是为此设计）。

### 5.5 方差与显著性（[lhxeval/stats.py](lhxeval/stats.py)）
**配对 bootstrap CI**（对 pair 重采样）、**McNemar 精确检验**（配对二元结果，报
helped/hurt/discordant + 精确双侧 p）、**Beta 后验**（Jeffreys 先验，小 n/小 k 下给诚实
区间）。全部纯 Python、精确（无正态近似），零 scipy 依赖。

### 5.6 Harness 卫生（[lhxeval/sandbox.py](lhxeval/sandbox.py)）
每 trial 在**全新临时工作区**运行（`task.id` 经消毒防路径逃逸），预置不可变 brief +
default-FAIL 契约，事后销毁，**无跨 trial 的文件或 git 历史泄漏**（Anthropic 观察到 agent
会读上一 trial 的 git 历史获利）。种子显式且记录；保留完整事件轨迹。

### 5.7 对有效性的威胁
任务歧义、grader 被绕过/reward-hack、环境不稳定、饱和、模型非确定性。缓解：参考解 sanity
check、"0% 通过率多半是任务坏了"、平衡的 capability/regression、确定性优先、读 transcript。
背景：连 **SWE-bench Verified 都在 2026-02 被弃用**（测试缺陷/污染），更说明参考解纪律与
抗污染的重要。

### 5.8 验证评估器自身（meta-eval / instrument validation）

在相信任何数字前做的事。**本节的仿真数字是"尺子校准"，不是模块的能力成绩**（仿真 agent 不是
业界评估方法）。

**仿真校准结果（simulated backend，13 任务 × k=10 × 2 arm = 260 trial）：** harness 在一个植入
了已知效应的仿真上正确地把效应检测了出来，且 regression 阴性对照无误报：

| 指标 | ON | OFF | Δ |
|---|---|---|---|
| pass@1（macro） | 92.3% | 50.8% | +41.5pp |
| pass^3 | 79.4% | 43.8% | +35.6pp |
| compaction 存活率 | 85.0% | 20.0% | +65.0pp |
| 中断后 resume | 93.3% | 13.3% | +80.0pp |
| 目标漂移率 | 0.0% | 49.2% | −49.2pp |

配对差异 +0.415 [+0.331, +0.500]（95% bootstrap CI）；McNemar p≈1e-16（helped 54，hurt 0）。
**读法**：效应是 by-construction 的（缓解挂在真实 Config 开关上），它证明的是 harness 能检测
效应、统计/沙箱正确，*不是*模块在真实 Claude 上有这么大提升。

做法：
1. **参考解 sanity check**（`lhx-eval validate`）：每任务参考解须评 `success=True` *且*空产出
   须评 `success=False`，否则任务损坏。
2. **已知 ground-truth 的仿真 backend**（[lhxeval/backends.py](lhxeval/backends.py)）：以显式
   概率建模 M2–M7，并把每个缓解**挂钩在真实 `lhx.Config` 开关上**，故翻转 `enabled` 的行为
   与真正部署一致；regression 子集是阴性对照（两臂≈100%，确认无误报）。
3. **两个被这套自检抓出来的真实 bug：**
   - *漂移假阳性*：首跑 ON 报 66.7% 漂移，是 keyword-drift 在合成 token 袋上误报。修复：
     ground-truth 漂移来自 backend，关键词启发式只用于真实散文。
   - *模块静默失效*：真实 CLI 首跑 `success=True` 却 0 事件：`LHX_CONFIG` 被塞成 JSON 串触发
     `OSError: File name too long`，**每个 hook 静默崩溃**，模块全程 inert，"成功"全靠 agent
     自报。修复：`from_env` 防御性解析 + 配置落文件；并发现需 `--setting-sources project`。
     这正是"评估在静默地测量虚无"的典型：只因 smoke 检查事件轨迹（而非只看 pass/fail）才被
     发现。
4. **数学单测**（[tests/](tests/)，43 项）：pass@k 对齐 Chen 闭式；∀`(n,c,k)` 有
   `pass@k ≥ pass^k`；McNemar 计数/精确 p；零假设下 bootstrap 覆盖 0；仿真**跨进程**确定。

### 5.9 真实 A/B：可执行验证

**Grader**：每任务携带 `verify` 检查（F2P/P2P，对产出工作区跑真实测试，退出码 0 即通过）。
`grade()` 在检查存在时按 verify 评分，否则回落到 token grader。"通过"不再依赖 agent 自评。

**6 个 executable-verified 任务**：

| 任务 | 类型 | 检查 |
|---|---|---|
| `v01-slugify` | regression | `slugify.py`：`slugify('Hello, World!') == 'hello-world'` |
| `v02-health-endpoint` | regression | stdlib HTTP server，真的起服务器并探测 `GET /health → 200 'ok'`（随机端口，可并发） |
| `v03-tasklib` | capability | 4 模块包（models/store/cli），4 个检查（imports / 行为 / pytest） |
| `v04-bigrepo-audit` | capability | `setup` 种入 50 个臃肿模块（~178k tokens），要求每个加 `AUDITED = True` |
| `v05-incremental-app` | capability | 10 个互相 import 的模块（F01→F10），prompt 显式要求"一模块一 session、先读 PROGRESS.md" |
| `v06-debug-session-scoped` | capability | 8 个各带独立测试的模块，每个 1 bug；prompt 约束"一模块一 session、只跑本模块测试、禁跑全量套件" |

#### 5.9.1 首跑：verified 任务对 Haiku 都是"单会话量级"

```bash
lhx-eval run --backend sdk --verified-only -k 1   # 当时 v01–v03 三个 verified 任务 = 6 trial, $0.33
```

结果：全部由 executable-checks 判 `success=True`；ON 臂 hooks 触发（v01=10、v02=8、v03=19 工具
事件），OFF 臂 inert（0 事件）；pass@1 ON 1.0 vs OFF 1.0，**Δ=0**。

诚实读法：即便 4 模块的 v03，真实 Claude 一次做完；`claude -p` 不强制 compaction，所以 ON/OFF
在这些任务上不该有差异，与仿真 regression 子集的预期一致。要看长程差异，需要"真的会跨多会话
/ 触发 compaction / 被中断"的任务。

#### 5.9.2 v04：agent 用 shell 批处理绕过 context 压力（负例）

v04 期望用 178k tokens 逼出真实 compaction。实跑：Haiku **只用 11 个工具调用、$0.079** 完成全部
50 个模块，`success=True`，无 compaction。原因是它用 `sed`/`python3 -c` 循环批处理，178k tokens
从未进入 context。

**教训**：磁盘上的 token 能被 grep 绕开，要施加真实 context 压力必须把内容塞进 prompt。
[`scripts/context_pressure_probe.py`](scripts/context_pressure_probe.py) 把 50 模块内联到 prompt
（≈168k tokens），确认触发一次 Haiku 自动 compaction（$0.249）。这证明长程 regime **可按需触发**，
但仅用 v04 出不了 ON>OFF：任务是 shortcut 友好型的。

**保留 v04 作为负例**：它揭示 agent 会自动优化到"不用 context"的路径，任何依赖"context 会被
消耗"假设的 harness 都会栽跟头。这本身是有价值的 long-horizon failure mode 记录。

#### 5.9.3 v05：跨会话协调，首次清晰击败 OFF

**任务设计**：10 个互相 import 的模块（F01 constants → F10 pipeline），6 个可执行检查（含 pytest）。
Prompt 明确"一模块一 session、先读 PROGRESS.md"。

**k=3, max_turns=80, max_sessions=3 A/B**（Haiku 4.5，总成本 $2.16）：

| metric | ON | OFF | paired Δ (95% CI) |
|---|---|---|---|
| success (pass@1) | **3/3** | **0/3** | +1.000 [+1.000, +1.000] |
| pass^3（可靠性）| **1.000** | 0.000 | +1.000 |
| partial credit | 1.000 | 0.310 | +0.692（三对完全相同）|
| tokens/trial | 29,392 | 5,650 | +23,742 [+18,066, +27,595] |
| cost/trial | $0.599 | $0.121 | ~5× |

零方差：3 个 ON trial 全部 1.00、3 个 OFF trial 全部 0.31，完全可复现。这是模块首次在真实 Haiku
上清晰击败 OFF。

**机理**（读 transcript + snapshot）：
- **ON**：session 1 一口气建 ~9 个模块并逐个 `mark_pass()` 更新 `feature_list.json`。Stop hook 见
  feature_list 未全 pass，block Stop、逼 agent 继续；session 2 完成 F10 + tests，verify 通过，
  干净停机。
- **OFF**：每个 session 只做 2-4 个模块，agent 自报"我做完了"、Stop 无 gate 拦截，会话早停；
  3 个 session 后仍卡在 partial=0.31。"完成"完全依赖 agent 自评，而 Haiku 在这类任务上系统性
  乐观（§2 M2 "过早宣布胜利"的真实观测）。

**核心 tradeoff**：ON 5× 贵、100% 成功；OFF 便宜、100% 失败。"付更多 token 换可靠性"由业务判断；
评估的任务是把两条曲线画清楚。

#### 5.9.4 v06：debug 版跨会话协调，机制不止 v05 一点

v05 是 build 任务。一个合理质疑是：正 delta 会不会只是 build 场景（没写的模块天然没有任何测试提示）的产物？v06 把同一机制搬到 debug 任务上复现。

**任务设计**：把一个 calc 库拆成 8 个小模块，每个植入 1 个 bug、各配一个独立测试文件。Prompt 强约束：每个 session 只准调试一个模块、只准跑该模块自己的测试、禁止跑全量套件。遵守约束的 agent 全程看不到全局 "N/M passed"，"整个库修完了吗" 于是退化为主观判断，正是 M2 的触发条件，与 v05 的"没有测试反馈"同构。多会话也被规模强制：8 个模块的工作量放不进单 session 的 turn 预算。

**k=3, max_turns=60, max_sessions=4**（Haiku 4.5，$2.03）：

| metric | ON | OFF | paired Δ |
|---|---|---|---|
| success (pass@1) | **3/3** | **0/3** | +1.000 [+1.000, +1.000] |
| partial credit | 1.000 | 0.500（三个种子完全一致）| +0.500 |
| tokens/trial | ~21,552 | ~11,809 | ON 反而更多 |
| cost/trial | ~$0.47 | ~$0.20 | ~2.4× |

**机理**：ON 的 completion gate 见 feature_list 未全 pass、block Stop，把 8 个模块在 1-2 个 forced session 里一次做完；OFF 遵守"一模块一 session"、每 session 只修约一个，又没有全局 oracle 提示还剩多少，几个 session 后停在约一半（partial 0.5）、verify 失败。注意 OFF 花了更多 session（4 vs 1-2）却做得更少：增益是"完成"，不是"多花力气"。

**诚实边界**：与 v05 一样这个 delta 带构造成分。"一模块一 session" 约束加上 max_sessions(4) < 模块数(8) 会从预算上饿死 OFF。它证明的是 completion gate 机制在 build 与 debug 两种任务形态上各自复现，不是一个野外发现。要去掉构造成分，需把 max_sessions 放宽到接近模块数、让胜负更多依赖真实的"过早宣布胜利"而非预算饥饿（列入 §7）。

**成本符号依任务而定（v07 反例）**：ON 不总是更贵。上面 v05（5×）与 v06（2.4×）里 ON 更贵，是因为 gate 逼它把 OFF 早停省掉的活真正做完。但 `v07`（debug + 跨会话健忘提示 + 限制 pytest 次数）是另一种结构：两臂都成功、无 success delta，然而 ON 的 PROGRESS.md 让每个冷启动 session 省掉"重新搞清哪些已修"的重复开销，**ON −45% token、−12,652 [−14,380, −10,924]、统计显著**。合起来：需要"逼着做完更多"时 ON 更贵（v05/v06），需要"跨会话重定向"时 ON 反而更省（v07）；把成本当成一律的开销会看错方向。

#### 5.9.5 debug 中发现的两个真实 bug

得到 v05 正向 delta 之前，先修好两个 bug（评估过程自己抓出来的，只看汇总数字会被埋掉）：

1. **PROGRESS.md 从未落盘（M4 断链）**。Stop hook 只在完成时 `ledger.append`；completion gate
   每次都 block Stop → append 永不执行、PROGRESS.md 保持空。修复：PostToolUse 在每次 Write/Edit
   后追加一行（[post_tool_use.py](lhx/hooks/post_tool_use.py)），带 unit test 断言
   （`test_post_tool_use_appends_edits_to_progress`）。
2. **CLAUDE.md 措辞导致 read-doubling**。原文 "produced and *read back* concrete evidence"，
   agent 直译为重新 Read 每个改过的文件，Read/Edit 比达 4.5:1。修复：改成 "the result of your
   own Edit/Write call IS your evidence; do not re-Read"。

#### 5.9.6 支持这些实验的代码扩展

- `ClaudeAgentSDKBackend.tools` / `disallowed_tools` / `allowed_tools`（[backends.py](lhxeval/backends.py)）：
  接出 `--tools`（真实 base-toolset 白名单，`--disallowedTools` 在 `bypassPermissions` 下是 no-op），
  `LHX_SDK_TOOLS` env 控制。用于 v04 的"禁 Bash 强制 Read+Edit"探针。
- `--task-id` CLI filter（[cli.py](lhxeval/cli.py)）：只跑指定任务；k=3 v05 只需 6 trial。
- 每 session debug 打印（`LHX_SDK_DEBUG=1`）：多会话运行时诊断关键。
- multi-session 模式（`max_sessions` / `LHX_SDK_MAX_SESSIONS`）：反复 fresh 跑 `claude -p`（不
  `--continue`），会话间用 verify 判收敛。是 v05 A/B 装置的基础。

### 5.10 与主流 Agent benchmark 对齐（解耦 模型 / Harness / Tasks / Eval）
主流长程 benchmark 都遵循"模型/Agent · Harness · Tasks · Eval"四层解耦，本方案与之**同构**：

| 层 | 本方案 | Terminal-Bench / Harbor | OSWorld-v2 | APEX-Agents |
|---|---|---|---|---|
| 模型/Agent | `backend`（sim / claude-sdk）| Harbor agent adapter（**含 Claude Code**）| agent | agent |
| Harness | `runner` + `sandbox` | Harbor harness（Docker）| 执行框架 | 统一基建 |
| Tasks | `tasks/*.json`（id/prompt/verify）| Harbor task format（registry）| JSON（id/instruction/config/evaluator）| HF 数据集 |
| Eval | `verify` 可执行检查 / token / model-judge | pytest F2P（容器）| 执行式 evaluator 脚本 | domain rubric（model-judge）|

我们的 **`verify` 可执行检查与 Harbor 的 pytest F2P、OSWorld 的 evaluator 脚本同构**（都按产出
的真实状态判定，而非自报），因此把 lhx 接到这些 benchmark 上是**写一个薄 adapter** 的工作量、
而非重构。可直接复用的长程套件（按对我们这套 Claude-Code/CLI 模块的接入难度排序）：
- **Terminal-Bench 2.0**（89 个真实终端任务，专门暴露 long-horizon coherence；**Claude Code 已是
  其受支持 agent**）：写一个 Harbor adapter 把 **lhx-ON/OFF 的 Claude Code** 接进去，即可在真实
  长程任务上跑 ON/OFF A/B。这是补齐 §7"缺真实长程任务"的最务实路径。
- **LongCLI-Bench**（专为长程 CLI，专家时长 1000+ 分钟、step-level 分数）、**SWE-bench(-Pro)**
  （多文件仓库、F2P/P2P pytest）：同属执行式评分，可同法接入。
- **OSWorld-v2 / APEX-Agents**（长程但需 GUI / 跨应用重型基建）：优先级靠后。

**诚实声明（资源边界）**：本 take-home 因时间/算力未在这些 benchmark 上实跑，每个都需各自的
Docker 镜像与大量 credit-hours（单 Terminal-Bench 一轮 ON/OFF×k 即可观）。但鉴于解耦同构且
`verify` 与其评分等价，所需的是一个 adapter 而非重新设计；本方案的离线仿真 + 小规模真实 A/B
已验证了这条链路的每一环。基线之难也佐证长程远未饱和：APEX-Agents 顶级模型 Pass@1 仅约 24%、
Terminal-Bench <65%。

---

## 6. 结果与 dashboard
`scripts/run_eval.sh` 产出 `runs/latest/results.json` 与自包含 `dashboard.html`（无 JS/CDN，
HTML 抽到 `string.Template`）：per-arm 表、配对差值 + CI、McNemar、pass@k vs pass^k 曲线。

**读 transcript**（同一 `(task=t04-cli-tool, seed=1000)`，仅翻转模块）比汇总数字更能说明
机制（事件取自 `.lh/events.jsonl`）：
```
OFF: F01-init ✓  F02-add ✓  F03-list ✓  compaction  → goal_lost_after_compaction
     结果 3/6 特性，drifted=True               （无 ledger，跨边界后丢目标、提前收手）
ON : F01-init ✓  F02-add ✓  F03-list ✓  compaction  interrupt  resume
     F04-done ✓  F05-report ✓  F06-tests ✓     结果 6/6，drifted=False
                                              （ledger 存活 + checkpoint 恢复，继续做完）
```

在仿真里失效模式被建模为**直接致任务失败**，故 pass@1 也被大幅拉动；真实模型上更可能的签名
是"raw pass@1 差异不大、增益集中在可靠性 pass^k 与 long-horizon 专属指标"。届时 pass@1
近零差、long-horizon 指标大差，**不是**零结果，正是模块在干它该干的事。

## 7. 局限与未来工作
- **跨会话协调 A/B 已在两种任务形态捕获（v05 build、v06 debug）；两处 delta 都带构造成分，跨
  compaction 多特性 A/B 仍是缺口。** 两个任务都靠"prompt 约束一模块一 session + completion gate
  强制续到 verify 通过"拿到 +1.00，说明机制不是 v05 单点；但胜负部分由"约束早停 + max_sessions
  小于模块数饿死 OFF"构造，还不是野外发现。**剩下的**：(a) 放宽 max_sessions 到接近模块数，让 OFF
  的失败更多来自真实的"过早宣布胜利"而非预算饥饿；(b) 把多特性结构与 §5.9 的 prompt 内联 168k
  tokens compaction 触发合并，测 M3（context rot 后 ledger 是否救得回来）；(c) 注入真实中断（杀
  `claude -p` 子进程 fresh 重启）直接考 M7 的 checkpoint+resume，SDK 路径可直接 `raise
  CancelledError`，比 CLI SIGKILL 干净。
- **更省事的替代：写一个 Harbor adapter** 复用现成长程套件（**Terminal-Bench 2.0** / LongCLI-Bench
  / SWE-bench），见 §5.10：因解耦同构、`verify` 与其 pytest-F2P 等价，是 adapter 工作量而非重构。
- **v05 与 v06 的 k=3 都已给出零方差满 delta；k≥5 会把 McNemar 从 p=0.25 推到 p<0.05**（当前
  k=3 上限为 exact-test 的样本量限制，非效应量问题）。同法可在 Haiku/Sonnet 对照跑，画真实
  pass^k / **成本·会话数** 曲线。
- 引入 **model-judge** grader 处理开放式产出。
- **feature_list.json 与 verify.checks 的对齐**：v05 里 feature_list 有 10 特性、verify 有 6 检查。
  当前 completion gate 只看 feature_list.json 的 passes 全 True 就放行，但 verify 可能仍失败（例
  如一个 ON trial 10/10 特性 pass 但没写 tests，verify tests 检查 fail）。后续可让 gate 也参考
  verify 结果，或强制 task 作者保证两者 1-1 对应。
- **隔离**：检查目前跑在临时目录（信任代码可以；任意 agent 产出应上 Docker/Harbor；服务器
  探测任务还需端口隔离才能并行）。
- 补回 cwc 的 `verify-gate` 硬写入门作纵深防御；CLI 成本解析已做（`--output-format json`）。
- 跟踪"重新简化节奏"：每次模型升级重检哪些护栏仍值得保留。

## 8. 安全
最小权限 `allowedTools`（evaluator 无写工具）；kill-switch（`AGENT_STOP`）+ steering
（`STEER.md`）；completion gate 提供 human-in-the-loop；隔离沙箱；`max_budget_usd` +
step-budget 熔断的成本上限；完整 transcript 审计；真实运行用 `--permission-mode
bypassPermissions` 仅限一次性沙箱。

## 9. 附录：复现
```bash
pip install -e .
python scripts/seed_tasks.py          # 生成 + 校验任务集
lhx-eval validate                     # 参考解 sanity check
lhx-eval run -k 10                    # 仿真配对 A/B → runs/latest/dashboard.html
pytest -q                             # 43 个单元/集成测试
# 真实 Claude（需 .env 里的 ANTHROPIC_API_KEY）：
cp .env.template .env
python scripts/smoke_sdk.py v01-slugify-verified   # 真实运行 + 可执行验证
```
Hook JSON I/O、任务 schema、`LHX_*` 环境变量分别在 `lhx/hooks/_io.py`、
`lhxeval/tasks/schema.py`、`lhx/config.py` 就地注释。
