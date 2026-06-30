# 方案设计文档 —— 一套面向 Long-Horizon Agent 扩展的评估 Harness

> Take-home 方向：**Long-horizon**；目标 harness：**Claude Code**。
> 本方案交付两部分：(1) 一个让 Claude Code 跨会话持续工作的轻量扩展模块 `lhx`；
> (2) 一套受控的 **A/B 评估 harness**。**评估方法学是本方案的主体**——回答"它在产品里真的更好吗、
> 我们凭什么相信这个数字"。模块是*被测对象*，评估方法学是*主角*。

---

## 1. 摘要（BLUF）

Long-horizon agent 任务失败，多半不是单步能力不足，而是整个运行跨越的 token、会话与出错
机会超出了单个 context window。`lhx` 用六个 hooks 给 Claude Code 补上一层跨会话策略
（§2）。但本方案真正的工作量在**如何严肃地评估这层策略**：一套配对 A/B harness，支持
**两种 backend**（确定性仿真 / 真实 Claude）、**多种 grader**（确定性 token / 可执行
F2P / model-judge / 以及被明确拒绝的"自报"反模式），并先**验证评估器本身**再相信任何结论。

**头条结果（simulated backend，12 个任务 × k=10 × 2 arm = 240 trial）：**

| 指标 | 模块 ON | 模块 OFF | Δ |
|---|---|---|---|
| pass@1（macro） | **91.7%** | 47.5% | +44.2pp |
| pass^3（可靠性） | **77.7%** | 41.7% | +36.0pp |
| compaction 存活率 | **82.0%** | 6.0% | +76.0pp |
| 中断后 resume 成功率 | **93.3%** | 13.3% | +80.0pp |
| 目标漂移率 | **0.0%** | 53.3% | −53.3pp |
| doom-loop / trial | **0.09** | 0.46 | −0.37 |

配对成功率差异 **+0.442 [+0.350, +0.533]**（95% bootstrap CI）；McNemar 精确检验
**p ≈ 2.2e-16**（helped 53，hurt 0）。

> ⚠️ **这是一次 harness 自检（harness-validation）运行，不是对真实模型的能力宣称。** 数字
> 来自一个具有**已知 ground-truth 效应**的仿真 backend（§5.8）：它存在的意义是证明"评估器
> 能正确检测出一个已知效应、且在阴性对照上不误报"，而非证明模块在真实 Claude 上提升多少。

**真实集成 A/B（real backend，`lhx-eval run --backend sdk --verified-only`，Haiku）：** 在三个
executable-verified 任务（含 capability 级的 v03，4 模块 + pytest）上跑了真实 ON×OFF A/B
（6 trial，总计 **$0.33**），全部由**可执行检查**（非 agent 自报）判定 `success=True`；ON 臂
hooks 实际触发（最多 19 工具事件），OFF 臂 inert（0 事件）。pass@1 ON 1.0 vs OFF 1.0（Δ=0）
——**正确的阴性对照**：这些任务真实 Claude 都能一次性做完，且 `claude -p` 不强制 compaction，
故此处本就不应有差异。它打通了"真实 Claude + 真实 hooks + 可执行评分"的完整闭环；要在真实
Claude 上看出长程差异，需要真会跨会话/触发 compaction 的任务（§5.9 / §7）。

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

**为什么是现在：** METR 的 *Measuring AI Ability to Complete Long Tasks* 发现 50%-任务完成
时间跨度约每 7 个月翻一番——约束从"模型能否做这一步"转向"harness 能否让运行跨会话保持
连贯"。每个方法都**单一可开关**（Anthropic 原则：harness 组件编码假设，应随模型升级被
重新简化）；这也正是干净 A/B 的前提（§5.1）。

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
4. **用仿真 ground-truth 验证评估器本身**（§5.8）+ **可执行验证做真实评分**（§5.9）——两者
   在现有资源里都没见到现成方案。

