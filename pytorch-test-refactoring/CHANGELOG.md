# Changelog

## 2026-08-25 — Harness 插件层重构（任务构建与发射解耦）

将 harness 依赖从"`BaseAdapter` 大接口 + `CodexAdapter(ClaudeCodeAdapter)` 继承链"重构为
**共享任务构建 + 薄 Harness 协议**，使新增 harness 只写一个模块 + 一行注册表，核心零改动。

- **职责拆分**：原 `agent/adapter.py`（~20 方法）拆成三层——`agent/tasks.py`（harness 无关的
  `build_*` 任务构建器，全部 harness 复用）、`agent/harness.py`（精简 `AgentTask` + `Harness`
  协议）、`agent/harnesses/{claude,codex}.py`（发射 + 策略实现）。删除 `adapter.py`/
  `claude_code.py`/`codex.py`/`registry.py`。
- **`AgentTask` 精简**：移除 Claude 专属发射提示（`mode`/`run_in_background`/`agent_type`/
  `model`）与冗余的 `agent_id`（并入 `context["send_message_to"]`），只保留 `phase`/
  `agent_name`/`prompt`/`context`。per-role 权限模式移入 `ClaudeHarness` 的角色映射表。
- **发射收敛**：`task_to_spec`/`ci_task_to_spec`/`ingest_task_to_spec`/`review_task_to_spec`
  四个近重复发射器收敛为 `spawn()`/`followup()`；`build_ingest_inline_spec` 移入 orchestrator
  的 `_inline_instruction()`，由 `supports_delegated_agents` 能力标志门控。
- **核心去 harness 化**：`flow.py`/`ci_ops.py`/`ingest_ops.py`/`review_ops.py` 不再持有
  adapter，直接调用 `agent.tasks` 的 `build_*`；`review_ops` 的
  `harness_name == "codex"` 分支替换为 `supports_delegated_agents` 能力标志。
- **选择去硬编码**：`--harness` 的 `choices` 从 `HARNESSES` 注册表自动派生；`_cmd()` 恒写入
  `--harness <name>`（含默认 `claude`），移除 `!= "claude"` 特判。
- **测试**：`tests/test_harness.py` 重写为 Harness 协议契约（新增 `supports_delegated_agents`、
  `AgentTask` 精简、registry choices 自动派生）；其余 5 个测试文件迁移构造签名；全套 58 个测试通过。

### 文件变更

| 文件 | 描述 |
|------|------|
| `agent/tasks.py` | 新增共享 `build_*` 任务构建器 + prompt/字段 helper（从 claude_code.py 迁入） |
| `agent/harness.py` | 新增精简 `AgentTask` + `Harness` 协议 |
| `agent/harnesses/{claude,codex}.py`、`__init__.py` | 新增 harness 发射/策略实现 + `HARNESSES` 注册表 |
| `agent/adapter.py`、`agent/claude_code.py`、`agent/codex.py`、`agent/registry.py` | 删除 |
| `flow.py`、`ci_ops.py`、`ingest_ops.py`、`review_ops.py` | 去 harness 化：移除 adapter 注入，改用 `agent.tasks` 构建器 |
| `orchestrator.py` | `get_harness`、能力标志、`spawn`/`followup`、`_inline_instruction`、`_cmd` 恒写 `--harness`、choices 自动派生 |
| `tests/*.py` | 迁移到新协议/构造签名；新增能力标志与 choices 派生测试 |
| `CLAUDE.md`、`README.md` | 更新架构与项目结构描述 |

## 2026-08-25 — Ingest sidecar 适配 Codex harness（inline 执行）

- **修复**：`--ingest-feedback` 与 `--apply-ingest` 在 Codex harness 下不再
  spawn triage/analyst/ruleset-editor 子代理。Codex MultiAgentV2 会把
  `spawn_agent` 的任务 message 记录为 assistant/commentary 信封而非
  user/task message（openai/codex#25458），导致子代理忽略任务并重跑
  orchestrator（表现为挂起、无结果、重复产出）。与 review queue 相同，
  Codex 改为 `method="inline"`：由 executor（main agent）自己执行
  triage/analyst/apply 步骤，把结果 JSON 写入 `feed_file` 后运行
  `on_complete.command`；Claude 保持 spawn 子代理不变。
- **实现**：`BaseAdapter.build_ingest_inline_spec()`（默认返回 None），
  `CodexAdapter` 覆写为 inline spec；`orchestrator._emit_ingest_action` 与
  `_run_apply_ingest` 优先使用 inline spec，无 harness 分支侵入 orchestrator。
  SKILL.md 的 ingest 章节与 daily cron prompt 更新为按 harness 分流。

## 2026-08-24 — Reviewer 反馈摄取（已应用）

