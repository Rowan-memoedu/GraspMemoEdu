# 共用平台与学科开发边界

当前共用层由本仓库统一维护；六个学科共用一个应用、账号系统、Lesson/Review 流程与判题入口。学科任务负责自身课程与教学内容，通用功能不复制成六套。

| 层 | 入口 | 职责 |
| --- | --- | --- |
| 学科外观与路由 | `web/subjects.js`、`math_learning/subjects.py` | 六科学科主题、路由、课程归属；旧数学路径继续兼容 |
| 课程与内容 | `catalog.py`、`author_import.py`、`scripts/import_course.mjs` | 作者 Markdown 机械导入、离线公式、来源校验、稳定 Topic ID |
| Lesson | `service.py`、`web/app.js` | 掌握选择、指导、练习、暂停、幂等提交、历史与草稿 |
| 复习 | `review.py`、`review_api.py`、`web/review.js` | FSRS、到期任务、持久练习队列；沿用原评分政策 |
| 原子训练 | `atomic.py`、`atomic_content.py`、`atomic_api.py`、`web/atomic.js` | 记忆组块与点级状态、交错、可变设问、打包复习 |
| 题型 | `question_types.py`、`judging.py`、`web/question-input.js` | 统一 schema、公共字段投影、作答控件、确定性/语义判题 |
| 导航与反馈 | `web/portal.js`、`web/catalog-picker.js`、`web/content-report.js`、`dashboard.py` | 顶部学科/课程切换、普通反馈、精确文字纠错 |
| 鉴权与运行 | `api.py`、`access.py`、`public_limits.py` | 学习者隔离、动态默认权限和显式覆盖、并发、公开接口限制 |

本次已确认的规则：

- 题目交互类型与“复刻/复现”判题模式独立。旧题缺省继续使用原问答与语义判题。
- 多判断、多空、配对均整题提交；全部小项正确才算正确，其他情况仅计一次错题。结构不完整、旧版本或判题故障不计错。
- 配对每侧候选数可多于框数；两侧可编辑，也可预填并锁定任一侧。关系一对一；两侧可编辑时整行排序不影响正确性。
- 综合范式训练的 Lesson 第四次练习错误暂停，Review 第三次错误暂停；原子训练只保留主动暂停。
- 原子训练初学答对不评分，6 小时后首次复习；初学答错记 1；正式复习答错 1、答对 3。同组块两次卡片展示至少间隔 3 分钟。完整内容合同和学习规则见 [两套 Topic 架构](topic-training-modes.md)。
- 切换学科/课程保留浏览器当前身份的未提交答案；仅浏览目录不改变课程。
- 文字纠错仅存建议，携带正文版本、块 ID、精确选区和前后文；不会直接修改课程或成绩。

正式数据仍由腾讯云维护。本地验收使用 `scripts/shared_features_fixture.py` 的合成课程；不得把验收课程加入正式目录。部署与备份见 [腾讯云运行说明](tencent-deployment.md)，题型作者规范见 [题型与判题](question-types.md)。
