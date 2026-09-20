# Example 复习后端

网站已接通此 Python 后端，复用 `py-fsrs==6.3.2`（MIT）。`create_app()` 为学习服务绑定复习后端，沿用身份、Topic/功能授权、提交限流和判题工作池。新完成模块开始排期，历史记录不自动回填。下述 Python 管理接口属于可信本机入口，不直接暴露给浏览器。

## 组件与边界

- `math_learning/fsrs_engine.py`：调用上游 `Scheduler`，不修改 FSRS 公式。数字 1/2/3/4 分别对应 Again/Hard/Good/Easy。
- `math_learning/practice.py`：Lesson 与复习共用补题、错误计数与结算逻辑；两者分别使用自己的规则。
- `math_learning/judging.py`：共用现有 MathAnswerJudge 判题约定；网络判题不持有数据库写锁。
- `math_learning/review.py`：Example 卡片、到期任务、复习轮次、评分记录、参数配置和优化接口。

每个 `learner_id + topic_id + module_id` 对应一张 FSRS 卡片。Practice 只是这张卡片某次复习时的抽题，不建立自己的调度卡片。课程版本不匹配时拒绝沿用旧卡，保留原数据。首次成功结算 Lesson 模块后，以该模块评分和真实完成时间调用 FSRS；后续完整复习轮次各调用一次。

FSRS 计算与四档评分遵循上游实现。默认使用上游的目标保留度 0.9、学习步骤 1/10 分钟、重学步骤 10 分钟及间隔扰动。**不能保证与某台 Anki 的所有具体到期时间完全相同**：Anki 的牌组配置、每日切换时间、负载分配及版本也影响排程；py-fsrs 的优化实现与 Anki Rust 实现存在差异。本阶段没有复制 Anki 的整套应用调度层。

