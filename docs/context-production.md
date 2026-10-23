# 本地语境材料生产

本地工作流有两个方向：从原文自动生成完整显性 Topic，从选定记忆点生成可用于复习的隐性语境题。前者包含已有点匹配、新点发现、语境义项决策、Introduction、记忆组块、记忆点和初学卡；后者生成新语境中的练习。生成、独立审核、材料复用、音频制作和机械导入分开留存；不连接线上常驻生产，也不写学习者进度。

`graspmemoedu/context_production.py` 使用学科注册表、既有题型和题内映射合同，不按学科名称分派业务。`scripts/produce_context.py` 保留既有 API 传输，也支持 `--backend codex-exec`；后者不读取旧 API 凭据，生成与审核分别调用官方 Codex CLI。新生成的语境卡仍是普通 Flashcard，统一标为 `learning_phases: ["review"]`；每个点的初学卡由显性来源提供。

## 输入

输入是 schema 1 的 JSON：

```json
{
  "schema": 1,
  "subject_id": "english",
  "points": [{"id": "stable-point-id", "target": "完整提取目标", "context": "相关指导"}],
  "tasks": [{
    "id": "stable-task-id", "kind": "cards", "topic_id": "stable-topic-id",
    "point_ids": ["stable-point-id"], "format": "语境题", "count": 1,
    "requirements": "题型、材料尺度及需要保留的提取方向"
  }]
}
```

`kind: "article"` 的任务增加 `article.paragraphs: [{id,text}]`。返回的每条已有点匹配和新点建议都有原段落、精确引用及理由；程序将引用转换为 Unicode 字符位置。引用不存在、出现多次、指向未知点或重复时拒绝。新点建议不获得正式记忆点 ID，也不会自动进入课程或 FSRS。

`kind: "cards"` 可以提供 `provided_cards`，只审核已有来源卡、不重新生成。这用于原有材料或可编辑显性指导的来源审核；输出审核通过不等于用户已经验收。准备导入时，原卡的题面、答案、解析、交互和关联必须与独立审核的快照相同，不能用同名点的一张别的卡代替审核。

生成使用既有选择、判断、填空、配对、语义文本及复合题控件。模型提供的复合题分段采用纯文本 `sections[].text`，程序转义后生成 `html`。不信任模型生成的活动 HTML。题内精确映射使用 `assessment.item_points`；语义写作等仍按整卡判定，不能伪造逐项精确评分。

## 完整材料与多个题号

一篇完整材料及其配套小题使用一个 `kind: "cards"`、`count: 1` 的任务。将同一 Topic 内实际考查的点列在任务 `point_ids` 中，在 `requirements` 中明确要求完整原文、所有题号、题型与逐题解析。产物仍是一张普通 Flashcard；整张卡通过 `point_ids` 关联多个点，各小题再通过 `assessment.item_points` 关联其中一个或多个点。关联应由作答所需的知识决定，不能因原文出现某个词就把对应点挂上去。

需要逐题精确判定时，使用 V2 `composite`、`grading: "exact"`，每个题号对应稳定控件 ID，题面 label 保留可见编号。原文在 `question` 完整呈现一次，小题与选项放入控件及其所属分段；完形只展示含缺口的原文。每个控件应有非空点映射，整卡关联应恰好等于映射并集，避免未映射点回退整题结果而掩盖局部差异。答案集合须包含题面支持的合理表达，单选必须只有一个正确项。

验证多对多关联时，可让一个小题真实联合考查两个点，其中一个点也由另一小题考查，并保留整卡内另一个独立的点。全对与仅该联合小题错误的两次隔离作答，应分别核对逐题结果、各点评分、每点一次 FSRS 更新及重复提交幂等性；另一题答对不能抵消同点的错误。题外的学习记录应保持不变。具体判定和推送规则沿用[语境学习](context-learning.md)与[统一 Flashcard 模型](topic-training-modes.md)，不增加专属卡型或排程。

这些完整材料使用同一 `run` 的生成、盲答及来源复核，并可与原有小型语境任务一同传给 `prepare`。追加任务时原任务与点对象保持原样，已通过记录继续从同一材料库复用；新增材料需提供已使用题面作为避重复资料，不能把原文或讲解中的已解句子重新包装成迁移题。

## 计划、生产与复用

```powershell
python scripts/produce_context.py plan --input <tasks.json> --library <材料库目录>
python scripts/produce_context.py run --input <tasks.json> --library <材料库目录> --run-root <新运行目录> --workers 4 --attempts 3
```

