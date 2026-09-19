# MathLearningWeb

本机数学学习网站。已完成并验收逐块学习、真实 MathAnswerJudge 判题、练习补题、模块评分、暂停、管理解锁和进度恢复。当前导入「数字化为字母」：6 个 Example、每模块 15 道 Practice，合计 96 道题。

公开入口：[数学学习](https://amymiller76717-sudo.github.io/MathLearningWeb-Pages/)。其他电脑、手机可直接打开，自动进入游客模式，无需填写服务地址、配对码或授予本地网络权限。后端仍运行在当前电脑，通过免费 Cloudflare 临时隧道接入；课程、答案、学习记录和邀请码资料仍保存在本机，源码主仓库保持私有。

游客模式默认可学习、提交答案和回看内容；主动暂停会提示「该功能需注册账号才能使用」。邀请码账号和按账号开放 Topic、功能的框架已接入；本轮按要求只验收游客使用，不进行账号登录使用验收。

## 打开与启动

网址：[http://127.0.0.1:8767](http://127.0.0.1:8767)。只在本机可用，判题需要联网。本机 8765 已由 AnkiConnect 使用，本项目使用 8767。

公开服务使用下面的入口。它复用已有隧道；隧道失效后取得新地址、更新本机配置并发布到同一个学习网址。整个过程在本机完成：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Start-PublicLearning.ps1'
```

电脑须保持联网且不休眠。隧道工具位于 `D:/Applications/cloudflared/cloudflared.exe`；日志和运行记录位于 `D:/AppData/MathLearningWeb/runtime/tunnel`。仅停止公开入口可运行 `Stop-PublicLearning.ps1`，不会停止本机服务或删除记录。更新后端代码时，可使用 `Start-PublicLearning.ps1 -RestartBackend`。

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
4. 第 4 次练习错误暂停整个 Topic。具有对应权限的账号也可点击「暂时终止学习」，下方说明为「未掌握前置知识，感到困难，可选择后延该模块」。游客点击时提示注册，不会提交暂停请求；后端同样禁止游客主动暂停。自动暂停规则保持有效。
5. 学习记录默认在页面左侧展开，宽度约 309px，手机抽屉按屏幕宽度限制。右下角的 Submit、Continue、下一页共用一个位置，按当前状态显示；上一页、下一页仅回看已开启页面，没有对应页面时不显示按钮。
6. 游客进度按浏览器身份分开保存，刷新后恢复；切换账号时使用独立进度。清除浏览器本地存储会失去原游客入口，管理员仍可通过学习者 ID 查看本机记录。

## 邀请码与账号权限

管理员在本机管理入口创建账号并生成专属邀请码。邀请码只显示一次，数据库仅保存哈希；网页输入邀请码后取得独立登录身份。当前框架支持已导入 Topic 的白名单，当前可用 Topic 为 `digits-to-letters`。

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' account-list
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' account-create --name '<显示名>' --topics digits-to-letters --features 'learn,submit_answer,review_history,pause_topic'
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' account-permissions --learner '<account-id>' --topics digits-to-letters --features 'learn,submit_answer,review_history'
```

`account-create` 省略 Topic 和功能参数时默认全部开放；`account-permissions` 省略的字段保持原值，传空字符串可清空该项授权。权限在下一次请求时生效，由后端强制校验。

| 功能标识 | 开放内容 |
|---|---|
| `learn` | 选择掌握情况、Continue 推进 |
| `submit_answer` | 提交答案判题 |
| `review_history` | 学习记录、上一页和下一页 |
| `pause_topic` | 主动暂时终止学习 |

游客固定开放前三项及所有已配置 Topic。网页可从邀请码登录切回原游客身份，两个身份的进度独立。本轮没有创建实际账号，也没有执行邀请码登录验收。

## 管理解锁

先查看当前暂停对象：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' status --list-learners
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' status --learner '<guest-id 或 account-id>'
```

按输出的 `pause.module_id` 与 `pause.event_id` 精确解锁：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' unlock --learner '<实际学习者 ID>' --topic digits-to-letters --module example-1 --pause-event '<实际暂停事件 ID>'
```

解锁新开当前未完成模块的一轮；保留旧作答、暂停记录、已完成评分和 Introduction 状态。若暂停时当前模块已经结算，解锁进入下一个未完成模块。页面没有解锁接口。

## 数据与备份

| 内容 | 位置 |
|---|---|
| 唯一活动源码 | `D:/Projects/MathLearningWeb` |
| 课程、独立答案表、勘误及导入来源索引 | `D:/AppData/MathLearningWeb/courses` |
| 学习状态、作答、事件、身份与账号权限 | `D:/AppData/MathLearningWeb/learning.sqlite3` |
| 可读事件日志 | `D:/AppData/MathLearningWeb/logs/events.jsonl` |
| 默认备份 | `D:/AppData/MathLearningWeb/backups` |
| 验收结果与截图 | `D:/Artifacts/MathLearningWeb` |
| 已确认需求档案 | `D:/Artifacts/MathLearningWebPlan` |

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' backup
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' restore --backup '<备份 ZIP 的绝对路径>' --destination 'D:\AppData\MathLearningWeb-Restored'
```

备份包含一致的 SQLite 快照（含身份和权限表）、结构化课程、原稿、独立答案/勘误表和 SHA-256 清单。邀请码和身份凭据只保存哈希。恢复只接受**尚不存在的新目录**，校验后重建可读日志；原目录不会被覆盖。连接网页用的配对凭据不进入备份。可用 `Start-Learning.ps1 -DataDirectory 'D:\AppData\MathLearningWeb-Restored' -Port 8768` 检查恢复结果。

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

公网流程使用 `Start-PublicLearning.ps1 -DataDirectory '<独立验收目录>' -Port 8768 -SkipPublish`，然后运行 `node scripts/verify_published_page.mjs <HTTPS页面> <HTTPS接口origin> <新结果目录> <课程JSON路径>`。该检查不模拟网络、不授予本地网络权限、不关闭浏览器安全限制。最后可加 `read-only`，仅新建独立游客、读取与刷新，不提交学习操作。

既有结果：[验收报告](D:/Artifacts/MathLearningWeb/验收报告.md)。本次公网验收记录位于 `D:/Artifacts/MathLearningWeb/public-internet`。浏览器验收使用独立 Chrome 上下文，不修改用户浏览器的学习记录；真实判题仅在独立验收数据目录执行。账号只进行语法、命令帮助与代码检查，未创建或登录账号。测试框架仍提示 HTTPX 接口弃用警告；当前固定版本正常通过，不影响服务运行。

## 打开公开页面

公开页面仅含 HTML、JS、CSS、图标、`.nojekyll` 和只含 `api_origin` 的 `deployment.json`。连接配置由发布时写入，网页没有配置入口。服务不可用时只显示提示和重试。游客身份在切换后端网址时会先到新后端验证，成功后迁移；连接错误不会清除旧身份或悄悄创建替代身份。

前端发布仓库为 `amymiller76717-sudo/MathLearningWeb-Pages`，与私有源码仓库独立。正式服务只允许已配置网页来源及自身公开来源。`settings.json` 的 `public_api_origin` 指定精确公开 HTTPS 地址；隧道默认保留 Host，后端据此识别公开请求。本机仍只监听 `127.0.0.1`，不开放路由器或局域网入站端口。

发布更新时，先导出到新的构建目录，再传给发布脚本。脚本验证导出与源码一致、检查远端完整文件清单，遇到额外文件会停止：

```powershell
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Manage-Learning.ps1' export-web --output '<新的构建目录>' --api-origin '<本机配置中的公开HTTPS地址>'
pwsh -NoProfile -File 'D:\Projects\MathLearningWeb\Publish-Frontend.ps1' -ExportDirectory '<新的构建目录>'
```

公开后端校验精确 Host、来源和学习者身份，不再要求访客共享配对码。每位访客仍按身份隔离进度，Topic 和功能授权由后端执行；管理解锁与账号授权仅在本机命令入口。请求正文上限 16 KiB，游客创建每分钟最多 20 次、邀请码请求每分钟最多 10 次、新判题每位学习者每分钟最多 12 次；全局最多 8 个在途判题。达到限制返回 429，未接受的请求不计入作答。公开健康检查不返回本机实例标识，公开入口不提供本机会话或管理接口。

免费入口用于测试，无需 Cloudflare 账号或自有域名；临时隧道重建会更换后端地址，重新运行公开启动入口可更新网页配置。Cloudflare 不为 Quick Tunnel 提供可用性保证，后续可替换为独立服务器，保持前端学习网址。参见 [Cloudflare 官方 Quick Tunnel 说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/)。复习调度、XP、云服务器迁移和管理员网页不在本轮范围内。
