# 独立题库与逐题训练

六科学科导航都在“指南”左侧提供“题库”，沿用原导航间隔。题库首页复用课程卡片布局，分组入口为“训练”；进入后按树形显示“题组”，题组入口为“开始训练”。每道题独立展示、独立记忆卡，不使用课程的先修、顺序解锁或自动暂停规则。任意选题、反复作答以及回看自己的记录均可使用。

## 共用层与独立数据

`training_content.py` 管理题库，`training.py` 管理作答和单题排期，`training_api.py` 注册公开学习端点，`web/training.js` 管理页面。题组树直接调用学习主页的 `courseSidebar`、`taskTree` 和任务卡组件；训练页直接复用 `index.html` 的阅读框架及原样式，左侧保留学习记录和移动端抽屉。`learning-ui.js` 的作答表单同时用于原 Lesson 和训练页，仅由各自调用者处理提交。复用 `question_types.py`、`judging.py`、`FSRSEngine`、`questionInput` 和身份/配额/后台判题队列。题库题目不进入原课程 Catalog，不改变 Topic ID、课程内容、学习任务或原课程复习记录。

题库数据位于私密数据目录的 `training-banks/<bank-id>.json`。它与用于现有课程题型转换的 `question-banks/<topic-id>/<revision>.json` 不同，二者不互相收集内容。书籍来源、OCR 和清洗流程由独立的 GraspMemoEduSourcePrep 项目维护，网站只接收经过来源校验的编译产物。

已安装书籍的机械格式修正保存在 `training-formats/<bank-id>.json`，绑定原题库版本和每题原内容哈希。只允许替换题目正面及 `interaction`、去掉指定的无题目单子节点包装层；原题库、答案、解析、题目/小节 ID、版本与学习进度均保持。`TrainingCatalog.bank()` 返回格式化视图，`banks` 保存原始内容；开始作答时冻结当前视图，已开始轮次继续原快照。原库和格式文件一同备份、恢复校验。格式安装为新增/相同内容幂等，拒绝静默覆盖不同格式。

书籍单选/多选按原选项排序（`shuffle:false`），由脚本判分。多选用复选框，少选、多选或错选均为整题错误；填空使用已有行内输入框和语义判分；大题保持文字作答与原答案模式。单选和多选不调用判题模型。

书籍排版统一由 `training_presentation.py` 机械处理：已有题型字段决定单选或多选，正文移除重复题型标签；题干和解析的小序号分段并留空行，保护公式、图像和参考答案。新作答冻结排版视图，历史快照不重写。显式格式更新必须匹配上一格式哈希，旧格式按哈希保存，并由现有整站备份保护。

SQLite 使用 `training_attempts`、`training_submissions`、`training_memory`、`training_ratings`、`training_preferences` 和 `training_suspensions`。每个身份、题库版本、题目只有一张记忆卡；不限作答次数。题目内容和选项顺序在开始本次作答时冻结。客户端刷新恢复同一次作答；请求重试不会再判题或重复排期。两次并发作答只有匹配记忆卡版本的一次能更新排期，另一次保存为额外训练。

## 评分

- 普通作答首次就答对记 5，保存学习记录并停止后续推送，不向 FSRS 传入 5。首次答错记 1，之后首次答对记 3，沿用已产生的 FSRS 卡片排期。正式复习答错 1、答对 3。新规则用于后续作答，不批量重写历史记录或排期。
- 账号同时拥有高阶学习者资格和 `training_self_rating` 功能时，问答题可以留空点击 `Show Answer`，显示 `Explanation` 后出现五个不同颜色的按钮。1–4 的小字分别为“做错了”“勉强做对，但很困难”“正常做对”“轻松做对”；5 为“极度熟悉，不必再练”。不显示按钮上方的额外评分提示语。默认开启，可在设置关闭。非空输入仍走共享判题；填空、判断、选择、配对只允许普通作答。仅查看答案不产生记忆卡或评分。
- 第五个按钮通过独立 `training/stop` 操作停止此身份、题库版本、题目的后续推送，不向 FSRS 传入评分 5。保留已有卡片及作答记录；刷新、重启、备份恢复和在途判题都不会恢复推送。
- 已有学习记录的题目才显示“提前复习”；只打开题目或显示答案不算完成学习。已进入排期的卡片尚未到期时，提前复习只保存作答记录，既不更新 FSRS，也不修改到期时间，自评同样遵循此规则。停止推送的题目仍可手动复习，但不会因此恢复推送。
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

书籍 OCR 可加 `--literal-prose`，将方括号题源和裸不等号按字面显示，并兼容 OCR 中不成对的 TeX 伸缩括号控制；不改写保存的原题或答案。每个题干、解析采用自包含的 MathJax 原生字形缓存，减少重复 SVG 路径。原文字段上限为 100,000 字符，`html`、`explanation_html` 各为 1,000,000 字符，完整题库文件仍限 32 MB。编译失败和体积诊断保存在输出目录，不会写出部分成功的题库。

正式安装在服务器使用：

```sh
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu.training_content install <compiled.json> /var/lib/graspmemoedu
sudo systemctl restart graspmemoedu
```

目标 ID 已存在时拒绝覆盖；新的内容版本使用新 ID，避免旧卡片的题目与答案被悄悄替换。现有整站备份包含题库及对应训练表，备份/恢复均核验保存记录与题库版本一致。恢复到新目录，旧备份没有题库时仍兼容。

## 验收

后端回归为 `tests/test_training.py` 与 `tests/test_training_api.py`。`scripts/training_fixture.py` 在新的 `D:/Artifacts/GraspMemoEdu` 子目录启动合成题库；`scripts/training_acceptance.mjs` 使用隔离 Chrome 上下文和真实判题检查导航、任意选题、草稿、普通作答、五个按钮、停止推送、复习、设置和管理员缓存边界，并对照原学习页检查桌面与手机的布局及组件尺寸。它们不向正式站点导入验收题目，也不使用正式学习者进度。