两个命令加 `--backend codex-exec` 即要求使用 Codex Exec；默认 `api` 保持旧脚本兼容。材料库按执行后端筛选，旧 API 结果不能充当 Exec 的新生产。可选的 `--codex-model` 是显式模型覆盖；省略时使用当前 Codex 配置的模型及推理强度。`--codex-timeout` 指定单次调用上限，超时保留证据并停止，不盲目再发请求。

`plan` 只验证来源并查询本地材料库，不调用模型。材料以稳定目标、完整来源、题型和要求的哈希检索；只复用完整校验且独立审核通过的记录。任务显示名称改变不妨碍复用，目标或要求改变则不能误用旧材料。每条记录包含内容哈希、审核结果与生产证据；损坏记录会报错而不是静默跳过。

Codex Exec 题目生产使用三个独立会话：生成、仅看学习者题面的盲审、结合来源与答案的复核。盲审只接收题目、公开控件和需要播放的内容，不接收目标知识点、原答案或生成对话；复核逐项检查盲审提出的答案与合理变体。原文分析仍使用生成和独立复核两个会话。请求、原始响应、候选、逐项审核和失败原因保存在运行目录。明确的内容缺陷自动反馈给下一次生成；已经通过的材料保留。程序内部错误及结果未知的请求不会盲目重发。

盲审列出开放填空或文本题的所有合理答案，不因存在多种表达就推断未提供的后台答案漏项。题面缺少必要信息、无法作答、泄题或单选题存在多个正确选项仍须拒绝；真实答案表是否覆盖这些表达，由后续来源复核判断。历史证据只允许与保留的旧提示或当前提示逐字匹配，且原始响应、输入和通过结果仍须完整验证；新调用使用当前提示，不把旧拒绝意见改成通过。

当前题面质量规则为 `standalone-context-v1`：卡片标题不得揭示答案，阅读和完形必须有完整材料，填空不得依赖未展示的“本文”或“上题”，精确判定需覆盖合理等价答案。讲解使用完整例句，初学与迁移题使用不同语境；普通教学例子不附生产说明。质量规则参与复用键，旧材料可以读取，但不能直接充当新规则下的通过证据。

修订审核流程而生成条件未变时，可使用 `--candidate-runs <旧运行目录>`。程序仅复用输入和生成提示词哈希均一致、结构重新通过的原始生成响应，然后使用新审核流程；不会手改候选。所有原始失败证据继续保留。`--only <任务ID...>` 可在同一冻结输入下补齐选定任务。

生成和盲答均已通过、来源审核因容量等执行错误明确终结且没有任何输出时，可显式增加 `--reuse-learner-runs <旧运行目录...>`，并指定新的 `--run-root`。此入口仅用于 Codex Exec 生成卡，与 `--candidate-runs` 互斥；选定的每个任务都先离线认证相同来源、原始生成、盲答公开题面和实际回执，全部通过才调用模型。新目录原样保留已完成阶段的文件和失败调用证据，仅重新执行来源审核。超时、仍在运行、无终止事件、半输出、已有结果或语义拒绝均不能走此入口；语义拒绝继续从原始输入重新生成，不修改候选。原运行中的 `outcome_unknown` 状态作为历史记录保留，不能仅凭该标签判断是否可恢复。

## 准备来源包

```powershell
python scripts/produce_context.py prepare --source <structured-topics.json> --tasks <tasks.json> --runs <已通过运行目录> --library <材料库目录> --output <新准备目录> --course-id <稳定课程ID> --course-title <课程名> --unit-id <稳定单元ID> --unit-title <单元名>
```

命令要求所有任务通过，核对每个来源卡与审核快照，再将生成卡机械追加到指定 Topic。输出包含完整结构化来源、逐题型覆盖表及文章分析。加入 `--compile` 时调用现有 `prepare_structured_atomic` 和 `import_atomic_topic.mjs`，产生原有安装器可用的内容包、答案侧车、机械审核记录和文件哈希清单。所有原点和原卡身份保持不变。输出已存在且内容不同会拒绝，不覆盖旧证据。

来源由下面的显性工作流生成时，`prepare` 可增加 `--explicit-runs <已通过显性运行目录...>`，复用其已完成的独立审核。程序回读真实 Exec 记录，并要求整个原始 Topic 与已审核记录的机械投影完全一致，才认可其中的指导、点和初学卡；任何来源改动均拒绝。此路径无需再为同批初学卡创建 `provided_cards` 任务。新生成的语境卡仍需独立生成和审核，原有人工来源审核路径不变。

