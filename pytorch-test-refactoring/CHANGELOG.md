# Changelog

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