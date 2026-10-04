# 作者导入：Markdown / JSON → Topic

正式后端已迁至腾讯云。本文本机命令用于准备来源包与隔离验收；正式安装必须将已验收包传到服务器，并在 `/opt/graspmemoedu/app` 使用 CLI 指定 `/var/lib/graspmemoedu`。本机原数据目录已是回滚快照，修改它不会更新正式网站。见 [腾讯云运维](tencent-deployment.md)。

Topic 内容与课程图谱独立导入。此入口不创建或覆盖图谱关系；图谱使用 `Import-Graph.ps1` / `graph-import`，可反复整体更新，详见 [图谱格式与工作流](course-graph.md)。两者通过稳定的 `topic_id` 关联，图谱可先于正文导入。

在后端电脑调用，网页不提供配置或上传入口。默认只机械导入已校准的 Markdown 或显式 v2 JSON：解析结构、按字段注入并搬运附件，不逐题检查数学、答案、图注或配图细节，不调用 API 改写内容。API 仅用于显式选择的极少量抽样检查整体性损坏。单篇入口加入已有学科、Course；整门新课程使用后文的课程安装入口。普通 Topic 不覆盖；v2 纯引论支持同 ID 更新，安装不写账号权限或学习数据库。

## 一条命令

```powershell
pwsh -NoProfile -File 'D:/Projects/GraspMemoEdu/Import-Topic.ps1' `
  -Source 'D:/Artifacts/我的物理稿.md' `
  -TopicId 'physics-my-topic' -Subject physics -CourseId test-physics-course
```

成功返回 `status: installed`（新增）或 `updated`（纯引论更新）、Topic 路径和审核包路径。随后按实际部署位置重载服务；以下脚本仅适用于本机隔离实例：

```powershell
pwsh -NoProfile -File 'D:/Projects/GraspMemoEdu/Start-PublicLearning.ps1' -RestartBackend
```

账号默认开放全部 Topic 和功能，新增内容自动继承；明确设置的 Topic 或功能列表覆盖各自默认值。权限管理仍在本机 CLI，见 [账号管理](accounts.md)。历史测试课程已下线，不随导入器更新重新发布。

## 输入格式

新格式见 [Topic v2 共享规范](topic-format-v2.md) 和 [JSON Schema](topic-format-v2.schema.json)。文件可以为 `.md` 或 `.json`，两个入口经过同一个纯 Python 归一器，再进入既有题目、图片和渲染流程；传入的 `TopicId` 必须与 v2 源文件的 `id` 相同。v2 只允许机械导入。

v2 支持多段 Introduction、与 Example 穿插的原稿顺序及纯引论 Topic；`content_order` 表达指导与例题的归属，普通 Topic 仍先选择例题是否掌握。Example 和 Practice 的既有 ID、题型、练习分组均保留。字段按共享规范固定归属：定义、定理、性质、识别特征、范式在 Introduction；要求、题源在题目正面；巧法、idea 在同题 Explanation，纯内部引用节点识别为 idea。新作者稿须先通过 `topic_format.validate_field_placement`，不能用保留原位置跳过归属整理。Callout、真实 RemNote 标签、Mermaid 和精确 Rem ID 引用保持；题源缺失字段省略，不输出占位语，难度仅留元数据。独立外链排除，混有正文的链接节点拒绝自动导入，待讨论后重新提供原稿。

未标记 v2 的旧 Markdown 沿用原有格式：两空格缩进的列表，一份文件恰好一个根 Topic、一个 Introduction、连续编号的 Example；每个 Example 与 Practice 各有一个非空 Explanation。每个 Example 至少 7 道 Practice，以满足现行练习补题机制。题量可因模块而异。

```markdown
- Topic 标题
  - Introduction
    - 概念、共同条件与公式。
  - Example 1
    - 完整题面。
    - Explanation
      - 推导与最终答案。
    - Practice 1
      - 完整题面。
      - Explanation
        - 推导与最终答案。
```

上例仅展示层级；实际文件须继续到 Practice 7 或更多。支持原有 Markdown/LaTeX 和本地 PNG 插图；主动 HTML、脚本、远程图片等不属于输入合同。字段不齐或题量不足会在调用 API 前失败。不会自动补题、改写源稿或猜测缺失条件。

## 本地插图

将 Markdown 与附件一起保存，例如 `topic.md` 引用 `![示意图](assets/rod.png)`。导入器只复制实际引用的 PNG，保留相对目录和原始字节；PNG 会内嵌进相应题面、解析或指导的 HTML，网页无需访问作者电脑的路径或额外图片服务器。