## 从阅读记录回到本地生产

阅读页导出的本人候选记录使用 `schema: 1, kind: "reading-context-export"`。`records` 每项保存 `topic_id`、`material_id`、`content_version`、`paragraph_id`、原 `quote`、原段落 `context`、`note`、全部 `definitions`、`point_ids`、`memory_status` 和 `stale`。

`quote` 兼容原文字符串和浏览器的 `{exact,start,end,prefix,suffix}` 锚点；后者按 Unicode 字符位置核对原段落，完整保留，不改写为截断的词条。已有的 `dictionary`（义项 ID、词典版本、来源及许可）、`selected_sense_id`、`contextual_sense_selected` 也原样留存，未选义状态不因导出而改变。

```powershell
python scripts/produce_context.py from-reading --input <本人阅读导出.json> --points <已知点tasks.json> --output <文章分析tasks.json> --candidates-only
python scripts/produce_context.py run --input <文章分析tasks.json> --library <材料库目录> --run-root <新分析目录>
```

`--points` 必须明确指定已知点文件，或包含同学科点文件的目录；不从用户环境猜目标。桥接保留原句、段落、疑问及所有未选词义，按教学来源内容的稳定哈希去重；账号身份、浏览器记录 ID 和时间戳不进入模型请求。原导出另存快照。过期来源只列入 `deferred_records`，须核对原版本后再处理，不自动套用当前版本。全部过期时不会生成可运行任务。

`--candidates-only` 仅分析 `memory_status: "candidate"` 且 `point_ids: []` 的未知候选；已有点或未申请学习的摘录保留在延后清单，不会静默丢弃。省略此项可对导出的全部有效语境做已有点匹配。

原有 `from-reading` 路径继续只输出文章分析任务。要自动完成新点的选义、指导和初学来源，使用下述显性工作流；仍可保留人工编写来源，再作为 `provided_cards` 独立审核。未经审核的建议不会直接成为正式点，未知词也不会被伪装成已经进入 FSRS。

## 原文自动生成完整显性 Topic

`scripts/produce_explicit_context.py` 复用同一文章和题型合同，执行后端固定为 Codex Exec。原文输入为 `schema: 1, articles: [{id,title,paragraphs:[{id,text}],topic_id?,requirements?}]`，也接受上述本人阅读导出。已知点目录必须显式传入；词典原始义项和未选义状态保留在来源快照中。

```powershell
python scripts/produce_explicit_context.py prepare-input --input <原文或阅读导出.json> --points <已知点目录.json> --output <显性任务.json>
python scripts/produce_explicit_context.py run --input <显性任务.json> --library <材料库目录> --run-root <新运行目录>
python scripts/produce_explicit_context.py prepare --input <显性任务.json> --runs <已通过运行目录> --library <材料库目录> --output <新准备目录> --course-id <稳定课程ID> --course-title <课程名> --unit-id <稳定单元ID> --unit-title <单元名> --compile --test-material
```

每篇先分析并独立核对原文证据，然后自动决定新增、复用或延后。新增目标由程序分配稳定 ID；模型生成完整 Introduction、组块、知识点和初学卡，先独立盲审初学题面，再由另一会话逐项复核选义、与已知点的重复、讲解和每张卡。清楚的语境直接完成决策；真正证据不足的候选保留原因，不凑数。一个批次按原文顺序运行，已通过的新点加入后续文章的已知目录，避免同一知识在不同文章中重复创建。

文章分析使用独立的阶段审核提示，只验收 `matches` 和 `suggestions` 的原文引用、上下文含义、已有点匹配与新增价值；整体需求中的指导、组块和初学卡在后续作者阶段验收。修订分析审核规则后使用新运行目录，已通过的分析和符合当前作者规则的显性来源仍可直接复用。若需重审旧运行中已完成的分析生成，可在 `run` 增加 `--reuse-analysis-runs <旧运行目录...>`：只复用与完整有效来源、已知点目录、生成提示及真实 Exec 哈希一致的候选，重新独立审核；旧拒绝意见不会充当通过记录，未完成调用不会复用。新审核若发现实质错误，再从原始输入生成下一次候选。原始证据留在旧目录，新阶段记录 `reused-generation.json`。

初学作者策略 `initial-transfer-v1` 将原文证据与作答情境分开：指导可完整讲解原句；应用型初学卡使用另一自然情境，不能把刚解出的同一句再次挖空，也不能只替换人名。概念卡可以检查一般规则与边界。该策略单独参与作者缓存和准备门禁，不使输入未变的原文分析失效；旧作者记录保留可读性，但不能直接按新策略导入。