- **Minor** [193124-3809809488](https://github.com/pytorch/pytorch/pull/193124#discussion_r3809809488) — 重构后遗留的孤儿 helper 函数未被覆盖：analyst/coder prompt 与 verify.py 只处理过期 import/符号，从不扫描无调用方的死 helper 函数。（目标：coder.md）
- **Major** [192741-3756398487](https://github.com/pytorch/pytorch/pull/192741#discussion_r3756398487) — S3 CUDA 类之外的残留 `@onlyCUDA` 装饰器能通过验证：`_check_stale_patterns` 从不标记 `@onlyCUDA`，`_check_imports` 只要文件任意位置出现 `@onlyCUDA` 装饰器即豁免 `onlyCUDA` import，导致遗留的 CUDA-only 装饰器从未被确定性捕获。（目标：verify.py、scripts/linter.py）
- **Major** [187926-5284009124](https://github.com/pytorch/pytorch/pull/187926#issuecomment-5284009124) — “半转换”缺口：测试新增了 device 参数但部分张量构造仍建在 CPU 上（`test_fliplr_invalid` 的 `torch.randn(42).to(dtype)` 忽略 device），且设备无关改动（`.cpu()` before `.numpy()`）被带入 `only_for="cpu"` 类成为死代码（`test_diagonal_multidim`）；coder.md 未记录或验证这些模式。（目标：coder.md）

## 2026-08-17 — PR Review Queue（每日批量 review sidecar，按 harness 分流）

新增独立 sidecar 模块 `review_ops.py`：读取 `agent_space/pr_needs_review.txt`
中的待 review PR，每天按批（`--limit N`，默认 10）做 diff-based review，并把
结果以一天一条 comment 发布到 `cosdt/pytorch-initial-pr-reviews#1`。

- **选择阶段（确定性）**：`scripts/review_queue.py` 通过 `gh pr view` 按 FIFO
  分类——open 且有 test 文件改动（`test/**`、`torch/testing/**`）的 PR 进入
  review 队列；merged/closed 或无 test 改动的 PR 记为“不适用”（在 comment
  中列出并归档）；元数据拉取失败的 PR 静默跳过、留在待处理列表。
- **Review 阶段（AI，按 harness 分流）**：Claude Code 每个 PR 一个 reviewer
  sub-agent、按 4 并发波次执行；Codex 由 executor（main agent）内联逐个 review
  （不 spawn 子 agent）。两者都复用 `review-test-refactoring` skill 的
  diff-based 模式，并写入 `agent_space/pr_reviews/pr_<n>_result.json`。
- **发布阶段（确定性）**：渲染一天一条 comment——每个 PR 一个 `<details>`
  折叠块（`@作者`、PR 状态、Blocker/Major/Minor 完整 findings），
  `gh issue comment` 发布到 tracking issue；归档已处理 PR 到
  `agent_space/pr_reviews/pr_reviewed.json`，并重写 `pr_needs_review.txt`
  （已处理移除、失败保留）。
- **失败语义**：review 失败或 PR 元数据拉取失败不出现在 comment 中、留在
  待处理列表下次重试；不适用项（merged/closed 或无 test 改动）在 comment
  中列出并归档。
- **为什么 Codex 分流**：Codex MultiAgentV2 会把 `spawn_agent` 的任务
  `message` 记录成 assistant/commentary 邮箱信封而非 user/task 消息
  （openai/codex#25458），spawn 出的 reviewer 会忽略自己的 review 任务并
  重跑 orchestrator，因此 Codex 改为 main agent 内联执行；Claude Code 的
  `Agent` 工具能正常下发任务，保留 sub-agent 模型。
- **编排入口**：`orchestrator.py` 新增 `--review-queue` / `--limit`，feed
  类型 `reviewer`；`ReviewOps` 状态持久化到
  `agent_space/pr_reviews/flow_state.json`。
- **测试**：新增 `tests/test_review_queue.py`（选择/渲染/发布/归档/内联与
  sub-agent 两条状态机路径/失败跳过），全套 54 个测试通过。

### 文件变更

| 文件 | 描述 |
|------|------|
| `review_ops.py` | 新增 ReviewOps 状态机（select → review → publish，按 harness 分流） |
| `scripts/review_queue.py` | 新增确定性逻辑：pending 加载/选择、gh 分类、comment 渲染、发布、归档、重写 pending |
| `agent/prompts/reviewer.md` | 新增单个 reviewer prompt（Claude 子 agent 模式） |
| `agent/prompts/reviewer_batch.md` | 新增内联批量 review 指令模板（Codex 模式） |
| `agent/adapter.py`、`agent/claude_code.py`、`agent/codex.py` | 新增 `build_reviewer_task` / `review_task_to_spec` |
| `state.py` | 新增 `PrReviewItem` / `PrReviewFinding` / `PrReviewResult` / `ReviewOpsState` |
| `utils.py` | 新增 review-queue 路径常量与工作区 helper |
| `orchestrator.py` | 新增 `--review-queue` / `--limit` 入口与 reviewer feed 路由 |
| `SKILL.md`、`README.md`、`CLAUDE.md` | PR Review Queue 文档与每日触发说明 |
| `tests/test_review_queue.py` | 新增 8 个单元测试 |

## 2026-08-17 — 测试字段分类与字段化引用知识库

为工作流引入 `core` / `distributed` / `graph` 三个测试字段，使引用知识与运行行为
可以按测试文件类型分域扩展。`core` 保持为默认字段，并继续使用根目录 `reference/`
作为默认知识库。

- **字段解析**：`utils.resolve_field()` 通过 `reference/distributed/test_list.txt` 与
  `reference/graph/test_list.txt` 精确路径匹配判定字段；未命中默认 `core`，同时命中
  多个非 core 列表时报歧义错误。
- **字段化工作区**：所有重构工作区改为 `agent_space/refactor/{field}/{file_name}/`；
  `RefactorState` / `AssessmentResult` 新增 `field` 并随 flow state 持久化。
- **非 core 基线**：非 core 字段暂无专属重构 profile，当前只执行
  import/符号清理，不进行 S1/S2/S3 分类、类拆分或设备改写；验证只跑
  syntax/test_count/class_structure/import/lint，最终评审为通用基线，
  本地测试门禁跳过。
- **Prompt 分域**：新增 `analyst_baseline.md` / `coder_baseline.md` /
  `checker_baseline.md`；`claude_code.py` 根据 ref_dir/workspace 自动选择 core
  或 baseline 模板，并在非 core prompt 中注入字段与 core 回退路径。
- **CI 工作区**：`orchestrator.py` 的 CI 检查路径同步使用字段化工作区。
- **测试**：新增 `tests/test_fields.py`（解析、重叠、字段化路径、规则、prompt、
  本地测试跳过），全套 46 个测试通过。

### 文件变更

| 文件 | 描述 |
|------|------|
| `utils.py` | 新增字段解析、字段化 reference/workspace 路径与 non-core 规则基线 |
| `state.py` | `AssessmentResult` / `RefactorState` 新增 `field` |
| `flow.py` | 字段解析、字段化 workspace、field-aware distribute/verify/local-test |
| `scripts/assess.py`、`scripts/verify.py`、`scripts/report.py` | 字段化 workspace、non-core 验证子集与报告字段 |
| `agent/claude_code.py` | field-aware analyst/coder/checker prompt 选择 |
| `agent/prompts/*_baseline.md` | 新增非 core 字段的 field-agnostic 基线 prompt |
| `orchestrator.py` | CI 工作区使用字段化路径 |
| `reference/distributed/test_list.txt`、`reference/graph/test_list.txt` | 字段路径清单迁移到字段目录 |
| `SKILL.md`、`CLAUDE.md`、`README.md` | 字段概念、目录布局、工作区与基线行为文档 |
| `tests/test_fields.py` | 新字段测试；同步更新旧工作区路径断言 |

## 2026-08-17 — 本地测试门禁（Phase 6.5）

在 Phase 6 最终评审之后、Phase 7 finalize 之前新增确定性本地测试门禁：默认在 CPU 上
运行整个重构后的测试文件（CUDA 可用时同一整文件运行自动覆盖 CUDA），解析 JUnit XML，
把 FAIL/ERROR 交给 coder 判断 `fixed`（refactor 导致）或 `deferred`（pre-existing/
environmental），重跑直到没有非预期失败（有界软失败）。

- **解释器解析**：`scripts/local_test.py` 先解析可用解释器——conda env（如
  `pytorch-dev-cpu`）、仓库内 venv，最后 `sys.executable`；可用
  `PYTORCH_TEST_REFACTOR_PYTHON` 覆盖，并用 `import torch` 验证候选。
- **整文件运行 + 解析**：`python test/<file> --use-pytest --junitxml=...` 单次运行即
  覆盖 CPU/CUDA；解析 JUnit XML 后 FAIL/ERROR 驱动修复，SKIP/xfail 记录，
  XPASS 记为 unexpected-success 不阻塞；整次运行 timeout/OOM/segfault/import 归为
  environmental，重试一次后继续。
- **修复循环**：`flow.py` 新增 Phase 6.5，运行 → 把失败转发给 coder 判断
  `fixed`/`deferred` → 重跑；`deferred_failures` 持久化并在后续轮次中排除；
  `MAX_RETRIES=3` 软失败。
- **状态与产物**：`state.py` 新增 `LocalTestResult`/`LocalTestFailure` 及
  `local_test`/`deferred_failures`/`test_sub_phase`/`test_retry_count`；工作区新增
  `local_test.json`。
- **适配器/编排**：`BaseAdapter.build_test_fix_task` + `claude_code.py` 实现；
  `orchestrator.py` 在 `--feed coder` 的 test 阶段解析 `verdicts` 并路由到
  `feed_local_test_fix_result`。
- **报告/文档/测试**：`report.py` 增加 Local Test 段落；`SKILL.md`/`CLAUDE.md` 更新
  phase 列表、工作区产物与 coder 回传格式；新增 `tests/test_local_test.py`（6 个），
  全套 37 个测试通过。实测 `test_tensor_creation_ops.py`：681 用例、587 通过、
  94 跳过、0 失败。

### 文件变更

| 文件 | 描述 |
|------|------|
| `scripts/local_test.py` | 新增确定性 runner（解释器解析、pytest+JUnit 运行、XML 解析） |
| `flow.py` | 新增 Phase 6.5 门禁与 coder 修复循环、`local_test` 持久化 |
| `state.py` | 新增 `LocalTestResult`/`LocalTestFailure` 及 `RefactorState` 字段 |
| `utils.py` | 新增 `LOCAL_TEST_FILE` 常量 |
| `agent/adapter.py`、`agent/claude_code.py` | 新增 `build_test_fix_task` |
| `orchestrator.py` | test 阶段 coder 回传路由 + verdicts 构建 |
| `scripts/report.py` | 新增 Local Test 摘要段落 |
| `SKILL.md`、`CLAUDE.md` | phase 列表、工作区产物、coder 回传格式文档 |
| `tests/test_local_test.py` | 门禁解析与修复循环测试 |

## 2026-08-16 — Codex 适配器修复：恢复子 agent 上下文传递

修复 Codex 运行环境下子 agent 收不到任务上下文的问题。旧的 Codex spec 使用
`send_input`/`resume_agent` 工具名，并同时为每个角色注入 `model` 覆盖值；当前
Codex 协作运行时改用 `spawn_agent`/`followup_task`/`wait_agent`，且全历史 fork
（`fork_turns: "all"`）不能接受 `model` 覆盖。两者叠加导致 spawn 出的 analyst 等
子 agent 缺少父上下文，返回"没有任务"。

- **spawn spec 重写**：改为 `task_name`/`message`/`fork_turns: "all"`，把超时移入
  `wait.timeout_ms`，不再输出 `prompt`/`timeout_ms`/`model`。
- **follow-up spec 重写**：`send_input`/`resume_agent` 改为 `followup_task`
  （`target`/`message`），fallback 重 spawn 同样使用全历史 fork 且不输出 `model`。
- **移除 Codex 每角色模型注入**：`_model_for`/`_with_model` 及 `DEFAULT_MODELS` 删除，
  `CodexAdapter` 继承父模型，`AgentTask.model` 仅保留给 Claude 路径。
- **回传说明与文档**：`completion_note` 和 `SKILL.md` 改为描述真实的 Codex 协作工具，
  并明确 `agent_id` 来自 `spawn_agent` 返回的 canonical task name（如 `/root/analyst`）。
- **测试**：更新 `tests/test_harness.py`/`tests/test_workflow.py` 断言，31 个测试通过。

### 文件变更

| 文件 | 描述 |
|------|------|
| `agent/codex.py` | Codex spec 形态改为当前协作工具；移除每角色 model 注入 |
| `agent/adapter.py` | `model` 字段注释更新为可选覆盖 |
| `SKILL.md`、`CLAUDE.md` | Codex 工具名/循环/`agent_id` 说明更新 |
| `tests/test_harness.py`、`tests/test_workflow.py` | 更新 Codex spec 契约测试 |

## 2026-08-14 — Harness 插件化重构 + Codex 适配器

将工作流的 harness 依赖从"Claude Code 专属"重构为插件化架构：每个 harness（Claude Code、
Codex）是一个 `BaseAdapter` 实现，`orchestrator.py` 通过注册表按名字选择，所有 harness
特定逻辑（任务 spec 形态、回传 note、cron 策略、git 预检）都下沉到适配器内，编排层不再
出现任何 harness 分支。

- **接口扩展**：`agent/adapter.py` 的 `BaseAdapter` 从"仅构造 AgentTask"扩展为完整 harness
  策略——新增 `task_to_spec`/`ci_task_to_spec`/`ingest_task_to_spec`、`completion_note`、
  `ci_wait_on_complete`/`ci_done_next_steps`、`git_preflight`/`git_preflight_error`、
  `build_ruleset_editor_task`；`AgentTask` 新增 `model` 字段。
- **ClaudeCodeAdapter 行为不变**：原 `orchestrator.py` 中的 spec 发射、Write-tool note、
  CronCreate 策略、`~/.claude/settings.json` 预检逐字迁入 `agent/claude_code.py`，
  `--harness claude` 输出与旧版一致。
- **新增 CodexAdapter**（`agent/codex.py`）：`spawn_agent`/`send_input` spec 形态、
  `resume_agent` 恢复、每角色 model 默认值（`feedback_triage` 用 `deepseek-v4-flash`，其余
  `deepseek-v4-pro`）、基于 `sleep` 轮询的 cron 替代（`--ci-check --resume` 幂等）、
  fallback 重 spawn 重建完整 coder 角色提示词。
- **注册表 + 选择**：`agent/registry.py` 提供 `HARNESS_ADAPTERS` 与 `get_adapter()`；
  `orchestrator.py` 新增 `--harness {claude,codex}`（默认取 `PYTORCH_TEST_REFACTOR_HARNESS`，
  再默认 `claude`）。`_cmd()` 把 `--harness` 写进每个回传/轮询命令，跨进程 resume 自动重选
  同一 harness。
- **编排层去分支**：`orchestrator.py` 删除 `_task_to_spec`/`_ci_task_to_spec`/
  `_ingest_task_to_spec`/`_denied_git_ops`，改为 `flow.adapter`/`ci.adapter`/`ops.adapter`
  委托；`ingest_ops.py` 接受注入 adapter。
- **CI cron 重设计（Codex）**：无 CronCreate 时改为 `poll`（`poll_command`/`user_cron_line`），
  `--ci-check` 恒 `resume=True` 以在轮询间保持 `ci_state.json` 状态。
- **prompt 中性化**：`analyst.md`（Write/Read 措辞）与 `debugger.md`（Claude Code
  permissions）改为 harness 中性表述。
- **入口指引**：`SKILL.md` 增加"先判断自身 harness 再传 `--harness`"步骤，CI/ingest/resume
  命令示例同步补充 flag。
- **测试**：新增 `tests/test_harness.py`（14 个，覆盖两套适配器的 spec 形态/model 注入/
  fallback/note/cron/preflight/命令回传）与 `tests/test_workflow.py`（3 个，基于
  `tests/materials/` 快照做 schema 校验、跨进程 resume、assess→finalize 全流程回放）；
  全套 31 个测试通过。

### 文件变更

| 文件 | 描述 |
|------|------|
| `agent/adapter.py` | `BaseAdapter` 扩展为完整 harness 接口；`AgentTask` +`model` |
| `agent/claude_code.py` | Claude 策略实现（spec/note/cron/preflight 迁入）；抽取 `_coder_prompt` |
| `agent/codex.py` | 新增 Codex 适配器（spec 形态、model 默认值、poll cron、respawn 提示词） |
| `agent/registry.py` | 新增 harness 注册表与 `get_adapter()` |
| `orchestrator.py` | `--harness` flag；委托化；`_cmd()`；`_rule_context_for()`；CI 恒 `resume=True` |
| `ingest_ops.py` | 接受注入 adapter |
| `SKILL.md` | 入口 harness 选择指引 + 命令示例补 flag |
| `agent/prompts/analyst.md`、`agent/prompts/debugger.md` | harness 中性措辞 |
| `tests/test_harness.py`、`tests/test_workflow.py`、`tests/materials/` | 契约测试 + 全流程回放测试/快照 |
| `docs/codex-harness-plan.md` | Codex 兼容性规格与实施计划（adversarial-review 产出） |

## 2026-08-13 — 确定性测试 linter 门禁（Phase 5）

将新增的 `scripts/linter.py`（AST 测试用例 linter）接入重构工作流，作为
Phase 4 coder 循环与 Phase 6 最终评审之间的硬门禁。linter 强制每个
`hw_classification` 的结构化契约（device 参数、实例化方式、`only_for`/`except_for`、
`@only*` 装饰器），现在是文件"完成"的权威定义。

- **Phase 5 `lint` 检查**：`verify.py` 导入 `check_file`，遇到任何 error 级别的lint 消息即失败，取代基于正则的 `_check_hw_classification`（H1）及其四个辅助函数。
- **硬门禁**：当 linter 报告错误时，`flow.py` 短路 Phase 6——合成 `ReviewFinding`（`category="lint"`、`coder_responsible="coder"`），进入修复循环，仅当 lint 干净后才启动 checker（最多 `MAX_RETRIES` 次，之后带着可见的失败继续进入评审）。
- **S3 契约对齐**：S3 类现在统一使用`instantiate_device_type_tests(only_for="<device>")` + `device` 参数；移除`utils.py`、`coder.md`、review skill 和 CLAUDE.md 策略表中的 plain-`TestCase`-带-`setUp`-guard 回退方案。
- **`@onlyNativeDeviceTypes*` 移除**：这些装饰器现已冗余并被移除（linter 会在ACCELERATOR 类上标记它们）；从所有 ruleset 文档的 KEEP 黑名单中剔除。
- **Checker 增强**：`checker.md` 和 review skill 现在镜像 linter 的结构化契约，使 AI 评审与确定性门禁保持一致。

### 文件变更

| 文件 | 描述 |
|------|------|
| `scripts/verify.py` | +`_check_lint`；移除 `_check_hw_classification` + 4 个辅助函数 |
| `flow.py` | lint 硬门禁 + 修复循环（`_collect_lint_errors`/`_enter_lint_fix`/`_advance_lint_gate`） |
| `state.py` | +`lint_gate_pending`、`lint_retry_count` |
| `utils.py` | `strategy_3` 不再提及 plain-TestCase 回退 |
| `agent/prompts/coder.md`、`analyst.md`、`checker.md` | S3 契约、KEEP 黑名单、linter 结构化规则 |
| `agent/skills/refactor-test-decoupling/SKILL.md`、`review-test-refactoring/SKILL.md` | S3 机制 + 命名、KEEP 黑名单、结构化规则 |
| `reference/classification_guide.md`、`CLAUDE.md`、`SKILL.md` | KEEP 黑名单 + S3 表 |

## 2026-08-13 — Reviewer feedback ingest (applied)

- **Minor** [185798-3615096416](https://github.com/pytorch/pytorch/pull/185798#discussion_r3615096416) — TEST_ACCELERATOR is a LazyVal not a bool, so replacing TEST_* in bool-type-checked decorators like serialTest raises AssertionError -- not yet documented in the ruleset. (target: coder.md)

## 2026-08-13 — MPS 安全规则例外：非 MPS 实例化类不要求 `@skipIfMPS`

`@onlyAccelerator` 只会在类确实被实例化到 MPS 时把测试暴露到 MPS。MPS 变体
只有在 `instantiate_device_type_tests` 传入 `allow_mps=True` 时才会创建
（`only_for`/`except_for` 单独设置不会启用 MPS）。若类未实例化到 MPS，测试
不可能运行在 MPS 上，此时追加 `@skipIfMPS` 无必要。

- **coder.md**：MPS safety 规则新增例外——类未实例化到 MPS（`instantiate_device_type_tests` 未传 `allow_mps=True`）时不要求 `@skipIfMPS`。
- **review-test-refactoring/SKILL.md**：MPS coverage safety 检查清单同步该例外。
- **verify.py**：`_check_skipifmps_coverage()` 新增 `_is_class_instantiated_for_mps()`——仅当方法所在类实例化到 MPS（`allow_mps=True` 且 `only_for`/`except_for` 未排除 MPS）才要求 `@skipIfMPS`；B3 dtype 安全检查的 MPS exposure 判定改为 `allow_mps=True` 存在（裸 `@onlyAccelerator` 不再视为 MPS exposure）。

### 文件变更

| 文件 | 描述 |
|------|------|
| `agent/prompts/coder.md` | MPS safety 规则新增"非 MPS 实例化类不要求 `@skipIfMPS`"例外 |
| `agent/skills/review-test-refactoring/SKILL.md` | MPS coverage safety 检查清单同步例外 |
| `scripts/verify.py` | +`_is_class_instantiated_for_mps()`；`_check_skipifmps_coverage()` 与 B3 尊重例外 |

## 2026-08-13 — PR feedback ingest sidecar module

新增独立 sidecar 模块，自动从 @KarhouTam 已合并的 `[Test]` PR 中抓取 reviewer
反馈，分析并（经人工审批后）落成 ruleset 修改。

- **抓取**：`scripts/ingest.py` 通过 `gh api` 抓取已合并（`Merged` label）PR 的
  replied inline review comments + `claude[bot]` issue-comment 摘要。
- **两阶段分析**：triage（相关性 + 目标 layer + 去重）→ analyst（逐层 intent spec）。
- **审批流**：findings 写入 `agent_space/ingest/findings/PR-<n>.md`，人工勾选
  Approved/Rejected 后 `--apply-ingest` 落盘并追加 CHANGELOG。
- **触发**：`--ingest-feedback` 子命令 + 每日 durable cron。

## 2026-08-12 — CI 自动化权限修复（Auto 模式下 git push/commit 被拦截）

在 Claude Code Auto 模式下调用 CI 自动化（Phase 8）时，debugger agent 运行 `git commit` / `git push` 被权限系统拦截，修复循环无法推进。

### 问题回顾

1. **`mode=bypassPermissions` 不生效**：部分 harness 忽略 `Agent` 工具的 `mode` 参数，子 agent 继承父会话的权限模式（Auto），不会获得 bypass；且显式 `permissions.deny` 规则在任何权限模式下都优先于 allow/bypass。
2. **全局 deny 列表**：`~/.claude/settings.json` 的 `permissions.deny` 含 `Bash(git push *)` / `Bash(git commit *)`（git-guardrails 类技能写入），debugger 无法提交/推送修复。
3. **回传命令不匹配 allow 规则**：`echo '{json}' | python orchestrator.py --feed debugger` 是复合命令，Bash 权限匹配按整串前缀进行，无法匹配 `Bash(python *)`，在 Auto 模式下被自动拒绝。

### 根因

CI 自动化本质上需要 `git commit` + `git push` 来推送修复，而环境的全局 deny 规则与技能的回传指令（echo 管道）在 Auto 模式下均被权限系统拦截。

### 改进要点

- **orchestrator.py 新增 `--feed-file`**：回传结果改为写文件 + `python orchestrator.py ... --feed X --feed-file <path>`，命令是单一普通前缀，可匹配 `Bash(python *)`，绕开复合命令拦截。
- **git 权限预检**：`_denied_git_ops()` 读取 settings.json 检测 `git commit`/`git push` 是否被 deny；若被拒，在 spawn debugger 前 fail-fast 输出可操作的修复指引。
- **SKILL.md / ci-automation SKILL.md / debugger.md**：统一改为 `--feed-file` 回传，并记录权限要求与 deny 冲突的两种解法（移除 deny 或改为手动推送）。

### 文件变更

| 文件 | 描述 |
|------|------|
| `orchestrator.py` | +`--feed-file` 参数、`_read_feed()`、`_denied_git_ops()` 预检；on_complete 指令改为 --feed-file |
| `SKILL.md` | 回传指令改为 feed_file；CI 表格与说明更新 |
| `agent/skills/ci-automation/SKILL.md` | --feed-file 回传 + 权限 caveat + allowlist 说明 |
| `agent/prompts/debugger.md` | git 被拦截时的处理说明 |

## 2026-08-10 — S2 过度分类修复 + 导入源纠正 + 分类正确性审查

基于 `test_packed_sequence.py` 重构实战中发现的三类问题，事后复盘并修复。

### 问题回顾

重构 `test_packed_sequence.py` 时连续出现三个错误：

1. **S2 过度分类**：analyst 将原文件全部 11 个 `PackedSequenceTest` 测试分类为 S2，coder 机械添加 `device` 参数和 `instantiate_device_type_tests`。用户指出这些测试只是 `rnn_utils` 工具函数测试（`pad_sequence`、`pack_sequence` 等），在 CPU 和多设备上走相同代码路径，device 参数化毫无意义。最终回退为 GENERIC，仅将真正测试 device transfer 的 `test_to` 提取到 `PackedSequenceTestDevice`。

2. **错误导入源**：coder 将 `instantiate_device_type_tests` 加到 `common_utils` import 块中，但该符号实际定义在 `common_device_type`。运行时 `ImportError` 导致整个模块无法加载。checker 捕获后需额外一轮 fix→re-check。

3. **分类正确性无人审查**：per-rule checker 只验证规则是否正确应用（device 参数是否加了、测试数量是否匹配），不验证规则是否应该应用。Phase 6 full-file checker 也漏过了——它检查 `hw_classification` tag 是否匹配 instantiation 机制，但不质疑分类决策本身。最终用户手动纠正。

**根因**：S2 定义为"使用 device 但只用到通用 API"过于宽泛——创建 tensor 就触发，而创建 tensor 是几乎所有测试都会做的事。S1 应为默认，S2 需举证"device 参数化提供了 CPU 测试无法覆盖的测试价值"。

### 改进要点

- **analyst.md Task 4 重写**：S1 明确为**默认分类**（原文件无 device decorators 的测试优先归 S1）；S2 新增"举证责任"——必须指明测试在 device 上执行的 specific testing value；工具函数测试（`rnn_utils`、`pad_sequence`、`pack_sequence`、`F.pad`、`F.embedding` 等）显式归入 S1。
- **coder.md 策略指导**：strategy_2 章节首位新增 `instantiate_device_type_tests` 精确 import 语句（`from torch.testing._internal.common_device_type`），标注"NOT `common_utils`"警告。Refactoring Standards 新增"验证每个新增 import 符号存在"规则。
- **checker.md 审查点**：新增审查点 #8 "分类正确性"——验证 S2 class 确实测试了 device 相关行为；per-rule scope 新增 strategy_2 专项检查——标记 `device` 参数无实际使用的测试（强烈信号：不应 S2）。
- **SKILL.md**：三策略表 S1 列补充"包括 device-agnostic 工具逻辑测试"；S2 列补充"不能仅因测试创建 tensor 就归 S2"。orchestrator 循环新增"启动 analyst 前快速浏览文件"步骤——若原文件是普通 `TestCase` 且测试主体是工具函数，预估大部分为 S1，对 analyst 的批量 S2 分类保持质疑。

### 文件变更

| 文件 | 描述 |
|------|------|
| `agent/prompts/analyst.md` | Task 4 重写：S1 默认 + S2 举证责任 + 工具函数显式归 S1 |
| `agent/prompts/coder.md` | strategy_2 首位 +import 指导；Refactoring Standards +import 验证规则 |
| `agent/prompts/checker.md` | +审查点 #8 分类正确性；scope 模板 +strategy_2 专项检查 |
| `SKILL.md` | 三策略表 S1/S2 描述收紧；orchestrator 循环 +pre-analyst 浏览步骤 |

## 2026-08-04 — HardwareClassification 自动标注

基于社区 PR #190508 引入的 `HardwareClassification` enum（`torch.testing._internal.common_utils`），工作流现在自动为重构后的每个 test class 添加 `hw_classification` 类属性，使测试运行器可通过 `--hw-classification` 按硬件类别过滤执行。

### 改进要点

- **自动标注**：coder agent 在重构时自动为每个 class 添加 `hw_classification = HardwareClassification.XXX`，按策略映射：
  - S1 (CPU-only) → `HardwareClassification.GENERIC`（或 `CPU`，若使用 `instantiate_device_type_tests(only_for="cpu")` for `@ops`）
  - S2 (device-agnostic) → `HardwareClassification.ACCELERATOR`
  - S3 (device-specific) → `HardwareClassification.CUDA` / `MPS` / `XPU`（按设备）
- **验证检查 H1**：`verify.py` 新增 `_check_hw_classification()`，自动检测 import 是否存在、每个 TestCase 子类是否标注、标注值是否匹配 class 机制（通过 `instantiate_device_type_tests` 调用模式 + `setUp` guard + `device` 参数推断预期值）
- **全链路覆盖**：analyst → coder → checker → verify 四阶段均纳入 `hw_classification` 检查

### 文件变更

| 文件 | 描述 |
|------|------|
| `utils.py` | +`HW_CLASSIFICATION_IMPORT`, +`HW_CLASSIFICATION_MAP`, +`STRATEGY_TO_HW_CLASSIFICATION` |
| `state.py` | `NewClassSpec` +`hw_classification`; `AnalystReport` +`hw_classifications` |
| `CLAUDE.md` | 三策略表 +`hw_classification` 列 + import 说明 |
| `agent/prompts/coder.md` | +6 值标注要求（Refactoring Standards 章节）；每个策略指导 +`hw_classification` 精确值 |
| `agent/prompts/checker.md` | 审查点 #8：每个 class 必须标注正确 `HardwareClassification` |
| `agent/prompts/analyst.md` | JSON 输出 +`hw_classifications` 字段；`new_classes` +`hw_classification` |
| `agent/skills/refactor-test-decoupling/SKILL.md` | 所有策略代码示例 +`hw_classification`；每个策略 Steps +标注步骤；Pitfalls +1；Instantiation 表 +`hw_classification` 列 |
| `agent/skills/review-test-refactoring/SKILL.md` | 审查清单 #9：`HardwareClassification Tag`（import/存在性/正确性/字母序）；Pitfalls +3（Blocker 级） |
| `scripts/verify.py` | +`_check_hw_classification()` + 3 个 helper（`_find_test_classes_with_bodies`, `_extract_hw_classification`, `_infer_hw_classification`），启发式推断预期值 |
| `scripts/report.py` | 策略分配行追加 `hw_classification` 括号注释 |

### 数据来源

- PyTorch PR #190508 (`HardwareClassification` enum 定义，6 值)
- 社区落地 PRs: #191889, #191909, #191913 (import 风格、属性放置位置、per-category linter 规则)
- `tools/linter/adapters/hw_classification_linter.py` (PR #190173) — 每类约束规则

## 2026-07-31 ~ 08-03 — 工作流改进与全流程验证（test_reductions.py × PR #185881）

以合入 PR #185881（545b05f → 341a9a2）为 gold standard，两轮迭代改进后全流程评估。评估产物在 `eval_scratch/`。

> **结论**：工作流结构正确，瓶颈在分类精度。两轮改进后分类准确率 77% → 80%（+3pp），类拆分/MPS 安全/过期符号检测等 5 项满分通过。唯一差距 G6（`apply_` carve-out 缺失，6 个误分类），`analyst.md` 单点修复后预计 → ~100%。

### 改进要点

- **类拆分支持**：`state.py` +`NewClassSpec`，`analyst.md` +Task 7，`flow.py` 从 analyst 报告生成类提取任务。此前 0% 覆盖率。
- **@onlyCPU 分类启发式**：两轮迭代——第一轮 6 个增强启发式 + tiebreaker；第二轮修复 5 个可泛化差距（Scope Guard、三级决策、扩展 S1 指标、优先级规则、上下文线索），误分类率 23% → 预估 <5%。
- **MPS 安全规则**：`coder.md` 3 场景覆盖 + `verify.py` `_check_skipifmps_coverage()`。
- **类拆分验证**：`verify.py` `_check_class_split()`。
- **Helper 重构 + 过期符号**：`coder.md` helper 重构指导，`analyst.md` stale_import → stale_symbol，`verify.py` `_STALE_SYMBOLS`。

### 全流程验证（08-03）

155 个独立测试，整体准确率 **80.0%** (24/30)。S1 F1: 0.80，S2 F1: 0.80。

**✅ 满分 (5/5)**：过期符号 100%、白名单/黑名单 100%、类拆分正确、测试计数 155、S3 假阳性 0。

**🔴 G6**：`apply_` 调用链被误判为 numpy-bound → S1，实际可机械替换为 `torch.empty` + `.copy_()` → S2。影响 6 个 `test_*_dim` 测试，涟漪传导至 P3-P7。tiebreaker tier 1 新增 `apply_` carve-out 修复。

**🟡 次要**：评估指南 gold label 一处勘误；`assess.py` 测试计数无缩进感知；`verify.py` `_check_skipifmps_coverage` 依赖 `git show HEAD` 可能在重构后失效；`flow.py` 单 coder 处理 4110 行文件存在上下文压力。

### 文件变更

| 文件 | 变更 |
|------|------|
| `agent/prompts/analyst.md` | +Task 7, +6 @onlyCPU 启发式, +tiebreaker, +Scope Guard, +扩展 S1 #4, +优先级规则, +三级决策, +上下文线索, +`apply_` carve-out, stale_import→stale_symbol |
| `agent/prompts/coder.md` | +MPS 安全 3 场景, +类提取指导, +helper 函数重构 |
| `state.py` | +`NewClassSpec`; `AnalystReport` +`new_classes` +`onlycpu_evaluations` |
| `flow.py` | `_phase_distribute` 类提取; `_finding_matches_rule` 更新 |
| `scripts/verify.py` | +`_check_class_split()`, +`_check_skipifmps_coverage()`, +`_STALE_SYMBOLS` |

## 2026-07-28 — Workflow Improvements from Reviewer Feedback

从 10 个 PyTorch test refactoring PR 的 reviewer 反馈中提取 17 项改进（fffrog + albanD + others）。

### 🔴 阻塞级修复 (4)

- **B1 — `@dtypes` 折叠语义变更**：coder 在 S1 转换时将 `@dtypes` + `@unittest.expectedFailure` 折叠为 for 循环，丢失了 per-dtype xfail 独立性。coder.md 增加 `@parametrize` 转换指导，verify.py 新增 `_check_dtype_integrity()` 检测。
- **B2 — Import 重组导致 ImportError**：coder 将符号从 `common_device_type` 错误移到 `common_dtype`，整个测试文件加载失败。verify.py 用完整 Python import 替换 `py_compile` 语法检查。
- **B3 — MPS dtype 兼容性**：`@onlyCUDA` → `@onlyAccelerator` 或 `allow_mps=True` 后 float64/complex128 在 MPS 不支持。采用保守策略：首次覆盖 MPS 默认加 `@skipIfMPS`。
- **B4 — `current_device_index()` 类型不匹配**：返回 `int` 但和字符串比较（`int == str` 永远 False），导致合入后 revert。coder.md 新增 HIGH RISK API 警告，verify.py 新增 `_check_accelerator_type_safety()`。

### 🟠 重要修复 (9)

- **M1 — `@onlyCPU` 全流水线修复**（4 PRs，最高频）：`@onlyCPU` 从 ENLARGE → REMOVE（改为 device-agnostic）；每个 `@onlyCPU` 测试 MUST 单独评估。跨越 5 个文件协调修改（SKILL.md、coder.md、analyst.md、verify.py、review SKILL.md）。
- **M2 — Dynamo CI 文件一致更新**：coder.md 明确要求 BOTH `dynamo_skips/` AND `dynamo_expected_failures/` 必须更新，禁止用 `@unittest.skip` 掩盖 CI 失败。
- **M3 — 白名单扩大约束**：`@onlyCUDA` → `@onlyAccelerator` 仅当逻辑真正设备无关才扩大。测试无限制则移除，后端特定行为（NaN、确定性、精度）则保留 `@onlyCUDA`。
- **M4 — S3 分类/实例化重写**：S3 永远不用 `instantiate_device_type_tests`，改用 `TestCase` + `setUp` 守卫，命名 `TestFooOnCUDA`。修复 `if device_type == "cuda"` 被误分类为 S3 的问题。
- **M5 — Review checklist 补充 4 项**：重复测试体检测、残留 `TEST_CUDA` 检测、`@skipIfMPS` 无 `device` 参数警告、S2 类中残留 `torch.cuda.*` 检测。
- **M6 — 禁止新增 device_type/device 参数**：应从已有张量或测试参数推导，不新增冗余参数。
- **M7 — `@onlyNativeDeviceTypes` 与 `@onlyAccelerator` 不可互换**：前者包含 CPU，后者不包含。devive_api_catalog.yaml 新增 decorator_classification 章节，文档化为"不动它"。
- **M8 — 混合设备测试处理**：跨设备错误处理测试保留 CPU 张量显式声明，accelerator 张量用 `device` 参数。
- **M9 — 覆盖率保留检测**：verify.py 新增 `_check_coverage_preservation()`，对比 refactoring 前后 per-method 装饰器集合，装饰器范围扩大时发出警告。

### 🟡 次要修复 (4)

- **m1**：S2 类中 `_cuda` 后缀方法名/变量名检测
- **m2**：`device == "xpu"` → `device.type == "xpu"` 指导
- **m3**：移除 `@onlyNativeDeviceTypes` 前检查 dtype 兼容性
- **m4**：`flow.py` 新增 PR diff 大小估算及 500 行警告

### 文件变更

| 文件 | 描述 |
|------|------|
| `agent/prompts/coder.md` | +11 条 coder 指导 (B1-B4, M1-M4, M6, M8, m2) |
| `agent/prompts/analyst.md` | M1: `@onlyCPU` 强制逐测试审计 + `onlycpu_evaluations` 输出 |
| `scripts/verify.py` | +3 个新检查函数，+7 个已有检查增强，py_compile → 完整 import |
| `agent/skills/refactor-test-decoupling/SKILL.md` | M1/M3/M4/M6/M7/M8 共 6 处方法论修改 |
| `agent/skills/review-test-refactoring/SKILL.md` | B3/B4/M1/M3/M4/M5/m1/m3 共 8 处审查清单补充 |
| `CLAUDE.md` | S3 策略表更新 + 分类规则补充 |
| `reference/device_api_catalog.yaml` | 新增 `decorator_classification` 章节 |
| `reference/classification_guide.md` | `@onlyNativeDeviceTypes` 警告 + S3 分类规则 |
| `flow.py` + `scripts/logger.py` | PR scope 估算及警告 + `RefactorLogger.warning()` 方法 |

### 数据来源

基于以下 10 个 PR 的 review comments 分析：
#188130, #188043, #185802, #185798, #185699, #187926, #186352, #186351, #185881, #185797
