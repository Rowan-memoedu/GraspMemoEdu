# 两套 Topic 训练架构

两种模式共用身份、课程目录、学科主题、题型控件、判题、事务提交与备份。默认的 `paradigm` 保持既有数据兼容；`atomic_retrieval` 是独立学习状态机。数学、物理继续使用范式训练；原子模式目前只允许英语、语文、生物、化学，服务端同时校验 Topic 声明与课程实际学科归属。

| 项目 | 综合范式训练 `paradigm` | 原子化提取训练 `atomic_retrieval` |
| --- | --- | --- |
| 内容组织 | Introduction → Example → Practice | Introduction → 记忆组块 → 原子记忆点；独立 Flashcard 指向记忆点 |
| 练习目的 | 使用 Introduction 中的方法解决算例 | 回忆 Introduction 中的知识本身 |
| 记忆状态归属 | 学习者 / Topic / Example 模块 | 学习者 / Topic / 记忆点 |
| 设问形式 | 模块下的 Practice 储备题 | 一个点可有多种 Flashcard；一张复习卡可关联多个点 |
| 暂停 | 保留既有主动及自动暂停规则 | **只有主动暂停，无错题次数触发的自动暂停** |

## 已确认的原子训练规则

- 初次进入只显示 Introduction，提示“请确认已理解讲解内容，开始训练后不得随意返回查看”，唯一训练入口“已理解，开始训练”。
- 记忆组块显示相关 Introduction 陈述、虚线下划线、斜箭头与批注框。三种线条同用学科主题色，批注框浅色填充。
- 每个点至少有一张单点卡，用于初学。先呈现组块，再呈现其到期单点卡；同组块两次卡片展示至少相隔 **180 秒**。其他组块可穿插；全部暂不可用时等待下一次到期。时间、顺序和卡片归属均由服务端决定，刷新、多标签页不能跳过间隔。
- 初学答错返回 **1** 并调用 FSRS；该点留在初学队列。初学答对返回 **null**，从初学队列移出，以这次答对时间为起点，**6 小时后首次复习**。
- 一次也未答错的点，在首次正式复习之前只有到期标记，`card_json=null`，没有稳定性、难度或评分日志。若初学已答错，则保留错误产生的记忆状态；后续初学答对只安排首次复习，不新增评分。
- 本 Topic 全部点初学答对后完成。Introduction 在此之前不能从历史返回查看；已掌握卡片绿色标记并可查看，仍将重现的卡片红色标记且不可查看。锁定项的题面、作答和解析不会放进公共状态，旧提交重放也受相同限制。
- 四科导航增加独立“复习”页面，按到期点推送卡片，使用与初学相同的控件、反馈和学科配色。初学完成后可以查看 Introduction。
- 正式复习默认答错 **1**、答对 **3**，直接复用现有 `FSRSEngine`，调用上游 [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)；不复制调度公式。
- 变式按使用次数轮换，共享原记忆点状态。打包卡只有其关联点全部到期、组块间隔全部满足时才可推送；优先选可用的多点卡。整卡继续采用共用的“全部小项正确才正确”判定，同一结果分别更新关联点的 FSRS 状态；不会更新其他点。
- 暂停冻结整个 Topic 的推送，保留已有记忆状态。管理端用精确事件解锁。游客主动暂停权限继续遵循平台原有约束。
- 记忆组块作为网络节点及相关复习机制属于后续功能，本次不实现。

## 作者与开发者入口

原子模式使用新的 JSON 作者格式，复用现有安全 Markdown / 离线 MathJax 渲染器。原有范式 Markdown 导入流程保持兼容。

```json
{
  "id": "sample-atomic-topic",
  "title": "仅示意结构",
  "subject_id": "english",
  "introduction": [{"id": "statement-a", "text": "讲解的一个陈述。"}],
  "memory_chunks": [{
    "id": "chunk-a", "title": "记忆组块",
    "statement_ids": ["statement-a"], "content": "这个组块的内容。",
    "points": [{"id": "point-a", "text": "开发者定义的原子记忆点。"}]
  }],
  "flashcards": [{
    "id": "card-a-1", "title": "第一种设问", "point_ids": ["point-a"],
    "question": "此处填写设问。", "explanation": "此处填写答案与讲解。",
    "reference_answer": "标准答案",
    "interaction": {"version": 1, "type": "text", "grading": "exact", "answers": ["标准答案"]}
  }]
}
```

`point_ids` 引用这个 Topic 内的稳定记忆点 ID。增加同点变式时添加新卡并引用相同 ID；综合卡引用多个 ID。所有点仍须有单点卡，避免初学必须依赖未学的其他点。题型配置复用 [题型与判题](question-types.md)。

```powershell
$env:GRASPMEMOEDU_PYTHON = 'D:\AppData\GraspMemoEdu\venv\Scripts\python.exe'
node scripts/import_atomic_topic.mjs <source.json> <new-topic.json>
```

转换器机械生成 Introduction 的精确 Unicode 批注位置、题面 HTML、公式样式、来源哈希和版本，检查重复 ID、缺失单点卡、非法学科及悬空关联。拒绝覆盖既有输出；不写正式课程目录、账号或学习记录。本次只使用独立目录的合成内容，未上传任何真实或测试课程到正式站点。

作者可选 `atomic_policy`：

```json
{"version":"atomic-v1","first_review_seconds":21600,"correct_rating":3}
```

策略随初学状态保存，避免中途配置漂移。未来调整默认评分或首轮时间时应使用新的策略版本，并明确既有记录的迁移范围；不能改课程版本后强行复用旧状态。主动暂停不包含可配置的自动错误阈值。

## 接线与验证

- `atomic_content.py`、`atomic_author.py`：内容合同与机械编译。
- `atomic.py`：点级状态机；复用 `topics/actions/submissions/events` 与后台判题执行器。API 仍使用 `/api/state`、`choose`、`continue`、`submit`、`submissions`、`pause`，增加只读 `/api/atomic/reviews`。
- `web/atomic.js`：初学、复习共享视图；身份、Topic、版本和展示实例隔离的草稿；失败不丢答案。
- 管理侧 `status`、`review-status`、`unlock --module atomic` 与 `review-unlock` 支持原子模式，网页不提供解锁入口。
- `tests/test_atomic*.py` 覆盖评分、6 小时到期、180 秒间隔、打包条件、变式、锁定、幂等、并发、判题失败、重启恢复、手动暂停和迟到结果、课程版本、四科权限及备份。
- `scripts/atomic_fixture.py` 与 `scripts/atomic_acceptance.mjs` 在独立 loopback 服务上使用真实 Chrome 和真实判题，可注入测试时钟；正式入口没有改时钟或注入内容的 API。