---

## 4. 架构与集成面

```
 Claude Code / Agent SDK   （agent harness —— A/B 中保持固定）
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
"评估"不是一个开关，而是几条**正交的轴**。把它们显式拆开，是为了让每个数字都能说清"这是
用哪种 backend、哪种 grader、哪种指标得到的"。

| 轴 | 模式 | 含义 / 何时用 | 代价 |
|---|---|---|---|
| **Backend**（被测体怎么"跑"）| **simulated** | 以已知概率建模失效模式，**验证评估器本身**；产出 token 即 artifact | 离线、确定性、零成本，但**不反映真实能力** |
| | **real（CLI / SDK）** | 真实 Claude 在隔离沙箱里跑、hooks 真跑 | 需 key、计费、非确定性 |
| **Grader**（怎么判成功）| **确定性 token / 状态检查** | 检查 artifact 含必需 token（sim 用）；快、客观 | 仅对结构化 artifact 有意义 |
| | **可执行 F2P/P2P** | 对**产出工作区**跑真实测试，按退出码判（real 用，§5.9） | 需把任务规格收紧到可机器验证 |
| | **model-judge（LLM rubric）** | 主观/开放式产出；成对或带参考打分 | 需校准、自身有方差 |
| | ~~自报（self-report）~~ | 信任 agent 写回 `feature_list.json` 的声明 | **反模式，明确拒绝**——可被 reward-hack |
| **Metric**（怎么聚合）| **pass@1 / pass@k** | 能力：k 次中至少一次成功；随 k 上升、"指数级宽容" | 会把走运排在可靠之上 |
| | **pass^k** | 可靠性：k 次全过；随 k 下降 | 单个不稳定 grader 即塌缩 |
| | **capability vs regression** | 前者起点低（要爬的山），后者≈100%（警戒线） | regression 抓回退 |

**贯穿原则（Anthropic *Demystifying evals*）：** 按**结果**而非路径评分；确定性 grader 优先，
pass^k 只在 grader 确定后才可信；先有参考解证明可解；读 transcript。本方案的两条 backend
正是"先校准尺子（simulated），再量真实对象（real）"的两层。

### 5.3 任务集
当前 12 个合成任务（[scripts/seed_tasks.py](scripts/seed_tasks.py) 生成并校验）：**capability**
子集（多文件构建、refactor/迁移链、CLI、ETL、bug 扫除，跨 compaction 边界且/或被中断）；
**regression** 子集（短、干净，两臂≈100%，抓回退）；以及 **executable-verified** 任务
（regression 级 `v01-slugify`、`v02-health-endpoint`，capability 级 `v03-tasklib`，带 `verify`
可执行检查，§5.9）。每个任务带参考解（证明可解）；schema 见
[lhxeval/tasks/schema.py](lhxeval/tasks/schema.py)。

### 5.4 指标（[lhxeval/metrics.py](lhxeval/metrics.py)）
全部**按任务**计算后 macro 平均：**pass@1**、**pass@k 曲线**（`1−C(n−c,k)/C(n,k)`, Chen et
al.）、**pass^k 曲线**与头条 **pass^3**（`C(c,k)/C(n,k)`；头条 k 取中等值 3，因 pass^n 会
塌缩成全有/全无）；以及 long-horizon 专属的 **compaction 存活率 / 中断后 resume 成功率 /
目标漂移率 / doom-loop 频次**，外加平均 steps·tokens·cost。

### 5.5 方差与显著性（[lhxeval/stats.py](lhxeval/stats.py)）
**配对 bootstrap CI**（对 pair 重采样）、**McNemar 精确检验**（配对二元结果，报
helped/hurt/discordant + 精确双侧 p）、**Beta 后验**（Jeffreys 先验，小 n/小 k 下给诚实
区间）。全部纯 Python、精确（无正态近似），零 scipy 依赖。

### 5.6 Harness 卫生（[lhxeval/sandbox.py](lhxeval/sandbox.py)）
每 trial 在**全新临时工作区**运行（`task.id` 经消毒防路径逃逸），预置不可变 brief +
default-FAIL 契约，事后销毁——**无跨 trial 的文件或 git 历史泄漏**（Anthropic 观察到 agent
会读上一 trial 的 git 历史获利）。种子显式且记录；保留完整事件轨迹。

### 5.7 对有效性的威胁
任务歧义、grader 被绕过/reward-hack、环境不稳定、饱和、模型非确定性。缓解：参考解 sanity
check、"0% 通过率多半是任务坏了"、平衡的 capability/regression、确定性优先、读 transcript。
背景：连 **SWE-bench Verified 都在 2026-02 被弃用**（测试缺陷/污染），更说明参考解纪律与
抗污染的重要。

### 5.8 验证评估器自身（meta-eval）
在相信任何数字前做的事——这是评估工程最该被考核的部分：
1. **参考解 sanity check**（`lhx-eval validate`）：每任务参考解须评 `success=True` *且*空产出
   须评 `success=False`，否则任务损坏。
2. **已知 ground-truth 的仿真 backend**（[lhxeval/backends.py](lhxeval/backends.py)）：以显式
   概率建模 M2–M7，并把每个缓解**挂钩在真实 `lhx.Config` 开关上**，故翻转 `enabled` 的行为
   与真正部署一致；regression 子集是阴性对照（两臂≈100%，确认无误报）。
3. **两个被这套自检抓出来的真实 bug：**
   - *漂移假阳性*：首跑 ON 报 66.7% 漂移——是 keyword-drift 在合成 token 袋上误报。修复：
     ground-truth 漂移来自 backend，关键词启发式只用于真实散文。
   - *模块静默失效*：真实 CLI 首跑 `success=True` 却 0 事件——`LHX_CONFIG` 被塞成 JSON 串触发
     `OSError: File name too long`，**每个 hook 静默崩溃**，模块全程 inert，"成功"全靠 agent
     自报。修复：`from_env` 防御性解析 + 配置落文件；并发现需 `--setting-sources project`。
     这正是"评估在静默地测量虚无"的典型——只因 smoke 检查事件轨迹（而非只看 pass/fail）才被
     发现。
4. **数学单测**（[tests/](tests/)，43 项）：pass@k 对齐 Chen 闭式；∀`(n,c,k)` 有
   `pass@k ≥ pass^k`；McNemar 计数/精确 p；零假设下 bootstrap 覆盖 0；仿真**跨进程**确定。

### 5.9 真实 A/B：可执行验证
要得到**可信的真实指标**而非"自报"，任务携带 `verify`——一组对**产出工作区**执行的检查
（F2P/P2P，退出码 0 即通过）。`grade()`（[lhxeval/graders.py](lhxeval/graders.py)）在检查
存在时按检查评分，否则回落到 token grader。三个 executable-verified 任务：
- `v01-slugify`（regression）：实现 `slugify.py`，检查 `assert slugify('Hello, World!')=='hello-world'`。
- `v02-health-endpoint`（regression）：stdlib HTTP server，检查**真的起服务器并探测** `GET /health → 200 'ok'`（随机端口，可并发）。
- `v03-tasklib`（capability）：4 模块包（models/store/cli + pytest），4 个检查（imports / 行为 / `pytest`）。

**这是一个真实的、可跑的 eval 模式**，复用同一条 A/B 流水线：
```bash
lhx-eval run --backend sdk --verified-only -k 1   # 仅 verified 任务，真实 claude -p，executable 评分
```
实测一次（3 任务 × ON/OFF × k=1 = 6 trial，Haiku，总计 **$0.33**）：六个 trial 全部由
**executable-checks** 判 `success=True`；ON 臂 hooks 实际触发（v01=10、v02=8、v03=**19** 工具
事件），OFF 臂 inert（0 事件）。**pass@1 ON 1.0 vs OFF 1.0（Δ=0），含 capability 级的 v03**。
这是一个**诚实但重要的结果**：即便是 4 模块的 v03，真实 Claude 仍能一次性做完，且
`claude -p` **不会强制 compaction**，所以真实 ON/OFF 在这些任务上本就不该有差异——与仿真里
regression 子集的预期一致。要在真实 Claude 上看出长程差异，必须有**真的会跨多会话/触发
compaction/被中断**的任务（§7）。drift 等 prose 启发式在 executable 任务上不参与评分
（见 [runner.py](lhxeval/runner.py) 门控）。

> 这就是"评估真实表现"的正确形态（Anthropic 路线）：跑真实 agent、用**可执行测试**判真实
> 产出；仿真层只是先确保这把尺子准的脚手架。要在长程能力上看出 ON/OFF 差异，需要把可验证
> 任务集扩充到会真正触发 M1–M7 的长程任务（§7）。

---

## 6. 结果与 dashboard
`scripts/run_eval.sh` 产出 `runs/latest/results.json` 与自包含 `dashboard.html`（无 JS/CDN，
HTML 抽到 `string.Template`）：per-arm 表、配对差值 + CI、McNemar、pass@k vs pass^k 曲线。

**读 transcript（同一 `(task=t04-cli-tool, seed=1000)`，仅翻转模块）** ——比汇总数字更能说明
机制（事件取自 `.lh/events.jsonl`）：
```
OFF: F01-init ✓  F02-add ✓  F03-list ✓  compaction  → goal_lost_after_compaction
     结果 3/6 特性，drifted=True               （无 ledger，跨边界后丢目标、提前收手）
