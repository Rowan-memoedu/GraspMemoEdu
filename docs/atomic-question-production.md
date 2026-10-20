# 原子可变设问的本地生产与机械导入

可变设问允许换题型，只用于初学通过后的复习。生产在本地运行，产物可按明确授权机械导入网站；线上耗尽触发、任务领取和服务器常驻生产尚未接入。每个既有记忆点默认每次生成两张卡，批量入口可指定新增数量，`point_ids` 仍指向该点。题型、生成来源与关联点数相互独立，继续使用统一 Flashcard 模型。

## 提示词

- [生成提示词](../prompts/atomic-question-generation.md)：包含角色、完整目标与方向约束、独立语境、题型适配、文本和公式要求、输入说明及输出协议。没有当前聊天的引用，也没有本批例子的期望变式答案。
- [独立审核提示词](../prompts/atomic-question-review.md)：重新依据题面作答，检查原目标、方向、答案键、条件、泄题和重复，拒绝时说明具体原因，不替作者改题。

调用标准 DeepSeek Chat Completions API；模型为配置的 `deepseek-flash`。没有训练、微调或修改模型权重。每次生成请求只有 system 提示词和一个 user JSON，后者包含同学科 few-shot 和当前任务；审核使用另一次独立请求，只提供 source 和 candidate。两类请求均不共享聊天历史。

## 同学科 few-shot 路由

输入来源已经提供 `subject_id`。程序直接以它检索已通过示例库，组装 `{"few_shot_examples":[{"source":{},"output":{}}],"task":{}}`；不让模型重新猜学科，不维护按学科分叉的生成提示词、判分或 Flashcard 业务。

当前示例库位于 `D:/Artifacts/GraspMemoEdu/atomic-examples/跨学科-API试跑/few-shot-examples.json`：六科分别采用用户已通过的五组阅读稿，计算机采用此前“基本合格”的首批示例中的前五组，共七科各五组。六科原始卡片不改写；计算机旧稿只机械适配字段、展开选项字母，并以原答案兼作解析。来源材料和示例库均留在本地，不进入源码仓库。

同学科示例默认全部提供；排除当前点本身和原卡题面相同的示例，避免把当前目标作为自己的演示答案。没有可用的同学科已通过示例时，在调用 API 前明确报错，不混用其他学科或静默退回无示例生成。新增学科或更换示例只更新注册数据与示例库。

示例只说明设问尺度和变化方式，本次事实及点绑定以 `task` 为准；输出使用统一 Flashcard 协议，数量由 `variant_count` 指定，缺省为两张。审核请求不携带 few-shot，独立依据本次来源与候选核对。运行记录保留所用示例快照及哈希。

## 最小输入

```json
{
  "subject_id": "学科注册 ID",
  "topic": {"id": "既有 Topic ID", "version": "内容版本"},
  "memory_chunk": {"id": "既有组块 ID", "content": "组块的完整相关内容"},
  "point": {"id": "既有点 ID", "target": "本点给定什么、要回忆什么，保留原提取方向"},
  "seed_card": {
    "point_ids": ["既有点 ID"],
    "question": "原始单点题面",
    "reference_answer": "完整答案",
    "interaction": {"version": 1, "type": "text", "grading": "semantic"}
  },
  "existing_variants": []
}
```

生成结果的卡片只提供 `point_ids`、`question`、`reference_answer`、`explanation` 和现成的 `interaction`。ID、入库时间、渲染 HTML、权限、版本核验及展示次序由程序负责，模型不提供这些运行状态。

## 本地运行

来源用例与结果放在 `D:/Artifacts/GraspMemoEdu/atomic-examples/跨学科-API试跑`。入口：

```powershell
& 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe' -B scripts/atomic_question_trial.py run `
  --input '<输入用例 JSON>' --run-root '<新的运行证据目录>' `
  --few-shots 'D:/Artifacts/GraspMemoEdu/atomic-examples/跨学科-API试跑/few-shot-examples.json' --workers 4
```

`--only <点ID...>` 只重跑指定失败项。完成的请求与结果保持原样；重启时复用已收到的响应。发现曾发起但未保存响应的请求时，保留结果未知状态，不自动重复付费调用。修订提示词或输入后使用新的证据目录，保留旧批次。

试跑用例可提供 `existing_variants`，直接映射到单点输入；缺省为空。本轮额外使用数学、英语各一个点，带入首批两张已生成卡，再独立生成两张新卡，模拟后续补题输入。此试跑只验证生成与去重，不代表学习者耗尽检测或后台任务调度已经实现。

凭据只通过 ai-production-supervisor 的配置读取 `DEEPSEEK_CODEX_API_KEY`；不借用网站判题、导入或范式变式的专用凭据。密钥不进入输入、响应、日志或 Git。

