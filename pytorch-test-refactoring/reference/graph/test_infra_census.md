# Graph-mode test infrastructure census

- repo: `/root/pytorch` @ `79f8150273e01d2c62baf6d45967703632d7891e`
- field list: `/root/.agents/skills/pytorch-test-refactoring/reference/graph/test_list.txt`
- listed paths: 582 lines / 582 unique / 0 missing on disk
- routed by `tools/testing/discover_tests.py`: 468

## Families in scope

| family | listed files | routed | LOC |
|---|---:|---:|---:|
| fx | 28 | 5 | 22239 |
| dynamo | 129 | 129 | 195837 |
| inductor | 162 | 162 | 229168 |
| aoti | 7 | 6 | 15496 |
| export | 31 | 31 | 36027 |
| onnx | 34 | 30 | 26655 |

## Out of scope

| family | listed files |
|---|---:|
| cpp/jit | 1 |
| cpython | 45 |
| functorch | 19 |
| higher_order_ops | 7 |
| jit | 95 |
| lazy | 9 |
| tensorexpr | 2 |
| top-level compile utilities | 2 |
| top-level graph IR | 11 |

## fx - generation and coupling mechanisms (lines, files)

- `hw_classification`: 38 lines / 21 files
- `@parametrize`: 14 lines / 4 files
- `instantiate_parametrized_tests`: 9 lines / 4 files
- `instantiate_device_type_tests`: 5 lines / 2 files
- `"cuda" literal`: 2 lines / 2 files
- `make_test_cls_with_patches`: 2 lines / 1 files
- `copy_tests`: 2 lines / 1 files
- `TestFailure`: 2 lines / 1 files
- `only_for=`: 2 lines / 1 files
- `@ops`: 2 lines / 2 files
- `torch.cuda.is_available()`: 1 lines / 1 files
- `torch.xpu`: 1 lines / 1 files
- `@onlyCPU`: 1 lines / 1 files

## fx - base classes (AST expressions, count)

- `TestCase`: 45
- `JitTestCase`: 13

## fx - harness imports (files)

- `torch.testing._internal.common_utils`: 27 files
- `unittest`: 10 files
- `torch.testing._internal.jit_utils`: 6 files
- `torch._dynamo.testing`: 2 files
- `torch._inductor.test_case.TestCase`: 1 files
- `torch.testing._internal.inductor_utils`: 1 files

## dynamo - generation and coupling mechanisms (lines, files)

- `@parametrize`: 355 lines / 38 files
- `torch.cuda.*`: 199 lines / 13 files
- `device="cuda`: 169 lines / 15 files
- `hw_classification`: 111 lines / 50 files
- `GPU_TYPE`: 108 lines / 10 files
- `instantiate_parametrized_tests`: 95 lines / 39 files
- `"cuda" literal`: 91 lines / 15 files
- `HAS_GPU`: 74 lines / 12 files
- `instantiate_device_type_tests`: 54 lines / 24 files
- `torch.cuda.is_available()`: 43 lines / 12 files
- `@requires_cuda_and_triton`: 35 lines / 5 files
- `@onlyAccelerator`: 22 lines / 5 files
- `HAS_CUDA_AND_TRITON`: 22 lines / 4 files
- `mps`: 18 lines / 5 files
- `torch.xpu`: 17 lines / 5 files
- `@requires_triton`: 15 lines / 2 files
- `@requires_gpu`: 15 lines / 3 files
- `TEST_CUDA`: 13 lines / 4 files
- `.cuda()`: 8 lines / 2 files
- `only_for=`: 6 lines / 6 files
- `@onlyCUDA`: 6 lines / 2 files
- `make_test_cls_with_patches`: 6 lines / 3 files
- `mtia`: 4 lines / 2 files
- `skipOps`: 2 lines / 1 files
- `@ops`: 2 lines / 2 files
- `@skipCUDAIf`: 2 lines / 1 files
- `@skipXPUIf`: 2 lines / 1 files
- `@slowTest`: 1 lines / 1 files

## dynamo - base classes (AST expressions, count)

- `torch._dynamo.test_case.TestCase`: 222
- `TestCase`: 74
- `torch._inductor.test_case.TestCase`: 18
- `LoggingTestCase`: 8
- `PytreeRegisteringTestCase`: 3
- `DynamoTestCase`: 3
- `InductorTestCase`: 2
- `DynamoDistributedMultiProcTestCase`: 1
- `DynamoDistributedSingleProcTestCase`: 1
- `MinifierTestBase`: 1
- `JitTestCase`: 1
- `torch.testing._internal.common_utils.TestCase`: 1

## dynamo - harness imports (files)

- `torch._dynamo.test_case.TestCase`: 130 files
- `torch.testing._internal.common_utils`: 101 files
- `unittest`: 76 files
- `torch._dynamo.testing`: 69 files
- `torch.testing._internal.inductor_utils`: 21 files
- `torch._inductor.utils`: 15 files
- `torch._inductor.test_case.TestCase`: 7 files
- `torch.testing._internal.jit_utils`: 1 files

## inductor - generation and coupling mechanisms (lines, files)

