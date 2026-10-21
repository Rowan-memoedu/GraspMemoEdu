# 独立课程图谱：格式与导入工作流

## Topic 与题组共用依赖图

协议 3 在同一图谱中支持两种节点：Topic 沿用 `topic_id`；题组使用 `bank_id` 和 `group_id`，边端点写为 `group:<bank_id>:<group_id>`。两者都需要 `title` 和布尔 `member`。题组使用题库原始稳定 ID，不复制成 Topic，也没有另一套依赖存储。

例如，CS Topic 学完后才开放对应题组：

```json
{
  "schema_version": 3,
  "subject_id": "cs",
  "course_id": "java-practice",
  "title": "Java 练习依赖",
  "nodes": [
    {"topic_id": "numeric-types", "title": "数值类型", "member": false},
    {"bank_id": "java-practice", "group_id": "literals", "title": "数值字面量", "member": true}
  ],
  "edges": [
    {"from": "numeric-types", "to": "group:java-practice:literals", "type": "prerequisite"}
  ]
}
```

`course_id` 保留原图谱文件标识的含义；题库图谱使用题库 ID。通过下面同一个 `graph-import` 入口预览、导入、更新和备份。Topic → 题组、题组 → 题组及题组 → Topic 都参与全局前置遍历与环路校验。目录章节和纯指导不是知识节点，不能代替实际题组。未导入、无权访问的前置仍然阻止解锁。

前置题组的直接题目必须全部至少完成一次，并且没有进行中的复习或到期未复习的卡片，才算满足依赖。首次直接答对且未主动加入错题本的题目沿用停止推送规则，不会因为没有 FSRS 复习而阻止解锁。Topic 继续使用原完成、复习和暂停策略；熟练度分数本身不作为解锁门槛。网页作答、IDE 回传和复习取题使用同一个 `DependencyPolicy`。

题库页面使用同一图谱查看器，节点链接分别打开原 Topic 或题组。学习和题库右上角的“隐藏暂未解锁的内容”只控制前置受阻任务的显示，保留每个身份的选项；空章节随之隐藏，取消勾选可恢复。隐藏不修改图谱、进度或复习状态。题组全部题目完成后显示“完成学习”，返回题库时展开并定位该题组。

来源批次支持协议 2：节点增加布尔 `member`，区分本课程成员与外部前置。一个 Topic 可属于多门课程，共用正文与学习身份。运行时合并所有来源的显式前置并检查全局环；外部节点不自动成为课程成员。协议 1 保持兼容。批次激活后，当前图谱位于 `active-content.json` 指向的内容目录内，详见[批次导入](mathacademy-import.md)。

正式图谱自 2026-09-21 起位于腾讯云 `/var/lib/graspmemoedu/graphs`。本文本机命令用于隔离准备和预览；正式导入通过 SSH 在服务器执行，见 [腾讯云运维](tencent-deployment.md)。不要将本机回滚快照当作正式数据源。

Topic Markdown/审核包负责教学内容；`course-graph.json` 负责图谱。两个入口独立、顺序不限，通过稳定的 `topic_id` 关联。图谱变更不改变 Topic 版本、正文、题库、账号权限或学习进度。

Topic v2 的纯引论只进入课程目录和层级，不能成为图谱节点或前置关系两端。正文内部引用和反向链接均不产生前置关系。预先导入的图谱若已占用纯引论 ID，导入器会拒绝冲突，保留现有目录和图谱。

## 版本 1 格式

```json
{
  "schema_version": 1,
  "subject_id": "physics",
  "course_id": "mechanics",
  "title": "力学",
  "nodes": [
    {"topic_id": "position", "title": "位置", "source": "topics/位置.md"},
    {"topic_id": "velocity", "title": "速度", "source": "topics/速度.md"}
  ],
  "edges": [
    {"from": "position", "to": "velocity", "type": "prerequisite", "reason": "由位置变化定义速度"}
  ]
}
```

- JSON Schema：[`course-graph.schema.json`](course-graph.schema.json)。运行时还检查 DAG、引用、重复、课程归属和文件名可移植性。
- `nodes` 只包含教学 Topic；版本 1 不接受外部前置知识节点。尚未导入教学内容的 Topic 仍是教学节点，名称下显示灰色小字“教学内容未导入”，不生成学习任务。
- `topic_id` 是全局稳定标识，发布后不因标题、文件名、阅读顺序改变而重编。导入 Topic 时沿用同一个 ID。图谱名称不覆盖教学正文标题。
- `course_id` 使用小写 ASCII 稳定标识，不使用 Windows 保留文件名。`subject_id` 必须是网站中存在的学科。图谱可先于教学课程存在，但已绑定的 Topic 不得放入其他课程。
- 每条边 `from → to` 表示后者以前者为前置；多个前置是 AND 关系。禁止自环、重复边、循环及未声明端点。阅读顺序不自动构成依赖关系。
- 节点可带 `source`；边可带 `reason`、`source`。来源用相对引用（可含 `#` 定位），禁止绝对路径。来源和说明保留在作者文件，网页接口不返回本机来源引用。
- 图谱是课程的完整快照：同一课程再次导入时整体替换，不合并累积。被删掉的节点或关系会从图中消失，但不会删除任何 Topic 内容。
- 上限为 1000 节点、10000 条边、4 MiB。跨课程边、OR 前置和外部前置节点不在本版本内。

