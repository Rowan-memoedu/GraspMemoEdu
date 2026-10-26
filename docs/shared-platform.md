# 共用平台与学科开发边界

所有学科共用一个应用和同一套业务机制。学科只配置内容归属、名称、说明、配色、徽标、指南和卷型内容；不能选择另一套学习、复习、权限、评分或记录实现。

## 架构边界

学科配置进入共用目录和路由，内容类型选择共用业务服务，作答能力连接相应的输入与判题适配器。改变 `subject_id` 不得改变同种内容的流程、评分、FSRS、权限或状态保存方式。

- 页面入口：课程、题库、复习、答疑、指南、设置与学习报告由同一个前端和对应 API 提供。学习收在课程入口内，没有独立学习页或学科专用页面实现。
- 前置依赖：Topic 与题组共用 `GraphStore` 和 `DependencyPolicy`，支持跨课程、跨题库的混合图谱；权限、完成状态、到期复习和暂停统一参与解锁。列表隐藏只消费同一份 `dependency_ready`，不另算前置条件。格式见 `docs/course-graph.md` 协议 3。
- 内容模型：课程 Topic、独立题库、纯阅读内容各有明确归属。范式、原子与混合 Topic 的差异由内容结构决定，对每个学科都适用。
- 作答适配：网页作答、IDEA Check、Hyperskill 回传是输入与判题能力，不定义一套学科架构。IDEA 和 Hyperskill 绑定不以 CS 作为能力开关，记忆点记录实际内容所属学科。
- 单卡复习：所有来源都显示在同一“原子卡片”目录，并共用单卡阅读、进度和返回入口；来源层级仅用于定位。外部判题不能把单卡复习退回整组练习页。
- 共用业务：身份、权限、测试材料隔离、作答幂等、暂停、记录、FSRS、复习选题、报告与备份保持统一。卷型、题目和判题依据可来自不同内容，执行规则共用。
- 内容生产：数学变式生成、Academy/Hyperskill 导入属于来源适配。其目标学科和已授权生产范围不等于运行时业务特例，也不自动在其他学科启用付费生成。

`subject_registry.py` 是后端唯一学科注册信息源，供学科目录、原子内容、题库、卷型和测试指南校验使用。前端主题表仅负责视觉，不能控制业务资格或流程。

独立题库只接受 `TrainingCatalog` 模型。历史“课程设置 placement=question_bank，再把 Topic 投影到题库”的运行入口已移除；启动时验证容器归属、学科、完整旧链接映射和唯一状态所有者。不完整转换会明确失败，不能在同一个题库入口静默回退到 Topic 页面。原始 Topic 可作为迁移证据保留，但不再拥有第二份活动学习状态。

旧数学无学科 URL、旧选课记录和旧备份保持兼容，仅在兼容边界解析其历史归属。新学科路径的指南图片统一验证身份；旧 `/guide-assets` 保留原公共资源兼容行为。

## 架构验收

`tests/test_shared_architecture.py` 用同一套合成内容逐一替换为每个已注册学科，验证页面接口、学习服务类型、六类身份的测试材料可见性、评分、FSRS 日期、未见变式优先、来源适配、卷型与测试指南支持。它还检查通用业务代码不能按学科名称分派，不允许再维护独立学科名单。

`tests/test_atomic_banks.py` 保留原错误入口的失败回归：只改归属不能激活题库，完整迁移后才可使用共用题库，原始证据和权限保留。新增学科必须自动进入上述对照，不靠只测数学和新增学科两个样本。

Topic 和题组的未完成树、完成图标、按日期排列的完成记录及展开／收起控件共用渲染器；原生题库仅负责将自己的首次作答记录投影为任务。完成后的复习独立于首次学习列表，不把题组重新加入层级。共同树渲染器只保留尚有任务的祖先容器。

课程和题库共用 `web/catalog-workspace.js` 的目录、任务页布局、作用域链接及按学科隔离的页面位置记录。`catalog-outline.js` 负责目录树，`task-tree.js` 负责任务树；`portal.js` 与 `training.js` 分别适配课程和原生题库的数据，共用概览栏、任务控件和完成记录。两者的概览栏都在百分比右侧提供“重新选择主题”，明确返回各自目录。该操作不清空选课偏好、草稿或学习状态。

课程任务页的地址包含课程 ID，可选 `root` 指定模块；进入 Topic 或范式复习时携带 `course` 和 `root`，返回位置不依赖之后的选课偏好。`learn` 仅在 `portal.js` 的兼容分支解析为新课程地址，不再渲染另一份页面。顶部课程、题库入口恢复当前访问中各自的最后位置；身份或权限变化清空位置记录，单卡复习不覆盖题库位置。