只有生成、审核、来源和内容哈希一致的记录能够编译。`prepare` 输出 schema 2 结构化来源、可供反向生成使用的已知点目录和完整决策记录。导入仍使用原有编译器与安装器，保持 Topic、题型、学习记录及测试材料权限的契约。

## Codex Exec 调用证据

`graspmemoedu/codex_exec_client.py` 使用本机已有 CLI 登录。每次生成或审核运行新的临时会话，启用只读沙箱、关闭自动项目上下文和工具，使用官方 JSONL 事件及输出 schema；不复制凭据到产物。任务输入、原始事件、退出码、用量、最终输出及哈希分别留在调用目录。事件流未完整结束、出现工具调用、非零退出或 JSON 不合法时拒绝输出。

两条工作流均复用已经通过的阶段和材料；正常复跑无需模型请求。失败修订针对通用提示、合同或校验器，从原始输入重新运行相应阶段，不能手改候选冒充自动生产。官方能力参见 [Codex 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)。

## 修订既有原子教学内容

纠正已安装的指导、组块或题面时，可给 `prepare_structured_atomic.py` 增加 `--preserve-atomic-progress <previous-versions.json>`。该文件明确列出 `{Topic_ID: 当前版本}`，安装时逐项核对。此路径要求 Topic、节、组块、点、卡的身份、顺序、关联、初学/复习阶段、交互控件和排程策略不变，不接受同时重置进度。

`AtomicContentRevision` 沿用现有导入锁、不可变内容批次、备份与激活指针。在事务中只迁移选中 Topic 的版本与页面修订号；掌握状态、FSRS、次数、已提交答案和原事件不改。已完成作答保留旧题快照，当前未回答卡刷新到修正后的题面，过期页面不能继续提交。活动判题、复习试卷或被占用的点会阻止更新；结束后可用同一内容包重试，失败批次保留。发布需同时声明 `atomic_content_revision: true`，发布程序独立核对允许的行变化，失败恢复旧指针、源码和数据库。

教学正文只包含教学内容。必要的引用署名可存于节的 `source_attributions`，使用 `{text,url?,license?,license_url?,changes?}`，在正文外的折叠“来源”中展示；生成、审核、缓存和机器校验信息保留在私密生产记录中。

## 音频与阅读材料

生成阶段的 `audio_script` 仅是私密文本，准备后保存在 `audio_source: {mode,transcript}`。这不是音频完成状态，带有 `audio_source` 的内容不能编译为可安装包。

```powershell
speech-kit --json doctor
python scripts/context_audio.py --input <准备目录/structured-topics.json> --output <媒体目录/structured-topics.json> --dry-run
python scripts/context_audio.py --input <准备目录/structured-topics.json> --output <媒体目录/structured-topics.json>
python scripts/prepare_structured_atomic.py --input <媒体目录/structured-topics.json> --output <新包目录> --subject english --course-id <稳定课程ID> --course-title <课程名> --unit-id <稳定单元ID> --unit-title <单元名> --topic-prefix "" --test-material
```

音频工具使用维护中的 `speech-kit` 默认 MiMo Mia，逐篇保留输入、服务回执、MP3 和合成记录；重复执行复用已完成文件。FFprobe 确认音频可解码、时长有效，再按音频字节 SHA256 命名。正式 `audio` 字段引用 `audio/<sha256>.mp3`，稿件保留在服务端。机械打包核对路径、字节哈希、MIME 和大小，将文件复制到包内 `topics/audio/` 并纳入同一 manifest。运行时读取还会重新验证。

测试课程使用 `--test-material` 在包中声明整门课程的测试限制。既有安装器在切换内容指针之前合并这些标记；标记不改变正文版本或学习记录。部署仍须按测试材料资格完成权限验收。

自由阅读使用 Topic 的 `reading_materials`，以纯文本段落、Unicode 位置、释义和已有 `point_ids` 绑定。词源和词义关系分别记录：词源须有明确来源；WordNet 的义项关系不能当作词根或无条件同义词。课程的实词源文、文章、音频和生产回执留在私密产物目录，不提交 Git。

可播放、可转写和内容完整是不同验收项。文件解码与哈希通过只证明媒体正确打包；实际转写、语义反馈及账号权限由隔离运行时验收。转写内容检查不冒充发音评分。