- `GPU_TYPE`: 3568 lines / 98 files
- `@parametrize`: 1341 lines / 71 files
- `device="cuda`: 889 lines / 30 files
- `"cuda" literal`: 573 lines / 58 files
- `torch.cuda.*`: 564 lines / 39 files
- `@requires_gpu`: 403 lines / 32 files
- `HAS_GPU`: 293 lines / 70 files
- `TestFailure`: 265 lines / 6 files
- `instantiate_parametrized_tests`: 194 lines / 62 files
- `@skipCUDAIf`: 137 lines / 13 files
- `mps`: 118 lines / 17 files
- `@skipXPUIf`: 82 lines / 5 files
- `hw_classification`: 75 lines / 20 files
- `HAS_CUDA_AND_TRITON`: 66 lines / 17 files
- `instantiate_device_type_tests`: 63 lines / 26 files
- `torch.cuda.is_available()`: 54 lines / 21 files
- `@requires_cuda_and_triton`: 50 lines / 7 files
- `@skipIfRocm`: 49 lines / 14 files
- `copy_tests`: 45 lines / 14 files
- `.cuda()`: 44 lines / 5 files
- `@requires_triton`: 32 lines / 6 files
- `torch.xpu`: 31 lines / 15 files
- `@onlyOn`: 28 lines / 2 files
- `TEST_CUDA`: 27 lines / 5 files
- `mtia`: 20 lines / 8 files
- `only_for=`: 19 lines / 13 files
- `@onlyCUDA`: 18 lines / 3 files
- `@ops`: 8 lines / 4 files
- `@slowTest`: 6 lines / 3 files
- `make_test_cls_with_patches`: 6 lines / 3 files
- `@onlyCPU`: 6 lines / 2 files
- `skipOps`: 4 lines / 2 files
- `@onlyAccelerator`: 4 lines / 1 files
- `@expectedFailure`: 3 lines / 2 files
- `add_test_failures`: 3 lines / 2 files
- `@onlyNativeDeviceTypes`: 1 lines / 1 files

## inductor - base classes (AST expressions, count)

- `TestCase`: 356
- `InductorTestCase`: 75
- `TestMixin`: 11
- `TestBase`: 7
- `torch._inductor.test_case.TestCase`: 6
- `torch._dynamo.test_case.TestCase`: 4
- `MultiProcessTestCase`: 4
- `BlockDescriptorTestBase`: 4
- `test_torchinductor.TestCase`: 3
- `unittest.TestCase`: 2
- `MinifierTestBase`: 2
- `DynamicShapesTestCase`: 2

## inductor - harness imports (files)

- `torch._inductor.utils`: 227 files
- `torch._inductor.test_case.TestCase`: 184 files
- `unittest`: 146 files
- `torch.testing._internal.common_utils`: 143 files
- `torch.testing._internal.inductor_utils`: 138 files
- `torch._dynamo.testing`: 30 files
- `torch._dynamo.test_case.TestCase`: 10 files

## aoti - generation and coupling mechanisms (lines, files)

- `GPU_TYPE`: 214 lines / 4 files
- `mps`: 38 lines / 2 files
- `"cuda" literal`: 29 lines / 3 files
- `hw_classification`: 19 lines / 4 files
- `copy_tests`: 16 lines / 3 files
- `@parametrize`: 14 lines / 1 files
- `TestFailure`: 13 lines / 3 files
- `HAS_GPU`: 12 lines / 3 files
- `@requires_gpu`: 10 lines / 1 files
- `torch.cuda.*`: 7 lines / 1 files
- `instantiate_parametrized_tests`: 3 lines / 2 files
- `instantiate_device_type_tests`: 2 lines / 1 files
- `@skipCUDAIf`: 1 lines / 1 files

## aoti - base classes (AST expressions, count)

- `TestCase`: 19
- `LoggingTestCase`: 2
- `AOTICustomOpTestCase`: 2

## aoti - harness imports (files)

- `torch._inductor.test_case.TestCase`: 12 files
- `unittest`: 6 files
- `torch.testing._internal.common_utils`: 6 files
- `torch.testing._internal.inductor_utils`: 6 files
- `torch._inductor.utils`: 3 files
- `torch._dynamo.testing`: 2 files

## export - generation and coupling mechanisms (lines, files)

- `@parametrize`: 43 lines / 6 files
- `hw_classification`: 39 lines / 20 files
- `"cuda" literal`: 35 lines / 9 files
- `device="cuda`: 35 lines / 4 files
- `TEST_CUDA`: 20 lines / 3 files
- `instantiate_device_type_tests`: 17 lines / 7 files
- `GPU_TYPE`: 16 lines / 1 files
- `make_test_cls_with_mocked_export`: 12 lines / 8 files
- `instantiate_parametrized_tests`: 11 lines / 5 files
- `skipOps`: 8 lines / 2 files
- `@requires_gpu`: 7 lines / 1 files
- `@ops`: 7 lines / 2 files
- `torch.cuda.*`: 6 lines / 2 files
- `only_for=`: 5 lines / 4 files
- `@requires_cuda_and_triton`: 5 lines / 1 files
- `torch.cuda.is_available()`: 4 lines / 2 files
- `HAS_GPU`: 2 lines / 1 files
- `@onlyAccelerator`: 2 lines / 1 files
- `@skipXPUIf`: 2 lines / 2 files
- `.cuda()`: 1 lines / 1 files
- `torch.xpu`: 1 lines / 1 files
- `@requires_triton`: 1 lines / 1 files

