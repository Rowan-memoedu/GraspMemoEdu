# 独立题库与逐题训练

六科学科导航都在“指南”左侧提供“题库”，沿用原导航间隔。题库首页复用课程卡片布局，分组入口为“训练”；进入后按树形显示“题组”，题组入口为“开始训练”。每道题独立展示、独立记忆卡，不使用课程的先修、顺序解锁或自动暂停规则。任意选题、反复作答以及回看自己的记录均可使用。

## 共用层与独立数据

`training_content.py` 管理题库，`training.py` 管理作答和单题排期，`training_api.py` 注册公开学习端点，`web/training.js` 管理页面。复用 `question_types.py`、`judging.py`、`FSRSEngine`、`questionInput` 和身份/配额/后台判题队列。题库题目不进入原课程 Catalog，不改变 Topic ID、课程内容、学习任务或原课程复习记录。

题库数据位于私密数据目录的 `training-banks/<bank-id>.json`。它与用于现有课程题型转换的 `question-banks/<topic-id>/<revision>.json` 不同，二者不互相收集内容。本次交付只有架构，不附带正式题库内容。

SQLite 新增 `training_attempts`、`training_submissions`、`training_memory`、`training_ratings` 和 `training_preferences`。每个身份、题库版本、题目只有一张记忆卡；不限作答次数。题目内容和选项顺序在开始本次作答时冻结。客户端刷新恢复同一次作答；请求重试不会再判题或重复排期。两次并发作答只有匹配记忆卡版本的一次能更新排期，另一次保存为额外训练。

## 评分

- 普通作答与现有原子化卡片一致：首次答错记 1；首次答对不产生新评分，首次复习定在 6 小时后，并保留此前答错产生的 FSRS 状态。正式复习答错 1、答对 3。
- 账号同时拥有高阶学习者资格和 `training_self_rating` 功能时，问答题可以留空点击 `Show Answer`，显示 `Explanation` 后按重来、困难、良好、简单选择 1–4 分。默认开启，可在设置关闭。非空输入仍走共享判题；填空、判断、选择、配对只允许普通作答。仅查看答案不产生记忆卡或评分。
- 已进入排期的卡片尚未到期时，主动重做只保存作答记录，既不更新 FSRS，也不修改到期时间，自评同样遵循此规则。
- 语义判题故障不计错；保留原提交以便重试。普通作答继续使用共享判题并发和每日配额。
- 六科顶部“复习”统一展示到期题库卡片。原子化学科的既有复习区独立保留，原课程学习主页的 Review 任务不变。

## 权限与管理员

每个题库注册一个独立权限标识 `training.<bank-id>`，复用现有动态默认权限。显式指定 Topic/功能列表的账号不会自动获得新增题库或高阶功能。管理命令仍通过服务器私密 CLI 使用；公开端点不提供内容导入、建号、提权或排期修改。

管理员沿用当前登录会话的 `MemoryLearningStore`；后台作答任务持有生命周期租约。退出后临时记录销毁，正式学习数据库和备份中不包含管理员训练记录。浏览器答案、待提交记录及计时使用共享 `createLearningCache`，不写管理员 Web Storage。管理员复习入口按目录显示全部题库题目。

## 作者输入与安装

作者 JSON 使用 `id`、`subject_id`、`title`、可选 `description` 和 `groups`。每个题组有 `id`、`title`、可选 `parent_id` 和 `questions`；父级可以只用于分类。每道题有稳定 `id`、`title`、Markdown `question`、Markdown `explanation`，以及可选 `reference_answer`、`constraints`、`answer_type`、`interaction`。不传参考答案时机械使用解析原文，不调用 LLM 改写。题型结构同 [题型规范](question-types.md)。

```powershell
$env:GRASPMEMOEDU_PYTHON = 'D:\AppData\GraspMemoEdu\venv\Scripts\python.exe'
node scripts/import_training_bank.mjs '<作者 JSON>' '<新的编译 JSON>'
```

导入器复用既有安全 Markdown 与离线 MathJax 渲染器。相对 PNG 按现有附件约束读取并内嵌；来源 SHA-256 保存在编译产物中。保留原稿和原图在已授权的私密来源包中。校验稳定 ID、题型、树环、重复题目和整份内容哈希。

正式安装在服务器使用：

```sh
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu.training_content install <compiled.json> /var/lib/graspmemoedu
sudo systemctl restart graspmemoedu
```

目标 ID 已存在时拒绝覆盖；新的内容版本使用新 ID，避免旧卡片的题目与答案被悄悄替换。现有整站备份包含题库及对应训练表，备份/恢复均核验保存记录与题库版本一致。恢复到新目录，旧备份没有题库时仍兼容。

## 验收

后端回归为 `tests/test_training.py` 与 `tests/test_training_api.py`。`scripts/training_fixture.py` 在新的 `D:/Artifacts/GraspMemoEdu` 子目录启动合成题库；`scripts/training_acceptance.mjs` 使用隔离 Chrome 上下文和真实判题检查导航、任意选题、草稿、普通作答、四级自评、复习、设置和管理员缓存边界。它们不向正式站点导入验收题目，也不使用正式学习者进度。
