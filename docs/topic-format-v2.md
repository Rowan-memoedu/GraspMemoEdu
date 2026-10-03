# Topic 格式 v2：共用作者与运行接口

本文件是 MathTopicPipeline、RemNote 转换流程与 GraspMemoEdu 的共用格式定义。适用于显式声明 `topic_format_version: 2` 的范式 Topic；旧 Markdown、原子训练 JSON、独立题库 JSON 继续沿用原协议。导入只解析、按字段映射、渲染与验证，不生成、改写或逐题审核教学内容。

## 身份、顺序与引论

- `id` 是稳定 Topic ID，显示编号（如 2.1）、标题、文件名变化不重编 ID。根 `source_rem_id` 保存原 RemNote Document 身份，可省略，不能按同名标题匹配。
- `topic_kind` 为 `lesson` 或 `introduction`，不替换既有 `training_mode`。
- 至少一个非空 Introduction。Introduction 可有多个，可与 Example 穿插；严格保留源顺序，不合并、不前移。
- 普通 Topic 保留连续 Example、Explanation、Interaction 和 Practice 规则。每个 Example 至少七道 Practice；5×3 编号仍展平显示，`source_practice` 保留原分组。
- 引论只能含 Introduction，`modules=[]`。母节点下、子 Topic 前的 intro 导出为该母节点的独立引论；编号由上游安排，不由导入器重排其他 Topic。
- 引论保留在课程目录中，但不属于课程知识图谱，不接受或产生前置边。导入遇到旧图谱冲突时报错，不修改图谱、账号或学习记录。
- 网站负责首次学习、完成记录及重读推送：完成后保留进度；已学过且教学内容版本变化时推送重读；未学过时更新首次内容；不产生定时复习或 FSRS 排期。

## 作者 JSON

JSON 是作者源文件，不是已编译的 `topic.json`。结构定义见 [JSON Schema](topic-format-v2.schema.json)，实际校验与 Markdown 归一均由 `graspmemoedu/topic_format.py` 实现。

```json
{
  "topic_format_version": 2,
  "id": "sample-introduction",
  "title": "引论",
  "topic_kind": "introduction",
  "source_rem_id": "DocumentRem",
  "nodes": [{
    "id": "intro-a",
    "text": "Introduction",
    "source_rem_id": "IntroductionRem",
    "children": [{
      "id": "definition-a",
      "source_rem_id": "DefinitionRem",
      "text": "**定义**：保留作者原有定义。",
      "children": []
    }]
  }]
}
```

`nodes` 与每个 `children` 都是有序数组。节点有稳定 `id`、原正文 `text`、可选 `source_rem_id`、真实 `source_tags`、`callout`、`source`，不接受未知字段后静默丢弃。正文换行使用 LF；仅统一标题外侧与节点末尾的空白分隔，保留内部换行、文本、父子关系与顺序，原稿字节仍完整保存。节点 ID 在一个 Topic 内唯一；全局定位用 `(topic_id, node_id)`。重复来源 Rem ID 或无法确定的映射报冲突。

顶层节点文字为 `Introduction`（可加 `: 标题`）或 `Example N`（可加标题）。Example 节点 ID 必须是 `example-N`，其题目 ID 仍为 `example-N-question`，Practice 仍为 `example-N-practice-M`。Introduction、正文、Explanation、Practice 及 Interaction 节点的来源身份由显式节点元数据保存，不能用当前行号推测身份。

## 作者 Markdown 与 JSON 的对应

沿用两空格大纲；文件头保存 Topic 元数据，节点末注释保存节点身份和来源。只识别下面两种专用注释，普通 HTML 不因此放行。正文元数据不呈现给学生。

```markdown
<!-- topic-format: {"topic_format_version":2,"id":"sample-introduction","topic_kind":"introduction","source_rem_id":"DocumentRem"} -->
- 引论
  - Introduction <!-- topic-node: {"id":"intro-a","source_rem_id":"IntroductionRem"} -->
    - **定义**：保留作者原有定义。 <!-- topic-node: {"id":"definition-a","source_rem_id":"DefinitionRem"} -->
```

`parse_source`、`dump_json`、`dump_markdown` 提供共同解析与标准序列化；调用方不用手工拼接注释。两种源格式归一后的内容、节点身份、章节顺序、引用边与 `content_version` 一致。原文件字节哈希不同是正常现象，分别保存在来源包，不用它代替教学内容版本。

## Callout、普通字段与题源

- 类型固定为 `hint`、`aside`、`requirement`、`source`，显示标签为 hint、题外话、要求、题源。
- 来源是 RemNote 时，以 `source_tags: [{"tag_rem_id":"ExactTagId","name":"题源"}]` 中真实标签识别，不凭正文出现同名词语判断。冲突标签、类型和标签不一致均报错，不套用 Blog 的首标签优先规则丢掉其他含义。
- JSON 节点的 `callout` 声明类型；Markdown 对应 `> [!type]`，后续本节点正文用 `> ` 续行，子节点维持原层级。四类框体由网站提供的 Blog 适配模块封装。
- 定义、定理、性质、识别特征、范式、巧法、idea 仍在所属 Introduction／题面／Explanation／Practice 正文中，格式为 `**字段名称**：内容`。不把它们提升为并列章节。
- Explanation 中节点自身只含一个内部引用时，机械标为 `**idea**：引用`，保留目标、子节点和顺序；其他正文中的引用不自动变成 idea。
- 题源节点用 `source` 对象提供已有信息：`name`、`year`、`edition`、`volume`、`page`、`item`、`location`、`adaptation`，均为可选文字。按此顺序用 `｜` 拼接非空项；缺项省略，不补作者、书名、年份、页码，也不输出“未知”“缺乏信息”等占位。
- `difficulty` 仅留来源元数据；`url` 仅作为原始外部参考，不显示、不生成网站链接，也不进入教学内容哈希。原材料与来源包保留。
- 题源可省略 `text`，由上述显式字段机械排版；若同时提供文字，必须等于字段排版结果，冲突时停止。只有 URL／难度且无可显示信息的空题源不生成 Callout；若还带其他正文或子节点，报告冲突。

