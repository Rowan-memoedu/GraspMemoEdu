# GraspMemoEdu

当前正式后端已于 2026-09-21 迁至腾讯云 `https://124.221.144.155`，GitHub Pages 前端地址保持不变。部署、证书续期、备份和回滚边界见 [腾讯云部署](docs/tencent-deployment.md)。下文 Windows 命令仅作隔离开发示例，必须显式指定隔离数据目录；正式管理在服务器执行，不要对本机回滚快照或旧维护任务执行它们。

课程图谱使用独立的 `Import-Graph.ps1` / `graph-import`，支持先于 Topic 导入、差异预览、重复更新、历史恢复及离线可视化。Topic 导入不覆盖图谱，图谱更新不改正文和学习进度。标准格式和工作流见 [独立课程图谱](docs/course-graph.md)。

作者可在本机将校准好的 Markdown 及相对路径 PNG 插图机械转换为 Topic：解析结构、按字段注入、搬运附件并验证来源一致性；默认不调用 API，不逐题审查内容。API 只用于极少量抽样检查整体性损坏。支持批量准备来源包、新建整门课程和局部修复。调用方法和输入格式见 [作者导入工作流](docs/author-import.md)。

多学科学习平台：总首页提供数学、物理、英语、语文、生物、化学入口，古典衬线字和纸色背景仅作用于总首页。数学和设置页恢复原有视觉；其他学科共用原模板，使用各自配色、名称与徽标。数学保留既有课程与记录，不新增测试课；其余五科测试内容已下线，保留学科入口与独立配色；测试原文件留在本机，历史学习记录已迁至服务器；物理正式课程「运动学进阶」已发布，共 22 个 Topic。账号、设置、学习和复习页面共用实现，配置与初始化见 [学科配置与扩展](docs/subjects.md)。本机源码和数据目录继续沿用 MathLearningWeb 名称，仓库与公开网页使用 GraspMemoEdu 品牌。

网站已接通 Example/FSRS 复习：新完成的 Lesson 模块自动计算下次复习时间，到期在主页显示“复习”；复习只抽取对应 Practice。完成后更新下次时间，已完成轮次可从学习记录回看。评分规则、接口和服务端管理见 [复习后端接口](docs/review-backend.md)。原有完成记录没有自动回填。

每个学科的顶部提供学习、课程、指南入口，并可返回所有学科。数学当前课程为「再探小学算术」，包含 Topic「数字化为字母」：6 个 Example、每模块 15 道 Practice，合计 96 道题。原有逐块学习、真实 MathAnswerJudge 判题、练习补题、模块评分、暂停与恢复继续使用同一份进度。

界面默认中文，可在设置切换英文。统一译法与内容边界见 [界面语言规范](docs/ui-language.md)，完整文案见 [中英对照表](docs/ui-language-table.md)。