ON : F01-init ✓  F02-add ✓  F03-list ✓  compaction  interrupt  resume
     F04-done ✓  F05-report ✓  F06-tests ✓     结果 6/6，drifted=False
                                              （ledger 存活 + checkpoint 恢复，继续做完）
```

在仿真里失效模式被建模为**直接致任务失败**，故 pass@1 也被大幅拉动；真实模型上更可能的签名
是"raw pass@1 差异不大、增益集中在可靠性 pass^k 与 long-horizon 专属指标"——届时 pass@1
近零差、long-horizon 指标大差，**不是**零结果，正是模块在干它该干的事。

## 7. 局限与未来工作
- **规模化真实 A/B：** 真实 Agent-SDK A/B 已在 verified 任务上跑通（§5.9，小规模阴性对照）；
  下一步是把 executable-verified 任务集扩到会真正触发 M1–M7 的**长程**任务（多会话、强制
  compaction、真实中断），并在 Haiku/Sonnet 上跑 k≥5，得到真实的 pass^k / 成本曲线——届时
  才可能看出 ON/OFF 的长程差异。组件（backend、可执行 grader、统计）已就绪。
- 扩充 executable-verified 任务集（目前 2 个）至 Terminal-Bench / SWE-bench 量级；引入
  **model-judge** grader 处理开放式产出。
- **隔离**：检查目前跑在临时目录（信任代码可以；任意 agent 产出应上 Docker/Harbor；服务器
  探测任务还需端口隔离才能并行）。
- 补回 cwc 的 `verify-gate` 硬写入门作纵深防御；CLI 成本解析已做（`--output-format json`）。
- 跟踪"重新简化节奏"：每次模型升级重检哪些护栏仍值得保留。

## 8. 安全
最小权限 `allowedTools`（evaluator 无写工具）；kill-switch（`AGENT_STOP`）+ steering
（`STEER.md`）；completion gate 提供 human-in-the-loop；隔离沙箱；`max_budget_usd` +
step-budget 熔断的成本上限；完整 transcript 审计；真实运行用 `--permission-mode
bypassPermissions` 仅限一次性沙箱。

## 9. 附录 —— 复现
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