旧来源清洗仍适用于题面前的杂项编号、难度星号；新规范允许在明确题源 Callout 中展示出处和定位。因此格式检查按节点用途判断，不再把题源中的题号一概报作题面残留。题内小问编号、必要引用、条件和其他正文不能随清洗删除。

## Mermaid

范式正文保留显式 `mermaid` fenced code block。整数 step 表示无分支顺序；同整数部分的小数 step 表示该阶段分支。上游按实际关系提供图与条件，导入器不凭编号猜测分支条件、合流或新步骤。

v2 只把已识别 Mermaid 源码交给网站的受控准备器，允许其中正常的 `-->` 和数学标签；普通代码块里的错误数学包裹继续检查。渲染诊断不能当作数学正确性证明；任意 HTML、脚本、主动链接和外部图片仍不放开。

## 内部引用与外部参考

- 内部引用写作 `[[id:<精确 Rem ID>|显示文字]]`。JSON 的 `text` 和 Markdown 使用同一语法；目标与显示文字来自上游，禁止只按标题猜测。
- 目标未导入时保留作者引用边与显示文字，初始不可跳转；目标导入后重建派生解析与反链。目标是 Topic 根时允许无目标节点 ID，跳到 Topic 入口。
- 反链从作者正文引用派生，目录导航、物理父级、自引用不构成反链或课程前置关系。同一目标可合并展示，仍保留各来源节点便于定位。
- 纯外部链接叶节点和明确“待转化参考资料”子树不进入 Topic；来源包与排除记录保留。外部链接与正文、题源或其他子节点混排时停止并报节点 ID，不删除整段正文，也不自行把链接改写成普通文字。
- 本地 PNG 属附件通道，不是外部参考。外部图片必须先通过既有附件流程提供本地副本，导入器不自行联网获取。路径、格式、字节与哈希校验沿用原规则。

## 网站运行字段

编译产物继续保留 `id/title/version/source_sha256/modules/math_css`，普通题目和 `interaction` 形状不变。v2 增加：

- `topic_format_version: 2`、`topic_kind`、可选根 `source_rem_id`。
- `introductions: [{id,title,html}]`，每节独立身份。`content_order: [{kind:"introduction"|"example",id}]` 必须恰好覆盖每个指导节及模块一次，Example 的 id 指 `modules[].id`，不是题目 ID。
- `content_nodes: [{id,section_id,parent_id,source_rem_id?,owner,...}]`。`owner` 为 `{kind:"introduction"|"example"|"practice",id,field:"html"|"explanation_html"}`；Introduction 的 id 指指导节且 field 只能是 html，其他 id 指题目。每个实际正文锚点为 `node-<id>`。网站据 owner 过滤尚未显示的解析和无权访问的内容。
- `references: [{source_node_id,target_rem_id,label}]` 只保存作者事实。网站根据当前已安装目录生成权限过滤的目标 Topic／节点和解析状态，不维护第二份可变反链真相源。
- `content_version` 是规范化教学内容哈希：包含标题、正文、顺序、节点身份、Callout、引用目标和显示文字、附件字节；排除来源行号、来源标签身份、难度/外链元数据、源文件路径、导入时间、HTML/CSS、渲染器版本和引用解析状态。相同图片字节与说明只改相对文件名不改变此哈希。
- `version` 仍标识完整导入产物。引论接续比较 `content_version`；普通 Topic 的原有版本保护不放宽。v2 的 `introduction` 兼容摘要不参与学习顺序，网站使用 `introductions/content_order`。

## 导入、更新与验证

原稿、答案侧车、转换凭据、附件及产物继续绑定哈希，安装前独立重转换比较。引论允许同 ID 更新，保留旧来源包和目录快照，以原子目录引用切换生效；不写学习数据库。普通既有 Topic 仍拒绝覆盖。相同包重复安装不产生新版本或新任务。

每份包声明一个精确 Topic 的完整作者出边。该 Topic 的正文更新后替换其出边；其他 Topic 不变，目标后续补齐只重建派生索引。失败不改变活动目录；回滚通过保留的旧包与目录记录恢复，不删除历史材料。

验收必须覆盖：Markdown/JSON 等价、穿插与纯引论、四类 Callout、题源缺项、idea、分支 Mermaid、未解析到已解析引用、混合外链冲突、旧题型/5×3/附件兼容、篡改拒绝、安装幂等和引论更新保留数据库。转换通过不等于网站已部署或真实课程已验收。