## export - base classes (AST expressions, count)

- `TestCase`: 45
- `_ConverterTestMixin`: 2
- `TorchTestCase`: 1

## export - harness imports (files)

- `torch.testing._internal.common_utils`: 24 files
- `unittest`: 17 files
- `torch._dynamo.test_case.TestCase`: 17 files
- `torch.testing._internal.inductor_utils`: 4 files
- `torch._dynamo.testing`: 2 files

## onnx - generation and coupling mechanisms (lines, files)

- `hw_classification`: 40 lines / 27 files
- `"cuda" literal`: 12 lines / 3 files
- `instantiate_parametrized_tests`: 10 lines / 9 files
- `instantiate_device_type_tests`: 7 lines / 2 files
- `.cuda()`: 2 lines / 2 files
- `only_for=`: 2 lines / 2 files
- `torch.cuda.is_available()`: 1 lines / 1 files
- `DecorateInfo`: 1 lines / 1 files
- `torch.cuda.*`: 1 lines / 1 files

## onnx - base classes (AST expressions, count)

- `common_utils.TestCase`: 31
- `pytorch_test_common.ExportTestCase`: 9
- `_BaseTestCase`: 1

## onnx - harness imports (files)

- `torch.testing._internal.common_utils`: 29 files
- `unittest`: 5 files

## Sentinel ledgers

| ledger | entries | device-keyed |
|---|---:|---:|
| `test/dynamo_skips` | 3997 | 3515 |
| `test/dynamo_expected_failures` | 3791 | 139 |
| `test/inductor_skips` | 6 | 5 |
| `test/inductor_expected_failures` | 363 | 357 |
| `test/compiled_autograd_skips` | 1205 | 867 |

## Highest hardcoded-CUDA concentration

| file | family | markers | per 1k LOC |
|---|---|---:|---:|
| `test/inductor/test_user_streams.py` | inductor | 302 | 97.9 |
| `test/dynamo/test_ctx_manager.py` | dynamo | 141 | 30.8 |
| `test/inductor/test_cutlass_backend.py` | inductor | 67 | 21.4 |
| `test/inductor/test_cudagraph_trees.py` | inductor | 64 | 8.1 |
| `test/inductor/test_cpu_repro.py` | inductor | 39 | 4.8 |
| `test/dynamo/test_streams.py` | dynamo | 36 | 11.0 |
| `test/inductor/test_flex_flash.py` | inductor | 35 | 10.3 |
| `test/inductor/test_torchinductor.py` | inductor | 34 | 1.5 |
| `test/inductor/test_nv_universal_gemm.py` | inductor | 32 | 5.6 |
| `test/inductor/test_fp8.py` | inductor | 28 | 9.5 |
| `test/inductor/test_async_compile.py` | inductor | 19 | 8.1 |
| `test/dynamo/test_activation_checkpointing.py` | dynamo | 19 | 4.6 |
| `test/inductor/test_cuda_repro.py` | inductor | 17 | 4.4 |
| `test/inductor/test_flex_attention.py` | inductor | 16 | 1.4 |
| `test/inductor/test_custom_op_autotune.py` | inductor | 14 | 9.0 |

## Listed paths the runner does not execute

Out of scope: 86 (see the out-of-scope table). In scope: 28.

- `test/fx/test_common_passes.py`
- `test/fx/test_cse_pass.py`
- `test/fx/test_dce_pass.py`
- `test/fx/test_dynamism.py`
- `test/fx/test_future.py`
- `test/fx/test_fx_const_fold.py`
- `test/fx/test_fx_node_hook.py`
- `test/fx/test_fx_param_shape_control_flow.py`
- `test/fx/test_fx_split.py`
- `test/fx/test_fx_split_node_finder.py`
- `test/fx/test_fx_traceback.py`
- `test/fx/test_fx_xform_observer.py`
- `test/fx/test_gradual_type.py`
- `test/fx/test_graph_pickler.py`
- `test/fx/test_lazy_graph_module.py`
- `test/fx/test_matcher_utils.py`
- `test/fx/test_net_min_base.py`
- `test/fx/test_opaque_infrastructure.py`
- `test/fx/test_partitioner_order.py`
- `test/fx/test_pass_infra.py`
- `test/fx/test_source_matcher_utils.py`
- `test/fx/test_subgraph_rewriter.py`
- `test/fx/test_z3_gradual_types.py`
- `test/inductor/test_aoti_cross_compile_windows.py`
- `test/onnx/model_defs/op_test.py`
- `test/onnx/test_models.py`
- `test/onnx/test_onnxscript_no_runtime.py`
- `test/onnx/test_pytorch_onnx_onnxruntime_cuda.py`

