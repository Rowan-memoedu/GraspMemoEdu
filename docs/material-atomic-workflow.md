# 通用原子工作流的材料入口

通用工作流位于 `D:/Projects/HyperskillRemNoteWorkflow/atomic_pipeline`。所有来源只有一个准备入口：`python -m atomic_pipeline prepare --input <来源声明.json>`。输入的 `source_format` 选择内部适配器，来源专用命令已移除。仓库名称不限定学科；`prompts/subjects/CS.md` 是按显式属性启用的独立扩展。普通材料与 Hyperskill 使用同一组生产、独立审核、确定性检查、原子实体导出、可变设问、编译与运行时服务。

| 衔接 | 实际入口与合同 |
| --- | --- |
| 所有来源 → 共用准备入口 | `atomic_pipeline prepare --input`；统一的规则、示例、输出、Topic 和单元筛选参数 |
| 自然章节、词条组 → 冻结单元 | `atomic-material-sections-v1` schema 1；完整 HTML、稳定身份、原始定位和本地原图 |
| Hyperskill 原译文 → 冻结单元 | `atomic-hyperskill-sections-v1` schema 1；声明 source、translated 目录及显式 subject，由内部适配器读取完整 Topic、step 和原译文文件对 |
| 单元 → Introduction、组块、原子点及种子卡 | 同一 `workflow.section_job`、`check`、独立审核和 `export_mapping`；模型工作记录为 facts/chunks/points |
| 独立知识与设问 → schema 2 | 同一 `export_contract.point_entities`；memory_points 与 flashcards 分离，通过 point_ids 关联，保留句子原文和 Unicode 范围 |
| 可变设问 | `scripts/produce_atomic_questions.py` 调用既有 trial 生成/独立审核，只追加 review 卡，保留种子卡和点身份 |
| 内容包 | `prepare_structured_atomic.py` → `import_atomic_topic.mjs` → `import_structured_atomic.mjs` → `structured_atomic.normalize/finalize`，验证原实体及锚点 |
| 平台学习 | 原 Catalog、统一 Flashcard、AtomicLearningService、评分与 FSRS；按内容能力选择，不按英语或 CS 分派 |
| 显性点 → 语境接口 | `scripts/export_atomic_context_points.py` 复用已有 `context_explicit.exported_points`；只做机械投影，不调用原文分析或作者阶段 |
| 隐性原文与复习题 | 原 `produce_context.py` 的 article/cards 接口；已有 `produce_explicit_context.py` 仍用于从隐性语境发现并独立审核显性目标，不取代上面的原材料原子链 |

逐个选用原材料自然单元；各自来源、规则和候选有独立快照，不要求先整合整门课程，也不先解决来源错误和资料冲突。来源适配不能代替章节内容选择。材料正文中的英语直接进入共用模型阶段，英文词形、搭配、语法和原例句不会被当作“英文对照档案”排除。普通材料的规则位于工作流的 `atomic_pipeline/prompts/material-rules.md`；历史技术材料的范围、翻译限制和 CS 规则不自动套用到英语。

## 运行

在工作流仓库统一运行 `prepare --input` / `run` / `verify`，合同与来源声明格式见该仓库 `ATOMIC-WORKFLOW.md`。`--topic-id` 与 `--section-id` 可重复；学科从来源声明读取。每个 batch 仅包含本次选定单元；`run --only` 可继续其中一个单元，其余状态保留。输入或规则变化使用新批次；实现修订后在原批次从冻结原材料重生受影响单元。已经通过且规则未变的单元继续复用。

在平台仓库按需使用既有可变设问和编译入口：

```powershell
$python = 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe'
& $python scripts/produce_atomic_questions.py --input '<批次/structured-topics.json>' --subject english --few-shots '<已认可的同学科示例库.json>' --run-root '<新设问批次>'
& $python scripts/prepare_structured_atomic.py --input '<结构化导出.json>' --output '<新内容包>' --subject english --course-id '<稳定课程ID>' --course-title '<课程名>' --unit-id '<稳定单元ID>' --unit-title '<单元名>' --topic-prefix '' --test-material
& $python scripts/export_atomic_context_points.py --input '<结构化导出.json>' --subject english --output '<known-points.json>'
```

可变设问是可选后续阶段；没有记忆点的完整指导仍可直接编译。`--test-material` 由现有安装器在切换内容前应用，技术验收不能混入正式内容。上述命令只准备本地包；线上安装仍遵循原有 SSH、备份、权限及学习状态保护流程。

图片优先使用来源已冻结的 `embedded_images`，校验格式、SHA256 及同 URL 的字节一致性。旧的仅提供 HTTPS URL 的输入仍保留原下载兼容行为。原始 source_locator 与源文件快照留在私密批次及来源包，公开正文不展示机器定位信息。

`export_atomic_context_points.py` 输出原有 `{schema:1, subject_id, points:[{id,target,context}], tasks:[]}`。选定后续隐性材料时再提供真实任务；投影本身不派发、不生成新 ID、不创建 FSRS 状态。`produce_context.py` 可用原点 ID 关联已有目标，`produce_explicit_context.py` 可用同一清单识别既有点，保留原显性/隐性接口。

## 验证与认可

- `ai_passed` 表示模型独立审核通过。
- `verification.json.verified` 表示确定性技术核验通过。
- `human_accepted` 保持独立，以上两者不自动授予用户认可。
- 原始来源、历次候选、审核与失败回执保留；不修候选来伪造工作流成功。

跨仓库验收分别使用各仓库原有 Python 环境，通过 schema 2 文件衔接，不把工作流依赖装入平台环境：

```powershell
$env:ATOMIC_WORKFLOW_ROOT = 'D:/Projects/HyperskillRemNoteWorkflow'
$env:ATOMIC_WORKFLOW_PYTHON = 'D:/AppData/HyperskillRemNoteWorkflow/venv/Scripts/python.exe'
& $python -m unittest discover -s tests -v
```

`test_material_workflow_compatibility.py` 的跨仓库测试仅模拟模型传输，实际执行共用分节链、可变设问校验、编译、隔离安装及学习服务，并核对原课程状态未变。工作流仓库 `test_atomic_materials.py` 另验证单元选择、失败恢复、旧提示兼容、CS 附加规则隔离和原图冻结。真实 Codex 调用的技术 fixture 回执需与这些传输模拟测试分别保留，均不是用户教学成果。
