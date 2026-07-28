# Changelog

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