公开入口：[GraspMemoEdu](https://amymiller76717-sudo.github.io/GraspMemoEdu-Pages/)。其他电脑、手机可直接打开，自动进入游客模式，无需填写服务地址、配对码或授予本地网络权限。后端、课程和学习记录运行并保存在腾讯云；原始邀请码管理文件仍留在本机私密目录，服务器保存认证所需哈希。源码仓库为私有 `amymiller76717-sudo/GraspMemoEdu`；本机源码目录保留原名。

游客可进入全部六科学习、提交答案和回看已发布内容；主动暂停仍提示「该功能需注册账号才能使用」，后端同样限制。邀请码账号在全部学科通用，默认开放全部 Topic 和功能并自动继承新增内容；管理员明确设置的 Topic 或功能列表分别覆盖默认权限，见 [账号管理](docs/accounts.md)。

游客头像右侧的「体验专用浏览版」默认关闭。勾选后，后续尚未填写的答案框会预填当前题的标准答案，可修改后正常提交判题；已有草稿与手动输入不会被覆盖。它便于体验页面流程，不代替实际学习或测评。

## 打开与启动

正式网址：[GraspMemoEdu](https://amymiller76717-sudo.github.io/GraspMemoEdu-Pages/)。腾讯云上的 systemd 持续托管后端，个人电脑关闭、休眠或退出 ChatGPT 桌面端中的 Codex 均不影响网站运行。运维命令见 [腾讯云部署](docs/tencent-deployment.md)。

`D:/AppData/MathLearningWeb` 已标为迁移回滚快照，旧启动、维护和默认管理入口会拒绝使用它，不能与云端同时写入。开发时必须显式指定独立数据目录；本机 8765 属于 AnkiConnect，不要占用。示例（先向隔离目录导入测试数据）：

```powershell
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m math_learning --data-dir 'D:\Artifacts\MathLearningWeb\development-data' serve --port 8768
```

## 学习方式

1. Example 先选「已掌握」或「未掌握」。已掌握才出现答案框，支持分数、数值和等价表达式。
2. Example 答对立即得到熟练度 4，点击右下角「下一页」进入下个模块。首次答错或选择未掌握，经下一页插入 Introduction，然后返回该题解析。
3. 练习初始 2 道；错误量 1、2、3 时总量扩为 3、5、7 道。完成后熟练度为 3、2、1；两道全对为 4。每题判定后必须点击下一页。
4. 第 4 次练习错误暂停整个 Topic。具有对应权限的账号也可点击「暂时终止学习」，下方说明为「未掌握前置知识，感到困难，可选择后延该模块」。游客点击时提示注册，不会提交暂停请求；后端同样禁止游客主动暂停。自动暂停规则保持有效。
5. 学习记录默认在页面左侧展开，宽度约 309px，手机抽屉按屏幕宽度限制。右下角的 Submit、下一页共用一个位置，按当前状态显示；上一页、下一页回看已开启页面，当前学习页的下一页可在满足条件后推进，没有对应操作时不显示按钮。
6. 游客进度按浏览器身份分开保存，刷新后恢复；切换账号时使用独立进度。清除浏览器本地存储会失去原游客入口，管理员仍可通过学习者 ID 查看服务器记录。

## 课程主页与指南

首次进入先选学科，再在课程页点击学习选择课程；选择随当前身份按学科保存。学习主页显示课程进度、章节状态、真实前置关系图、待学习任务和按日期分组的已完成任务。到期“复习”按时间排在 Lesson 前，同一 Topic 优先恢复未完成的复习轮次。主页自动查询到期变化，关闭网页期间的到期任务会保留，重新打开后展示。课程进度仍按 Lesson 模块完成数计算；缺少预计完成日期、难度或旧作答用时的数据显示「—」，不生成测验或 XP。

点击已完成任务可回看各模块的实际作答、结果和解析。新作答记录页面可见且处于答题状态的累计用时，不计判题等待；旧记录不补造用时。更早记录支持分页、失败重试和去重。总首页为 `#/`，学科页面为 `#/subjects/<学科ID>/learn`、`courses`、`guide`、`topic/<id>`、`review/<topic>/<module>?session=<round>`。原来的 `#/learn` 等链接自动转到数学并保留查询参数。支持刷新、浏览器前进和后退，也适用于 GitHub Pages 子路径。

每个学习者的 Example 有一条跨 Lesson/复习保留的抽题队列：15 道 Practice 按 5 组各 3 题组织，首次每组随机选一题；某组第一次答错时，将其余两题追加到队尾，该组以后答错不重复补入。每轮只取实际需要的题，余题留给以后；不足时随机补未推送题，全都推送过后按最久未推送顺序复用，同轮不重复。详见 [Practice 持久队列](docs/practice-queue.md)。

复习每轮先做 1 题，每错 1 题增加 2 题的本轮题量，从上述队列继续取题。首题正确返回 4（Easy）；累计错 1 题并完成共 3 题返回 2（Hard）；累计错 2 题并完成共 5 题返回 1（Again）。第 3 次错误或主动暂停立即终止本轮，不评分、不更新 FSRS。当前没有返回 3（Good）的复习路径。游客不能主动暂停，自动暂停仍有效。以后调整 `math_learning/practice.py` 中的 `REVIEW_POLICY` 并递增版本；进行中的轮次继续使用原规则快照。

用户菜单的「设置」可修改显示名、时区和界面语言，默认中文，可切换英文；课程正文不随 UI 语言翻译。「帮助」打开常见问题 Q&A，「反馈」提交站内反馈，指南保留在顶部导航。游客可在头像右侧开启「体验专用浏览版」，学习任务的学习按钮旁可用黄色「重置」按钮重置当前游客的该主题；账号不显示这两个游客入口。题目记录中的内容纠错会附上课程与题目位置。反馈保存于服务器数据库，管理员查看：

```powershell
ssh ubuntu@124.221.144.155 'cd /opt/graspmemoedu/app && sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m math_learning --data-dir /var/lib/graspmemoedu feedback-list --limit 30'
```

正式指南由服务器 `/var/lib/graspmemoedu/subjects.json` 指定，当前位于 `subject-guides/<学科ID>/guide.md`，配图放在同级 `assets` 中。数学与物理指南已发布，其他四科为空。正文更新必须同步到服务器；只修改本机回滚快照不会更新网站。打开的 GUIDE 约每 5 秒检查更新并保留滚动位置；重新进入或刷新同样读取最新内容。读取或渲染错误会如实提示。网页没有指南或服务器配置编辑入口。

指南支持标准 Markdown、表格、代码和 `$...$`、`\(...\)`、`$$...$$`、`\[...\]` 数学公式。仅正文明确引用、位于指南目录内的图片/PDF/TXT/CSV 可通过只读资源入口访问；源文件、目录外文件、脚本和主动 TeX 不会发布。

正式课程目录保存在服务器 `/var/lib/graspmemoedu/catalog.json`。Course 只组织现有 Topic，不改变 Topic 的 ID、版本或进度；缺少目录时兼容原单 Topic 配置。新增课程应引用已审核导入的 Topic 文件，不能重复 Topic ID。示例：

```json
{"courses":[{"id":"arithmetic-revisited","title":"再探小学算术","description":"从熟悉的算术出发，用字母表达规律，重新理解计算。","topics":[{"id":"digits-to-letters","path":"courses/digits-to-letters.json","prerequisites":[]}]}]}
```

可选 `units` 组织章节，每章包含 `id`、`title` 和 `topic_ids`，必须恰好覆盖一次本课程的全部 Topic。省略时按课程生成一个章节；前置关系限于本课程且不能形成循环。账号授权仍以 Topic 和功能为边界。

## 邀请码与账号权限

管理员通过 SSH 在服务器创建正式账号并生成专属邀请码。邀请码可重复登录同一账号，跨设备共享该账号的进度；数据库仅保存凭据哈希，迁移前的原始邀请码清单仍保存在本机私密管理目录。账号按 Topic 与功能授权，Topic 清单以当前课程目录为准，以下用 `digits-to-letters` 举例。完整批次创建、停用、换码和会话管理见 [账号管理](docs/accounts.md)。

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' account-list
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' account-create --name '<显示名>' --topics digits-to-letters --features 'learn,submit_answer,review_history,pause_topic'
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' account-permissions --learner '<account-id>' --topics digits-to-letters --features 'learn,submit_answer,review_history'
```

`account-create` 省略 Topic 和功能参数时默认全部开放；`account-permissions` 省略的字段保持原值，传空字符串可清空该项授权。权限在下一次请求时生效，由后端强制校验。

| 功能标识 | 开放内容 |
|---|---|
| `learn` | 选择掌握情况、下一页推进 |
| `submit_answer` | 提交答案判题 |
| `review_history` | 学习记录、上一页和下一页 |
| `pause_topic` | 主动暂时终止学习 |

游客固定开放前三项功能及全部学科的已配置 Topic，既有游客身份自动继承新增 Topic。网页退出账号会在服务端撤销当前登录会话，并切回原游客身份；其他设备的登录保留。账号会话 30 天后到期，页面明确提示重新登录。停用账号、更换邀请码或管理员撤销会话会使相关旧登录失效；恢复账号不会复活旧会话。游客与账号的进度不自动合并。

初始批次为 10 个正式使用账号与 5 个固定标记的测试账号。测试标记不能从个人设置修改，公开验收只能使用测试账号。采用默认权限的账号会继承新增 Topic；采用明确 Topic 列表的账号只开放列表中的内容。

### 前置知识约束

Lesson 和 Review 均须等待所有上游 Topic 完成学习、完成到期或进行中的复习，并解除暂停。主页优先显示可学习任务；未解锁任务按未满足条件的直接前置 Topic 数量升序排列在后，可展开查看前置知识，但学习按钮禁用。直接打开学习入口也执行相同约束；已有内容仍可只读回看。待办任务用空方框、学习记录用勾选方框、未解锁任务用闭锁图标。任务卡中每项前置知识显示完成百分比和各模块最新熟练度的平均值，熟练度没有解锁门槛。独立图谱优先于课程目录中的依赖，详见 [课程图谱](docs/course-graph.md)。

## 管理解锁

先查看当前暂停对象：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' status --list-learners
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' status --learner '<guest-id 或 account-id>'
```

多 Topic 时用 `status --learner '<ID>' --topic '<Topic ID>'` 查看对应状态。按输出的 `pause.module_id` 与 `pause.event_id` 精确解锁：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' unlock --learner '<实际学习者 ID>' --topic digits-to-letters --module example-1 --pause-event '<实际暂停事件 ID>'
```

解锁新开当前未完成模块的一轮；保留旧作答、暂停记录、已完成评分和 Introduction 状态。若暂停时当前模块已经结算，解锁进入下一个未完成模块。页面没有解锁接口。

复习造成的 Topic 暂停（包括 Lesson 已经全部完成）使用本机命令查看准确事件后解锁：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' review-status --learner '<学习者 ID>' --topic digits-to-letters
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' review-unlock --learner '<学习者 ID>' --topic digits-to-letters --pause-event '<暂停事件 ID>'
```

解锁后旧轮次保留，新轮次从 Example 剩余队列继续取题、本轮错误数清零；长期的组内补题标记保留。游客重置某 Topic 时，其 Lesson 进度、复习记录和 Practice 队列一起清空；复习判题在途时须等判定结束再重置。

## 数据与备份

| 内容 | 位置 |
|---|---|
| 唯一活动源码 | `D:/Projects/MathLearningWeb` |
| 正式课程、答案表及导入来源索引 | 服务器 `/var/lib/graspmemoedu/courses` |
| 正式 Course 与 Topic 归属及图谱 | 服务器 `/var/lib/graspmemoedu/catalog.json`、`graphs` |
| 正式指南与附件 | 服务器 `/var/lib/graspmemoedu/subject-guides/<学科ID>`（兼容入口 `guide`） |
| 正式学习状态、事件、身份与账号权限 | 服务器 `/var/lib/graspmemoedu/learning.sqlite3` |
| 可读事件日志 | 服务器 `/var/lib/graspmemoedu/logs/events.jsonl` |
| 每日验证备份 | 服务器 `/var/backups/graspmemoedu` |
| 迁移回滚快照、未发布原稿与原邀请码清单 | 本机 `D:/AppData/MathLearningWeb`，不再是正式数据源 |
| 验收结果与截图 | `D:/Artifacts/MathLearningWeb` |
| 已确认需求档案 | `D:/Artifacts/MathLearningWebPlan` |

```sh
# 在服务器上执行，生成备份并验证可恢复性
sudo /usr/local/sbin/graspmemoedu-backup
```

备份包含一致的 SQLite 快照（含身份、权限、选课、时区和站内反馈）、目录内全部 Topic、原稿、独立答案/勘误表、指南及实际引用附件和 SHA-256 清单。新版 format 2 支持多课程迁移，仍可恢复原 format 1 备份。邀请码和身份凭据只保存哈希。恢复只接受**尚不存在的新目录**，校验后重建可读日志；原目录不会被覆盖。连接网页用的配对凭据不进入备份。可用 `Start-Learning.ps1 -DataDirectory 'D:\AppData\MathLearningWeb-Restored' -Port 8768` 检查当前课程的恢复结果。

课程版本变化时拒绝沿用旧进度，不会静默覆盖；当前不提供自动内容迁移。日志写入失败不丢数据库事件，下一次读取或启动时可恢复补写。

## 开发环境和课程导入

实际验收环境：Windows、PowerShell 7.6.5、Python 3.14.6、FastAPI 0.141.1、SQLite、Chrome 150.0.7871.129。前端为原生 HTML/CSS/JS，复用 MA 卡片与排版参数；509 个公式为离线 MathJax SVG。精确依赖版本在 [requirements.txt](requirements.txt)。

```powershell
python -m venv 'D:\AppData\MathLearningWeb\venv'
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m pip install --cache-dir 'D:\Caches\pip' -r 'D:\Projects\MathLearningWeb\requirements.txt'
```

导入在源码目录运行：

```powershell
node scripts/import_course.mjs 'D:/Artifacts/MathTopicPipeline/数字化为字母/数字化为字母-topic.md' 'D:/Artifacts/MathLearningWeb/development-data/courses/digits-to-letters.answers.json' 'D:/Artifacts/MathLearningWeb/development-data/courses/digits-to-letters.json' 'D:/Artifacts/MathLearningWeb/content-audit.json'
```

导入器依赖现有 `D:/Caches/MathTopicPdf/node_modules/mathjax-full` 和 Codex Node 运行库中的 marked。可用 `MATH_TOPIC_PDF_RUNTIME`、`CODEX_NODE_MODULES` 覆盖位置。原稿与独立答案表均保留在本机，未提交源码仓库。题面变化须重新准备并审核独立答案表，导入器拒绝旧哈希或未支持的运算结构。

GUIDE 复用这两个渲染依赖，并使用 sanitize-html 2.17.7 清理 HTML。依赖位于 D 盘缓存目录，可用 `MATH_LEARNING_NODE_MODULES` 覆盖：

```powershell
npm install --prefix 'D:\Caches\MathLearningWeb\node-runtime' --cache 'D:\Caches\npm' --save-exact --no-audit --no-fund sanitize-html@2.17.7
```

判题复用 `D:/Projects/MathAnswerJudge/math_answer_judge.py` 的既有接口与提示词，保持不思考模式。网站仅读取独立的 `MATH_JUDGE_API_KEY`、`MATH_JUDGE_BASE_URL`、`MATH_JUDGE_MODEL`，不再回退到 Codex 外包的密钥、模型或配置文件。正式 Linux 服务从 `/etc/graspmemoedu/judge.env` 加载变量，修改后须重启后端服务生效；本机开发环境中 Windows 用户环境变量优先于继承的进程变量，每次判题重新读取，保存修改后下一次提交即可生效；变量缺失时返回服务不可用，不借用外包配置。接口地址必须是 HTTPS，服务需兼容既有 Chat Completions/JSON 判题请求。只有当前题面、标准答案和学习者提交被发送给判题服务；网页和日志不包含 API 密钥。

## 验证

```powershell
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m unittest discover -s tests -v
node scripts/inspect_page.mjs
```

真实全流程复现需在**单独的、未开始的验收数据目录**启动服务，再运行 `node scripts/live_browser_acceptance.mjs <本机验收网址> <新结果目录> <课程JSON路径>`。该脚本真实调用判题，会消耗测试进度及 API 用量，不能对学习者正式进度运行。

公网流程使用 `Start-PublicLearning.ps1 -DataDirectory '<独立验收目录>' -Port 8768 -SkipPublish`，然后运行 `node scripts/verify_published_page.mjs <HTTPS页面> <HTTPS接口origin> <新结果目录> <课程JSON路径>`。该检查不模拟网络、不授予本地网络权限、不关闭浏览器安全限制。最后可加 `read-only`，仅新建独立游客、读取与刷新，不提交学习操作。

既有结果：[验收报告](D:/Artifacts/MathLearningWeb/验收报告.md)。公网初次接入记录位于 `D:/Artifacts/MathLearningWeb/public-internet`，主页与指南证据位于 `D:/Artifacts/MathLearningWeb/homepage`，账号测试证据位于 `D:/Artifacts/MathLearningWeb/accounts`。浏览器验收使用独立 Chrome 上下文，不修改用户浏览器记录。账号先在独立目录验收，再使用正式批次中保留的测试账号验证公网登录、跨设备进度、真实判题、权限与退出；不使用 10 个正式学习账号。测试框架仍提示 HTTPX 接口弃用警告，当前固定版本通过测试后继续使用。

## 打开公开页面

公开页面仅含 HTML、JS、CSS、图标、`.nojekyll` 和只含 `api_origin` 的 `deployment.json`。连接配置由发布时写入，网页没有配置入口。服务不可用时只显示提示和重试。游客身份在切换后端网址时会先到新后端验证，成功后迁移；连接错误不会清除旧身份或悄悄创建替代身份。

前端发布仓库为 `amymiller76717-sudo/GraspMemoEdu-Pages`，与私有源码仓库独立。正式服务只允许已配置网页来源及自身公开来源。`settings.json` 的 `public_api_origin` 指定精确公开 HTTPS 地址；nginx 保留公开 Host，后端据此识别公开请求。云端后端只监听 `127.0.0.1:18768`，由 nginx 提供公网 HTTPS。

修改前端后，先应用 `python scripts/version_web_assets.py --patch` 输出的补丁，为 HTML、CSS 和整套模块引用生成同一内容版本号；再导出到新的构建目录。脚本验证版本号和源码、导出一致，并检查远端完整文件清单，遇到额外文件会停止：

```powershell
python scripts/version_web_assets.py
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' --data-dir '<隔离构建数据目录>' export-web --output '<新的构建目录>' --api-origin 'https://124.221.144.155'
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Publish-Frontend.ps1' -ExportDirectory '<新的构建目录>'
```

发布回执的 `submitted` 仅表示已提交，不能作为上线验收。等待公开资源与导出哈希一致，并在真实浏览器中检查首页和受影响页面；同时验证已缓存页面能加载新版本。GitHub Pages 的 HTML 可缓存 10 分钟，刚发布的入口可附 `?v=<资源版本号>`。主题、标志及双语边界的唯一说明见 [学科配置](docs/subjects.md)。

公开后端校验精确 Host、来源和学习者身份，不再要求访客共享配对码。每位访客仍按身份隔离进度，Topic 和功能授权由后端执行；管理解锁与账号授权仅通过 SSH 在服务器命令入口执行。请求正文上限 16 KiB，游客创建每分钟最多 20 次、邀请码请求每分钟最多 10 次、新判题每位学习者每分钟最多 12 次；全局最多 8 个在途判题。达到限制返回 429，未接受的请求不计入作答。公开健康检查不返回本机实例标识，公开入口不提供本机会话或管理接口。

当前固定入口为腾讯云 IP HTTPS，已完成云端迁移；FSRS 复习调度已接入，XP 与管理员网页尚未实现。Cloudflare 临时隧道不再用于正式服务。

## 已停用的本机持续维护（历史）

迁移前使用 `Install-LearningUserStartup.ps1` 安装当前用户登录启动项。该启动项及维护进程现已停用，历史状态见 `D:/AppData/MathLearningWeb/runtime/maintenance`。正式运维以云端 systemd 服务和定时器为准。

旧维护机制中，本机进程存在但健康检查失败时会恢复后端；公网连续三次失败时重建归本项目管理的隧道。`Start-PublicLearning.ps1 -RepairOnly` 只通过 `Update-PublicEndpoint.ps1` 更新公开仓库的 `deployment.json`，不会把工作区中尚未发布的源码自动上线。网页在网络或网关错误时最多每 30 秒重新发现入口；身份经原有验证迁移，只重试读取，不重复提交答案。

安装命令：`pwsh -NoProfile -File D:/Projects/MathLearningWeb/Install-LearningUserStartup.ps1`。仅启动快捷方式位于 Windows 用户 Startup 目录，源码、数据与日志仍在 D 盘。当前非提升权限无法注册计划任务；`Install-LearningMaintenance.ps1` 保留为有权限环境的替代安装入口，两种方式择一。登录启动项方案适用于当前用户保持登录的运行环境，不承诺注销后或登录前可用，不修改电源或唤醒设置。维护进程本身被整体强制结束后，需要重新运行安装入口或下次登录才能恢复。

上述机制缩短故障恢复时间，不能保证 Cloudflare 或互联网故障期间零中断。维护重建及 GitHub Pages 更新需要时间；不要仅凭隧道进程仍存在或日志曾连接成功判断公网健康。
