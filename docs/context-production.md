# 本地语境材料生产

本地工作流有两个方向：从文章查找已有记忆点并提出新点建议，从选定记忆点生成可用于复习的语境题。生成、独立审核、材料复用、音频制作和机械导入分开留存；不连接线上常驻生产，也不写学习者进度。

`graspmemoedu/context_production.py` 使用学科注册表、既有题型和题内映射合同，不按学科名称分派业务。`scripts/produce_context.py` 复用 `atomic_question_trial` 的凭据发现及已有 `CompatibleClient` API 传输。凭据只从 Codex 生产专用配置读取，不写入请求记录。新生成卡仍是普通 Flashcard，统一标为 `learning_phases: ["review"]`；每个点的初学卡由原来源提供。

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

## 计划、生产与复用

```powershell
python scripts/produce_context.py plan --input <tasks.json> --library <材料库目录>
python scripts/produce_context.py run --input <tasks.json> --library <材料库目录> --run-root <新运行目录> --workers 4 --attempts 3
```

`plan` 只验证来源并查询本地材料库，不调用模型。材料以稳定目标、完整来源、题型和要求的哈希检索；只复用完整校验且独立审核通过的记录。任务显示名称改变不妨碍复用，目标或要求改变则不能误用旧材料。每条记录包含内容哈希、审核结果与生产证据；损坏记录会报错而不是静默跳过。

生成请求和审核请求是独立调用，后者重新按题面作答，不携带生成对话历史。请求、原始响应、候选、逐项审核和失败原因保存在运行目录。明确的内容缺陷自动反馈给下一次生成；已经通过的材料保留。程序内部错误及结果未知的请求不会盲目重发。

修订审核流程而生成条件未变时，可使用 `--candidate-runs <旧运行目录>`。程序仅复用输入和生成提示词哈希均一致、结构重新通过的原始生成响应，然后使用新审核流程；不会手改候选。所有原始失败证据继续保留。`--only <任务ID...>` 可在同一冻结输入下补齐选定任务。

## 准备来源包

```powershell
python scripts/produce_context.py prepare --source <structured-topics.json> --tasks <tasks.json> --runs <已通过运行目录> --library <材料库目录> --output <新准备目录> --course-id <稳定课程ID> --course-title <课程名> --unit-id <稳定单元ID> --unit-title <单元名>
```

命令要求所有任务通过，核对每个来源卡与审核快照，再将生成卡机械追加到指定 Topic。输出包含完整结构化来源、逐题型覆盖表及文章分析。加入 `--compile` 时调用现有 `prepare_structured_atomic` 和 `import_atomic_topic.mjs`，产生原有安装器可用的内容包、答案侧车、机械审核记录和文件哈希清单。所有原点和原卡身份保持不变。输出已存在且内容不同会拒绝，不覆盖旧证据。

## 从阅读记录回到本地生产

阅读页导出的本人候选记录使用 `schema: 1, kind: "reading-context-export"`。`records` 每项保存 `topic_id`、`material_id`、`content_version`、`paragraph_id`、原 `quote`、原段落 `context`、`note`、全部 `definitions`、`point_ids`、`memory_status` 和 `stale`。

`quote` 兼容原文字符串和浏览器的 `{exact,start,end,prefix,suffix}` 锚点；后者按 Unicode 字符位置核对原段落，完整保留，不改写为截断的词条。已有的 `dictionary`（义项 ID、词典版本、来源及许可）、`selected_sense_id`、`contextual_sense_selected` 也原样留存，未选义状态不因导出而改变。

```powershell
python scripts/produce_context.py from-reading --input <本人阅读导出.json> --points <已知点tasks.json> --output <文章分析tasks.json> --candidates-only
python scripts/produce_context.py run --input <文章分析tasks.json> --library <材料库目录> --run-root <新分析目录>
```

`--points` 必须明确指定已知点文件，或包含同学科点文件的目录；不从用户环境猜目标。桥接保留原句、段落、疑问及所有未选词义，按教学来源内容的稳定哈希去重；账号身份、浏览器记录 ID 和时间戳不进入模型请求。原导出另存快照。过期来源只列入 `deferred_records`，须核对原版本后再处理，不自动套用当前版本。全部过期时不会生成可运行任务。

`--candidates-only` 仅分析 `memory_status: "candidate"` 且 `point_ids: []` 的未知候选；已有点或未申请学习的摘录保留在延后清单，不会静默丢弃。省略此项可对导出的全部有效语境做已有点匹配。

分析先核对所选词在上下文中的义项。已有点可继续复用；新增建议仍需要作者明确选义并形成指导与初学种子来源，再作为 `provided_cards` 经独立审核，最后走同一 `prepare` 编译和导入。作者决定、自动生成、独立审核分别留存。未经审核的建议不会直接成为正式点，未知词也不会被伪装成已经进入 FSRS。

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