```powershell
& 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe' -B scripts/atomic_question_trial.py render `
  --input '<输入用例 JSON>' --runs '<首次运行目录>' '<失败项重跑目录>' `
  --output '<阅读稿目录>'
```

排版只机械展开题面、选项、判断命题、填空位置、配对项目、答案和解析。原始响应不改写。每次请求、原始响应、结构校验、AI 审核和提示词快照均单独保留。AI 审核通过仍需区分于用户验收。

## 本轮修订依据

- 配对／排序候选若已经与位置逐行对齐，会泄露答案；在通用生成和审核提示中禁止，并增加机械检测。
- 题型转换可以构成有效的呈现差异；同目标、同答案不能被审核器误判为换题失败。干扰项出现相邻知识词语，不等于必须考查该知识。
- 填空交互必须与题干标记一一对应，不能把普通括号或问答题干直接声明为 fill_blank。
- 单选应排除多个选项同时满足题意；纠错题应明确保留所考结构，防止删除该结构即可绕开目标。
- 校验器须区分数学不等式和 HTML，不能把不等号中的字母误判为标签。候选内容本身不通过手工修补来过关。

提示词保存在上述两个固定文件。耗尽检测、任务领取和服务器常驻生产仍属于后续阶段；本地内容生产及经授权的机械导入使用下述入口。

## 结构化 Topic 批量生产

`scripts/produce_atomic_questions.py` 读取 schema 2 的 `topics[].sections[]`，保留并列的 Introduction、记忆组块、记忆点与原卡。使用同一套生成、审核提示词和同学科 few-shot；`variant_count` 指定本次新增数量，缺省为 2。一次明确要求每点补 3 张时用 `--count 3`，不改变常规两张的默认值。

```powershell
& 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe' -B scripts/produce_atomic_questions.py `
  --input '<structured-topics.json>' --subject cs --count 3 --workers 16 --attempts 6 `
  --few-shots 'D:/Artifacts/GraspMemoEdu/atomic-examples/跨学科-API试跑/few-shot-examples.json' `
  --run-root '<新的本地运行目录>'
```

每点分别保留原始请求、响应、结构检查和独立审核。明确缺陷自动重新生成，失败理由进入下一轮输入；已通过点和已完成调用不重跑。结果未知的请求停止自动重试。输入、提示词和示例库哈希随批次固定。整批全部通过才输出 `structured-topics.enriched.json`；原文件不覆盖，原字段与原卡保持不变。`--only` 可限制本次处理的点。

新卡仍是统一 Flashcard，通过 `learning_phases: ["review"]` 限定用于复习。缺省卡片可用于初学和复习；所有记忆点仍须有可初学的单点卡。这个字段不新建卡片类型或记忆状态。`refill_required` 用于本地验证初学通过、剩一张和耗尽的边界；当前批量入口由用户明确启动，不连接网站自动领取需求。

## 机械导入

`scripts/prepare_structured_atomic.py` 把结构化批次准备为现有 `academy_import` 可安装的、带哈希的内容包。实际编译继续通过 `scripts/import_atomic_topic.mjs`，支持 `structured-atomic-v1` 来源：

- 多段指导与组块的句子定位保持精确对应；独立点机械归入其组块，卡片关联与内容不改写。
- 题面、答案、解析与代码使用既有 Markdown/MathJax 渲染器；代码中的 Unicode 转义和 `$` 按字面保存，来源 URL 只显示文字。
- 原图按来源字节嵌入并校验哈希，SVG 需通过无活动内容、无外部引用的检查。来源包、图片和答案侧车进入既有备份及恢复验证。
- 课程、章节和 Topic ID 由调用者明确指定。普通安装不覆盖已有 Topic。用户明确授权纯阅读升级时，才使用 `--reset-reading-progress`：保留原 ID 与章节，仅允许清理这些纯阅读 Topic 的旧阅读行，完整旧行存入私密恢复档案；包含作答或记忆状态的记录会拒绝清理。

```powershell
& 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe' -B scripts/prepare_structured_atomic.py `
  --input '<structured-topics.enriched.json>' --output '<新的导入包目录>' --subject cs `
  --course-id '<课程ID>' --course-title '<课程名称>' --unit-id '<章节ID>' --unit-title '<章节名称>' `
  --topic-prefix '<稳定的Topic前缀>'
```

`scripts/verify_structured_atomic.py --package <包目录> --output <新验收目录>` 在独立本地数据中验证原位升级、无关记录保留、重复安装、全包备份恢复、真实判题，以及原卡初学不评分、新卡复习按原点评分。阅读稿与机器验证分别保存。服务器安装继续使用既有维护暂存、备份、内容指针激活、服务重载与回读流程；这不部署原子设问后台生产服务。