仅接受文档目录内的相对 `.png` 路径，拒绝绝对路径、上级目录、网络链接、符号链接、目录联接和伪装文件。图片经过 PNG 校验、尺寸和哈希检查；每张上限 8 MiB，边长最多 10000、像素总数最多 2000 万。SVG 原件可以留在交付目录，但本版本不导入 SVG 附件。图片按宽度自适应显示。

资产清单与源稿凭据绑定，只机械检查附件可读、格式有效、路径与哈希一致。换图后只更新受影响引用和哈希，不检查图中数学、方向、单位或图注细节。安装及整站备份携带相同附件。

`topic-diagrams` 生成的哈希注释仅在显示时忽略，原 Markdown 不变。其他任意 HTML 不因支持图片而被放开。

## 多篇准备与新建课程

`scripts/prepare_topic_batch.py --plan <JSON> --run-root <新目录> [--workers 1..4]` 可准备多篇独立审核包。计划的 `topics` 项包含 `topic_id`、绝对 `source` 路径和 `source_sha256`；可用多个 `--topic` 只运行指定项。源文件必须与计划哈希一致，每个 Topic 的失败和成功分别保存；失败重试使用新运行目录，不覆盖旧证据。

全部审核通过后，将对应 `package` 路径填入课程安装计划：

```json
{"course_id":"my-physics-course","subject_id":"physics","title":"课程名称","topics":[{"topic_id":"physics-my-topic","package":"D:/Artifacts/my-approved-package"}]}
```

```powershell
& 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe' scripts/install_author_course.py --plan '<课程安装计划.json>' --data-dir 'D:/AppData/GraspMemoEdu'
```

此入口只新增课程：先在隔离目录验证全部包，再切换课程与学科配置，保存切换前配置。已有图谱使用相同 Course/Topic ID，不重编号。原课程、学习记录和权限不改写；默认权限账号自动继承新内容。已有课程仍使用单 Topic 安装入口。失败留下的未激活内容供检查，不自动删除或覆盖。安装成功后按上文重载服务。

支持分组生产稿：每个 Example 下共 5 组、每组 3 道，依次标为 `Practice 1.1`、`Practice 1.2`、`Practice 1.3`、`Practice 2.1`，直到 `Practice 5.3`。所有 Practice 仍是 Example 的直接子节点，不增加分组容器。每个 Example 的分组编号从 1.1 重新开始；同一 Example 内不混用整数与分组编号，重复、缺号、乱序或不完整分组均拒绝导入。

导入保持源顺序和正文不变，将分组编号映射到普通连续编号：1.1→1、1.3→3、2.1→4、5.3→15。网站题目标题为 `Practice 1` 到 `Practice 15`，题目 ID 继续使用 `example-N-practice-M`。原始分组编号存入题目的 `source_practice` 元数据，包含 `label`、`group`、`member`，不显示在学生题面中。原有连续整数编号输入仍兼容，已有 Topic 和学习抽题机制不因这项导入支持改变。

## 标准流程与字段

1. 保存原始文件及引用附件快照，解析结构并生成稳定题目 ID、源码行号和哈希。
2. 将原稿 Explanation 完整映射到 `reference_answer`，使用通用答案类型及判题约束，不让 LLM 重写答案。
3. 按题目 ID 注入字段，渲染指导、题面和解析；检查字段覆盖、附件引用、哈希与题库结构，不判断教学内容正误。
4. 凭据明确标记为机械校验，不冒充语义审核通过。历史逐题 API 审核记录保留作为旧证据，不继续运行该流程。
5. 安装前通过确定性转换比较确认字段未损坏，再原子切换目录；原有内容、权限和学习记录不改写。这里的本地一致性检查不触发 API 或细节审核。

标题、Introduction、模块、题面和解析来自源文件；答案侧车由原稿机械映射。源文件、答案侧车、转换凭据与 Topic 用 SHA-256 关联。网站备份携带这些来源，恢复时核验字节及引用完整性。

## 分阶段调用

在项目目录运行；`--data-dir` 放在子命令之前。

```powershell
$python = 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe'
& $python -m graspmemoedu author-prepare --source 'D:/Artifacts/我的物理稿.md' --topic-id physics-my-topic --run-dir 'D:/Artifacts/GraspMemoEdu/author-import/runs/my-new-run'
& $python -m graspmemoedu author-install --package 'D:/Artifacts/GraspMemoEdu/author-import/runs/my-new-run' --subject physics --course test-physics-course
```