`scripts/course_workspace_acceptance.mjs` 使用 `scripts/bank_navigation_fixture.py` 的隔离数据逐学科检查选课、模块返回、旧链接、刷新、前进后退、选择失败重试及中英文窄屏布局；`scripts/bank_navigation_acceptance.mjs` 保留题库草稿、滚动位置和单卡复习回归。正式学习记录不用于验收。

`scripts/shared_architecture_fixture.py` 与 `scripts/shared_architecture_acceptance.mjs` 使用隔离合成数据检查每学科的 15 个页面，连续跨学科切换并对照可见 DOM 结构。检查等待实际内容加载完成，忽略编辑器运行时生成的样式编号；个人报告因累积记录不同只检查可正常呈现。阅读容器在内容切换后清理旧的反向链接样式状态，页面不能依赖此前访问过哪个学科。

测试材料通过独立标记保留，仅高阶学员测试账号（同时具备高阶和学员资格）及管理员测试账号可见，正式账号均不可见。课程、题库、复习、管理报告、答疑历史和指南共用同一策略；配置与恢复边界见 [测试材料机制](test-materials.md)。

| 层 | 入口 | 职责 |
| --- | --- | --- |
| 学科外观与路由 | `web/subjects.js`、`graspmemoedu/subjects.py`、`subject_registry.py` | 全部注册学科的主题、路由、课程归属；旧数学路径继续兼容 |
| 课程与题库工作区 | `web/catalog-workspace.js`、`web/catalog-outline.js`、`web/task-tree.js` | 共用目录、模块内任务布局、主题重选与页面位置；内容服务分别适配 |
| 课程与内容 | `catalog.py`、`author_import.py`、`scripts/import_course.mjs` | 作者 Markdown 机械导入、离线公式、来源校验、稳定 Topic ID |
| Lesson | `service.py`、`module_flow.py`、`web/app.js` | 掌握选择、指导、练习、模块跳过、自动暂停与冷却、幂等提交、历史与草稿 |
| 复习 | `review.py`、`review_api.py`、`web/review.js` | FSRS、到期任务、持久练习队列；沿用原评分政策 |
| 原子训练 | `atomic.py`、`atomic_content.py`、`atomic_api.py`、`web/atomic.js` | 记忆组块与点级状态、交错、可变设问、打包复习 |
| 独立题库 | `training_content.py`、`training.py`、`training_api.py`、`web/training.js` | 独立内容树、自由选题、单题卡片、自评与统一复习入口，见 [题库架构](training-banks.md) |
| 统一复习 | `review_cards.py`、`review_sources.py`、`review_papers.py`、`review_paper_api.py`、`web/review-papers.js` | 原子点与题库共用选卡、MD 卷型、重叠候选和双提交模式；评分仍由来源服务负责，见 [统一复习](unified-review.md) |
| 题型 | `question_types.py`、`judging.py`、`web/question-input.js` | 统一 schema、公共字段投影、作答控件、确定性/语义判题 |
| 导航与反馈 | `web/portal.js`、`web/catalog-picker.js`、`web/content-report.js`、`dashboard.py` | 顶部学科/课程切换、普通反馈、精确文字纠错 |
| 鉴权与运行 | `api.py`、`access.py`、`public_limits.py` | 学习者隔离、动态默认权限和显式覆盖、并发、公开接口限制 |

本次已确认的规则：

- 题目交互类型与“复刻/复现”判题模式独立。旧题缺省继续使用原问答与语义判题。
- 多判断、多空、配对均整题提交；全部小项正确才算正确，其他情况仅计一次错题。结构不完整、旧版本或判题故障不计错。
- 配对每侧候选数可多于框数；两侧可编辑，也可预填并锁定任一侧。关系一对一；两侧可编辑时整行排序不影响正确性。
- 综合范式训练的 Lesson 第四次练习错误暂停，Review 第三次错误暂停；非学员 6 小时后恢复，学员等待管理员解锁。全站移除主动暂停；初学模块仅学员可跳过，见 [模块跳过与冷却](module-skip.md)。原子训练无错误阈值暂停，也不提供跳过。
- 原子训练初学答对不评分，6 小时后首次复习；初学答错记 1；正式复习答错 1、答对 3。同组块两次卡片展示至少间隔 3 分钟。完整内容合同和学习规则见 [两套 Topic 架构](topic-training-modes.md)。
- 切换学科/课程保留浏览器当前身份的未提交答案；仅浏览目录不改变课程。
- 文字纠错仅存建议，携带正文版本、块 ID、精确选区和前后文；不会直接修改课程或成绩。

正式数据仍由腾讯云维护。本地验收使用 `scripts/shared_features_fixture.py` 的合成课程；不得把验收课程加入正式目录。部署与备份见 [腾讯云运行说明](tencent-deployment.md)，题型作者规范见 [题型与判题](question-types.md)。
