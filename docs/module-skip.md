# 模块跳过与自动暂停

六个学科共用这套机制。跳过仅作用于综合范式训练的当前初学模块；引论、复习和原子训练没有跳过。全站删除主动暂停按钮，旧公开暂停接口返回 403 `manual_pause_removed`；可信服务端旧暂停与精确解锁保留用于历史兼容。

学员点击“跳过此模块”后，当前轮次标为 `skipped`、mastery 为 null，进入下一模块。判题中的当前模块也可跳过，迟到结果不能写入新轮次。跳过不会生成 FSRS 卡或复习任务，其他完成模块的正常复习保留。非学员仍能点击按钮，提示“该功能仅对学员开放”；服务器根据学员资格与功能授权再次校验。

`topics.body.skipped_modules` 按模块保存精确事件及 `released` 状态。初轮结束后 Topic 进入学习历史，含跳过模块时显示半勾；`initial_completed_at` 保留首轮时间。管理员解锁会标为 released，同一 Topic 只产生一个 Lesson，按原稿顺序只补学已解锁的跳过模块。补学完成才创建该模块的 FSRS，所有模块补完后恢复全勾。跳过时默认清除 `skip_downstream_allowed`。

下游默认受未补学模块阻挡。管理员可在暂停管理页勾选放行，操作必须匹配当前全部跳过事件；取消勾选恢复阻挡。放行不绕过其他前置知识、独立暂停、进行中的复习或到期 FSRS。

自动暂停的错题阈值保持不变：Lesson 第四次 Practice 错误，Review 第三次错误。学员按原规则等待管理员解锁；非学员保存持久 `cooldown.resume_at`，6 小时后恢复。Lesson 从当时暂停模块开新轮次，保留已完成模块、审计记录及跨轮次的抽题队列。Review 只重开复习轮次，保留先前 Lesson 和 FSRS。网页恢复查询或重新进入时，服务端按 UTC 精确边界解除冷却，不依赖进程内计时器；旧非学员自动暂停按原事件时间补充 6 小时边界。

暂停 Topic 的任务使用与前置受阻相同的锁图标，开始按钮禁用，接口也阻止新的作答。学员管理报告保持只读，不触发恢复或状态迁移。

验收：`tests/test_module_skip.py`、`tests/test_ordered_learning.py`；浏览器合成数据入口 `scripts/module_skip_fixture.py` 与 `scripts/module_skip_acceptance.mjs`。隔离数据和截图均位于 D:/Artifacts，正式学员进度不作为测试材料。