`author-prepare` 只产生审核包；`author-install` 不再次调用 LLM。`author-import` 合并两步。Course 有多个显式 Unit 时须提供 `--unit`（PowerShell 为 `-Unit`）。每次 prepare 使用新运行目录。后续发现问题只局部修复并验证受影响部分，不整篇重跑；保留已成功的提取、转换和审核结果。审核误报的人工纠正须附具体证据，保留原拒绝结论并明确标记人工核对，不能伪装成 LLM 自动通过。相同包重复 install 返回 `already_installed`。

局部接续使用以下入口，原包保持不变：

```powershell
& $python scripts/resume_topic_import.py --package '<原审核包>' --run-dir '<局部修复结果目录>'
```

有正文或附件修复时加 `--source '<更正后的MD>'`，按当前机械规则更新字段、引用和哈希，保留旧包，不重跑逐题 API。历史语义拒绝不作为机械导入门禁，也不被改写成模型批准。

默认运行材料位于 `D:/Artifacts/GraspMemoEdu/author-import/runs`；普通内容位于数据目录的 `imported-topics/<TopicId>`，纯引论版本位于 `imported-topics/<TopicId>--<version>`。原稿快照保留对应的 `source.md` 或 `source.json` 与原始字节。API 请求、响应、逐题理由和 `manifest.json` 留在本机，不能加入源码仓库。返回失败状态时进程退出码非零。未激活的残留目录或锁须核对原运行进程后处理，不自动删除。

## 纯引论的同 ID 更新

仅当现有与新内容都明确为 v2 `topic_kind: introduction`，且 Course、Unit 归属相同，才允许更新。每次独立重编译后保留不可变版本包与 `catalog-before.json`，只原子切换目录路径；安装不改学习数据库。相同包重复安装仍返回 `already_installed`，普通 Topic 同 ID 覆盖仍拒绝。

`version` 标识完整渲染产物；`content_version` 标识教学正文、树、顺序、引用和图片内容，排除行号、渲染器、难度及来源 URL 等元数据。网站负责据此安排重读和保留完成历史。安装后未打开过的新版本允许在整站备份中携带旧引论进度版本，恢复时严格核对版本清单；普通 Topic 的版本一致要求不变。纯引论不进入知识图谱，已预声明为图谱节点的同 ID 会拒绝安装，导入器不会删除或改写图谱。

## API 与扩展边界

默认机械导入无需 API 凭据。可选 API 抽样只检查字段丢失、大片乱码、图片未呈现等整体性损坏；每批最多抽两题，不按每篇循环抽样，不核算答案或评价配图细节。导入及内容转换使用独立 `GRASPMEMOEDU_IMPORT_API_KEY`；Windows 用户变量优先于继承的进程值，每次创建客户端重新读取。缺失或为空时停止，不回退到 Codex 通用外包、网站判题、旧 Markdown 或其他功能的密钥。可选 `GRASPMEMOEDU_IMPORT_BASE_URL`、`GRASPMEMOEDU_IMPORT_MODEL` 默认分别为 `https://api.deepseek.com`、`deepseek-flash`，只允许 DeepSeek 官方 HTTPS 地址。未经明确要求不启动历史逐题提取或审核。

```powershell
& $python scripts/sample_import_integrity.py --package '<包1>' '<包2>' --output '<抽样记录新目录>' --max-questions 2
```

`--package` 一次传入整批包，整批合计最多两题。该入口需显式调用，普通导入和安装不会触发。2026-09-21 已将 22 篇 Topic 安装到物理课程“运动学进阶”（`rigid-constraints`）；原数学课程、图谱及学习记录保留。

准备包逻辑在 `graspmemoedu/author_import.py`，安装在 `graspmemoedu/topic_install.py`，归一器在 `graspmemoedu/topic_format.py`，渲染沿用 `scripts/import_course.mjs`，共享图片校验在 `graspmemoedu/topic_assets.py`。后续新增输入格式或内容类型时在这些边界扩展。当前不支持覆盖普通 Topic 或任意 Markdown 排版。
## 通用题型扩展

判断题、多空填空、带干扰项配对及复刻/复现判题，使用每题的显式 `Interaction` 节点；字段、例子和边界见 [题型与判题](question-types.md)。不写此节点的原课程保持现有行为。
