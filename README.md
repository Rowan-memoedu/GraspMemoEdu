# MathLearningWeb

本机数学学习网站。已完成并验收逐块学习、真实 MathAnswerJudge 判题、练习补题、模块评分、暂停、管理解锁和进度恢复。当前导入「数字化为字母」：6 个 Example、每模块 15 道 Practice，合计 96 道题。

**当前状态（2026-09-19）：本机阶段通过验收。GitHub Pages 的静态前端与配对接口已准备，尚未公开发布，真实 GitHub HTTPS 页面访问本机服务的 Chrome 权限流程仍待发布后实测。**

## 打开与启动

网址：[http://127.0.0.1:8767](http://127.0.0.1:8767)。只在本机可用，判题需要联网。本机 8765 已由 AnkiConnect 使用，本项目使用 8767。

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Start-Learning.ps1'
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Stop-Learning.ps1'
```

启动入口后台运行，不打开额外窗口；重复启动复用同一实例。停止只处理入口记录且身份校验一致的学习服务进程树。重启后继续读取原进度；中断的判题可重试，不计错。启动失败的详细信息在 `D:/AppData/MathLearningWeb/runtime/server-error.log`。

开发时可在源码目录前台启动：

```powershell
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m math_learning serve
```

## 学习方式

1. Example 先选「已掌握」或「未掌握」。已掌握才出现答案框，支持分数、数值和等价表达式。
2. Example 答对立即得到熟练度 4，点击 Continue 进入下个模块。首次答错或选择未掌握，经 Continue 插入 Introduction，然后返回该题解析。
3. 练习初始 2 道；错误量 1、2、3 时总量扩为 3、5、7 道。完成后熟练度为 3、2、1；两道全对为 4。每题判定后必须点击 Continue。
4. 第 4 次练习错误暂停整个 Topic。页面「暂停学习」表示当前掌握不佳，需要管理者解锁；它与关闭页面休息不同。
5. 学习记录可回看已访问内容。刷新、多个窗口、重复点击都以数据库中的进度为准。

## 管理解锁

先查看当前暂停对象：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' status --learner local
```

按输出的 `pause.module_id` 与 `pause.event_id` 精确解锁：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' unlock --learner local --topic digits-to-letters --module example-1 --pause-event '<实际暂停事件 ID>'
```

解锁新开当前未完成模块的一轮；保留旧作答、暂停记录、已完成评分和 Introduction 状态。若暂停时当前模块已经结算，解锁进入下一个未完成模块。页面没有解锁接口。

## 数据与备份

| 内容 | 位置 |
|---|---|
| 唯一活动源码 | `D:/Projects/MathLearningWeb` |
| 课程、独立答案表、勘误及导入来源索引 | `D:/AppData/MathLearningWeb/courses` |
| 权威学习状态、作答与事件 | `D:/AppData/MathLearningWeb/learning.sqlite3` |
| 可读事件日志 | `D:/AppData/MathLearningWeb/logs/events.jsonl` |
| 默认备份 | `D:/AppData/MathLearningWeb/backups` |
| 验收结果与截图 | `D:/Artifacts/MathLearningWeb` |
| 已确认需求档案 | `D:/Artifacts/MathLearningWebPlan` |

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' backup
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' restore --backup '<备份 ZIP 的绝对路径>' --destination 'D:\AppData\MathLearningWeb-Restored'
```

备份包含一致的 SQLite 快照、结构化课程、原稿、独立答案/勘误表和 SHA-256 清单。恢复只接受**尚不存在的新目录**，校验后重建可读日志；原目录不会被覆盖。配对凭据不进入备份。可用 `Start-Learning.ps1 -DataDirectory 'D:\AppData\MathLearningWeb-Restored' -Port 8768` 检查恢复结果。

课程版本变化时拒绝沿用旧进度，不会静默覆盖；当前不提供自动内容迁移。日志写入失败不丢数据库事件，下一次读取或启动时可恢复补写。

## 开发环境和课程导入

实际验收环境：Windows、PowerShell 7.6.5、Python 3.14.6、FastAPI 0.141.1、SQLite、Chrome 150.0.7871.129。前端为原生 HTML/CSS/JS，复用 MA 卡片与排版参数；509 个公式为离线 MathJax SVG。精确依赖版本在 [requirements.txt](requirements.txt)。

```powershell
python -m venv 'D:\AppData\MathLearningWeb\venv'
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m pip install --cache-dir 'D:\Caches\pip' -r 'D:\Projects\MathLearningWeb\requirements.txt'
```

导入在源码目录运行：

```powershell
node scripts/import_course.mjs 'D:/Artifacts/MathTopicPipeline/数字化为字母/数字化为字母-topic.md' 'D:/AppData/MathLearningWeb/courses/digits-to-letters.answers.json' 'D:/AppData/MathLearningWeb/courses/digits-to-letters.json' 'D:/Artifacts/MathLearningWeb/content-audit.json'
```

导入器依赖现有 `D:/Caches/MathTopicPdf/node_modules/mathjax-full` 和 Codex Node 运行库中的 marked。可用 `MATH_TOPIC_PDF_RUNTIME`、`CODEX_NODE_MODULES` 覆盖位置。原稿与独立答案表均保留在本机，未提交源码仓库。题面变化须重新准备并审核独立答案表，导入器拒绝旧哈希或未支持的运算结构。

判题使用 `D:/Projects/MathAnswerJudge/math_answer_judge.py` 的既有接口和配置；没有改动其提示词、模型或思考模式。密钥继续从原有后端环境读取。只有当前题面、标准答案和学习者提交被发送给判题服务；网页和日志不包含 API 密钥。

## 验证

```powershell
& 'D:\AppData\MathLearningWeb\venv\Scripts\python.exe' -m unittest discover -s tests -v
node scripts/inspect_page.mjs
```

真实全流程复现需在**单独的、未开始的验收数据目录**启动服务，再运行 `node scripts/live_browser_acceptance.mjs <本机验收网址> <新结果目录> <课程JSON路径>`。该脚本真实调用判题，会消耗测试进度及 API 用量，不能对学习者正式进度运行。

详细结果：[本机验收报告](D:/Artifacts/MathLearningWeb/验收报告.md)。已通过 57 项规则/内容/API 测试、14 项隔离页面检查，以及真实课程中的 8 次判题和完整学习流程。测试框架仍提示 HTTPX 接口弃用警告；当前固定版本正常通过，不影响服务运行。

## GitHub Pages 准备状态

静态页面在 `D:/Artifacts/MathLearningWeb/pages-ready`，只有页面代码、样式、图标及 `.nojekyll`，不含课程、记录、数据库、标准答案或凭据。浏览器连接设置可指定回环地址，配对码只存当前标签页会话。

公开发布前需单独确认。当前 GitHub 账户为 Free；[GitHub 官方说明](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)要求 Free 账户的 Pages 来自公开仓库，预定使用单独公开的 Pages 发布仓库，源码主仓库保持私有。授权发布后才对实际页面来源执行 `Manage-Learning.ps1 pair --origin '<HTTPS origin，无路径>'`，重启本机学习服务并在网页输入本机配对码。不要把配对码写进公开页面或网址。

后端校验精确 Host、来源和凭据，CORS 预检仅开放已配置来源；普通页面不能自行建立本机会话或解除暂停。Chrome 的本地网络访问权限必须在真正的 HTTPS 页面上验证；不通过关闭浏览器安全功能来验收。参见 [Chrome 官方说明](https://developer.chrome.com/blog/local-network-access/) 和 [FastAPI CORS 文档](https://fastapi.tiangolo.com/tutorial/cors/)。

当前不提供学生公网访问、账号管理、复习调度、XP 或管理员网页。手机、其他电脑的 localhost 不会连接到本机。