## 从内容生成图谱

1. 列出交付范围内的 Topic，分配或复用稳定 ID，记录名称和原稿相对路径。
2. 从已审核的前置表、Introduction 和 Example 解题步骤识别依赖。区分课程内 Topic 与默认已知知识；本版本只记录前者。
3. 核查压轴 Topic 的每一步是否有上游路径。保留原表中明确声明的依赖；补充边时在 `reason` 和 `source` 记录依据，不按编号机械连成链。
4. 检查独立分支、冗余关系和循环。明确声明的传递依赖可以保留，不静默重写原审阅结论。
5. 生成完整 JSON，运行独立导入器预览差异与图形，核对后安装。以后更改图谱重复同一路径，不重新导入教学正文。

## 标准入口

```powershell
# 只校验、列出差异，并生成离线可视化。
pwsh -NoProfile -File D:/Projects/GraspMemoEdu/Import-Graph.ps1 `
  -Source D:/Artifacts/MyCourse/course-graph.json `
  -PreviewHtml D:/Artifacts/MyCourse/course-graph.html

# 安装整份图谱，也可同时指定 PreviewHtml。
pwsh -NoProfile -File D:/Projects/GraspMemoEdu/Import-Graph.ps1 `
  -Source D:/Artifacts/MyCourse/course-graph.json -Apply
```

CLI 为 `python -m graspmemoedu --data-dir <目录> graph-import --source <文件> [--apply] [--preview-html <HTML>]`。默认只预览，不写正式图谱或数据库。自动化可选用 `--expected-revision <上次 previous_revision>` 防止旧预览覆盖并发更新，首次为 `none`；这不是批准口令。

Topic 仍使用 `Import-Topic.ps1` 或 `author-prepare` / `author-install`，课程需按原流程配置。图谱导入不会创建空教学课程或伪造 Topic。后续正文导入并重载内容目录后，图谱按 ID 自动显示可用；图谱更新本身在下次打开时生效，无需重启服务。

## 存储、更新与恢复

- 当前图谱：数据目录 `graphs/<course_id>.json`，由导入器维护。不要手改安装文件绕过校验。
- 更新前版本：`graphs/history/<course_id>/<revision>.json`。重新导入历史文件即可恢复；回滚也保存被替换版本。
- 导入复用作者导入锁；先校验，再持久保存旧版，最后原子替换当前文件。相同内容重复导入返回 `unchanged`，不改时间戳。
- 整站备份和恢复包含当前图谱及历史，校验文件身份、哈希与内容绑定。
- 独立图谱存在时，它是该课程关系的唯一维护入口。旧 `catalog.json.prerequisites` 只用于没有独立图谱的课程兼容，独立导入不回写它。

## 展示和权限

`GET /api/graphs?subject_id=…` 列出可见独立图谱；`GET /api/graphs/<course_id>?subject_id=…` 读取图形。已导入课程保留原图谱入口，只有图谱的课程出现在所属学科的图谱列表。离线 HTML 使用同一渲染组件，不依赖网络。

支持搜索、缩放、选节点高亮上游路径、查看直接前置/后续。已导入节点仅显示居中的 Topic 名称，不显示状态副标题；未导入节点使用虚线边框，保留名称及下方灰色小字“教学内容未导入”，不计入学习进度。图谱展示不创建学习记录。

学习推送和 Lesson / Review 操作共同使用图谱依赖；没有独立图谱时使用课程目录内的前置关系。所有上游前置 Topic 必须完成学习、没有到期或进行中的复习、没有暂停，才允许学习后续 Topic。未导入或无权限访问的前置节点不会被跳过。图谱变更即时参与判断，已有学习记录保留，受阻时仍可只读回看已有内容。

任务卡的前置知识显示已有 Topic 完成百分比，以及各模块最新有效熟练度的平均值。评分取当前内容版本，包含 Lesson 和后续复习；尚未评分的模块不计入平均值，全部未评分时显示「—」。熟练度不参与解锁。

游客仍只访问允许的数学 Topic。限定 Topic 的账号只看到已授权节点及两端都可见的边。默认继承当前/未来 Topic 权限的账号可以看到同一学科的未导入节点；不修改账号权限。来源、原因等作者审校字段不进入公开响应。

## 验收

- 两种导入顺序均可用；Topic 导入不覆盖图谱，图谱更新不改正文和进度。
- 增加、改名、删除关系、幂等重导、并发冲突、失败重试和回滚均有隔离测试。
- 拒绝未知端点、自环、循环、重复、错误课程归属、外部节点和不安全来源。
- 网站接口实时更新，权限不泄露隐藏节点，备份恢复包含历史。
- 从真实源文件导入并回读，再运行可视化验收；单测通过不等于生产导入成功。