上游资料：[py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)、[固定版本](https://pypi.org/project/fsrs/6.3.2/)、[Anki 牌组选项](https://docs.ankiweb.net/deck-options.html#fsrs)。

## 接收已有 Lesson 评分

```python
from math_learning.service import LearningService
from math_learning.review import ReviewBackend

lesson = LearningService(course, data_dir, judge, learner_id=learner_id)
reviews = ReviewBackend(lesson)

# 后端启动时执行一次，必须早于任何判题任务。
lesson.recover_interrupted()
reviews.recover_interrupted()
```

构造 `ReviewBackend` 是显式启用点。它给这一个 `LearningService` 注册完成、暂停和解锁事件；重建服务对象时也要重新构造。Lesson 的评分和 FSRS 更新在同一个 SQLite 事务中提交。网站 `create_app()` 已构造它并在启动时恢复中断提交；本机旧 Lesson 解锁入口也绑定复习后端以同步解冻。

若结果来自其他已验证的后台流程，可以直接提交整段 Example 模块的结果。这个接口不应原样暴露给浏览器，让用户自行填写分数：

```python
scheduled = reviews.record_module_result(
    module_id="example-1",
    mastery=4,
    result_id=stable_module_result_id,
    completed_at=original_completion_datetime,  # 带时区；内部统一 UTC
    elapsed_ms=measured_module_answer_time_ms,  # 可省略，不补造旧记录用时
)
# scheduled 包含 module_id、mastery、completed_at、due_at、source_id、algorithm。
```

同一结果 ID 重放只返回已有调度结果；改变其评分或完成时间会报冲突。时间更早的结果不能覆盖已经更新的卡片。`None`、0、5、字符串和布尔值均不能作为 FSRS 评分。进行中与暂停结果不进入此入口。

构造后端不会自动回填历史 Lesson。未来回填须另外明确范围，并传入每条结果原始 ID 与时间。

## 网站接入

- `GET /api/dashboard` 汇集当前课程已授权 Topic 的到期卡片和未完成轮次，返回 `type: "Review"` 与 `next_review_due_at`。主页到期自动查询，页面隐藏时暂停查询、恢复可见后继续。已完成轮次并入现有学习历史分页。课程完成度仍由 Lesson 决定。
- `POST /api/review/begin` 接收 `module_id/request_id`；`GET /api/review/sessions/{id}` 读取当前轮次。前端地址为 `#/review/<topic>/<module>?session=<id>`，刷新恢复相同轮次。
- `POST /api/review/sessions/{id}/submit`、`/continue`、`/pause` 对应提交、下一题与主动暂停；`GET /api/review/submissions/{id}` 读取判题结果。前两者使用 revision 与 question_id 校验；所有请求带 `topic_id` 并核对身份和权限。
- 复习页面只有当前 Practice 和本轮已答题，不提供 Example、Introduction、未来题或未作答题解析。游客主动暂停返回 403；游客自愿开启体验浏览时，`GET /api/review/sessions/{id}/demo-answer` 只提供当前可提交题的标准答案，已有草稿不覆盖。
- 保留度、优化、解锁及后台配置不开放网页接口。`review-status --learner ... --topic ...` 查看本机复习状态；`review-unlock --learner ... --topic ... --pause-event ...` 按精确暂停事件解锁，Lesson 已完成时同样有效。
- 本次推送指网站内到期任务；浏览器系统通知、邮件与站外消息未接入。

## 到期推送与复习轮次

```python
tasks = reviews.due(limit=50)
next_wakeup = reviews.next_due_at()
round_state = reviews.begin(tasks[0]["module_id"], request_id=start_request_id)

submission = reviews.submit(round_state["session_id"], {
    "request_id": answer_request_id,
    "expected_revision": round_state["revision"],
    "question_id": round_state["practice"]["id"],
    "answer": submitted_answer,
    "elapsed_ms": measured_answer_time_ms,
})
# 交给后台判题工作池；此调用可能等待现有网络判题服务。
reviews.process_submission(submission["submission_id"])
result = reviews.submission(submission["submission_id"])
```

- `due()` 返回该学习者、当前 Topic 的到期 Example，按到期时间排序；返回任务类型、内容标识、时间与调度修订号。按账户的已授权 Topic 汇集这些结果即可组成网站复习队列。
- `next_due_at()` 供后端决定下一次查询时间。SQLite 到期队列在停机后仍保留；未领取任务不会因某次轮询或服务重启丢失。它服务于网站内推送，本阶段不包含浏览器 Push、邮件或消息发送。
- 同 Topic 同时最多进行一轮复习。开始后从可推送队列移除，刷新或重新领取同一 Example 会恢复该轮；不同请求争抢不会重复创建轮次。
- 每轮按随机无放回顺序抽题，并持久保存抽题顺序。答题返回只包含当前 Practice 和已完成记录，不返回 Example、Introduction、未来题目或标准答案。答题成功后才提供该 Practice 的解析。
- 未完成轮次的结果反馈必须由调用方调用 `advance(session_id, {request_id, expected_revision, question_id})` 推进。完成轮次的状态直接包含 `mastery` 与新的 `due_at`。
- 重复提交不重复判题或补题。判题异常不计错，原请求可重试；启动恢复会把中断请求置为可重试。暂停或解锁后才到达的旧结果不能推进新轮次。

## 可修改的评分规则

| 复习情况 | 累计题量 | 熟练度 |
|---|---:|---:|
| 第一题正确 | 1 | 4 |
| 最终错 1 题，完成全部补题 | 3 | 2 |
| 最终错 2 题，完成全部补题 | 5 | 1 |
| 第 3 次错误，或主动暂停 | 立即终止 | `null` |

当前复习规则没有产生 3 的条件；算法接口仍完整支持 3，Lesson 也继续使用自己的 4/3/2/1 规则。

规则入口是 `REVIEW_POLICY = PracticePolicy('review-v1', (1, 3, 5), (4, 2, 1))`。`targets` 与 `ratings` 按错误数索引，错误数达到数组长度就暂停。未来可通过 `ReviewBackend(lesson, policy=...)` 注入新规则。每次规则变更使用新版本名，已开始的轮次继续使用保存的旧规则快照；评分日志记录实际使用的版本。

## 暂停和管理解锁

`pause(session_id, request_id=...)` 或第 3 次错误沿用“整个 Topic 暂停”的语义。若 Lesson 仍在进行，也会暂停其当前未完成模块；已完成模块评分保留。若 Lesson 已全部完成，保留其完成状态，同时停止该 Topic 的所有复习推送。暂停的复习不调用 FSRS，保留原卡与到期时间。

`unlock_topic(pause_event_id=...)` 是仅供本机管理侧调用的入口。它按精确事件恢复整个 Topic；若 Lesson 同时暂停，复用其原有解锁规则。旧复习轮次作为历史保留，再次 `begin()` 新建轮次、重新抽题，错误数清零。对同一事件重复解锁不会重复创建轮次，也不能误解锁后来的暂停。

学习权限（包括游客不能主动暂停）由现有账号系统决定；正式接线时在调用这些后端方法前执行原有权限检查，不给网页开放解锁入口。

## 设置保留度、优化与应用参数

设置按学习者与 Topic 持久保存，每次评分记录当时的完整调度参数：

```python
current = reviews.scheduler_settings()
reviews.configure_scheduler(desired_retention=0.95)
# 默认只影响后续评分；显式指定才重算既有卡片。
reviews.configure_scheduler(desired_retention=0.95, reschedule_existing=True)
```

保留度取 0 到 1 之间的数值，不含端点。`parameters=[...]` 可设置完整的 21 个模型参数；交由上游检查数值范围。重排使用上游 `reschedule_card()` 从原复习日志重算，保留原始评分与旧参数快照；有进行中的复习时拒绝重排，避免更改已领取任务。

优化在本机运行，不上传学习记录。普通调度依赖很小；优化的可选运行环境单独锁定在 `requirements-optimizer.txt`：

```powershell
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m pip install --no-compile --cache-dir 'D:\Caches\pip' -r 'D:\Projects\MathLearningWeb\requirements-optimizer.txt'
```

```python
status = reviews.optimization_status(scope="learner")
candidate = reviews.optimize_parameters(scope="learner")
# 生成候选不会改变当前设置或到期任务。
reviews.apply_optimization(candidate["optimization_id"], reschedule_existing=False)
# 若同时生成了建议保留度，可明确选择采用它：
candidate = reviews.optimize_parameters(scope="learner", include_optimal_retention=True)
reviews.apply_optimization(candidate["optimization_id"],
                           use_recommended_retention=True, reschedule_existing=True)
```

- `scope="learner"` 默认汇集同一学习者各 Topic 当前内容版本的复习记录；`scope="topic"` 只使用本 Topic。不能读取其他学习者记录。生成的候选应用于当前后端所绑定的学习者与 Topic，不自动修改其他 Topic。
- **py-fsrs 6.3.2 真正训练参数至少需要 512 条有效记录**：每张卡只考虑前 64 次，首条不计，后一次必须与前一次相隔至少 24 小时。上游在不足时返回默认参数，本封装改为明确报告数据不足，不保存“已优化”候选。
- 因此仅有 6 个 Example 的单一 Topic 最多贡献 378 条此类记录；可从同一学习者更多 Topic 汇集。`optimization_status()` 返回实际可用数量及依赖情况，不把“有 512 条任意历史记录”当作满足训练条件。
- 自动建议保留度还要求至少 512 条均有实际作答用时的日志；旧记录缺少用时不会填入虚构值。自动建议保留度是可选能力，不影响手动设置保留度或只优化模型参数。
- 候选保留算法版本、样本量、数据哈希及评分规则版本。训练使用一致的数据快照，耗时计算不持有 SQLite 写锁；只有显式应用才改动设置，应用事件关联候选 ID。

## 验证

```powershell
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m unittest tests.test_review -v
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' scripts/verify_review_backend.py --real-judge --output 'D:\Artifacts\MathLearningWeb\fsrs-backend\<新的验收目录>'
```

测试包含四档评分与上游对照、到期边界、随机补题、规则版本、重复/并发请求、暂停与在途判题、重启、身份隔离、保留度设置、重排、备份恢复，以及超过上游门槛的真实参数优化。真实判题 Canary 使用独立生成的算术课程、新数据目录和模拟推进的时钟，验证 Lesson 评分 → FSRS → 到期领取 → 错一题补两题 → 评分 2 → 下一次调度 → 重启回读。不会打开正式网站数据库。
