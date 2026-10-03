# 邀请码账号管理

正式账号由服务器管理命令创建。网页只提供登录、退出和学习功能，不开放建号、权限修改或解锁等管理接口。2026-09-21 起正式数据库位于腾讯云；下文 Windows 命令仅作隔离开发示例，不能针对本机回滚快照管理正式账号。正式调用方法见 [腾讯云运维](tencent-deployment.md)。

账号默认动态开放全部 Topic 和功能，后续新增内容会自动获得权限。Topic、功能分别支持明确覆盖：设置某一项的列表只覆盖该项，未设置的项继续继承默认值；空列表表示全部禁止。游客限制保持不变。`account-list` 的 `permission_defaults` 显示每项是否继承默认。

恢复默认权限（立即生效，不改变邀请码或学习记录）：

```powershell
& 'D:/AppData/GraspMemoEdu/venv/Scripts/python.exe' -m graspmemoedu account-permissions --learner '<learner_id>' --default-topics --default-features
```

`--topics` 与 `--default-topics` 互斥，`--features` 与 `--default-features` 互斥。显式列出当前全部 Topic 仍是固定列表；希望继承以后新增内容时使用默认模式。旧数据库没有记录是否曾显式设置，程序保守保留其列表；本次正式初始批次按原始建号记录核对后将未限制的默认权限迁入继承模式，真正不同的权限仍保留覆盖。

初始批次共 **15 个账号：10 个学习账号（`purpose=learner`）和 5 个测试账号（`purpose=test`）**。两类账号均默认获得全部当前及后续 Topic、功能；测试标记用于区分用途，不代表权限较低。真实验收只使用 5 个测试账号，不消耗学习账号的进度。

## 创建初始批次

在 PowerShell 中执行：

```powershell
Set-Location 'D:\Projects\GraspMemoEdu'
$python = 'D:\AppData\GraspMemoEdu\venv\Scripts\python.exe'
$data = 'D:\AppData\GraspMemoEdu'
& $python -m graspmemoedu.cli --data-dir $data account-provision `
  --batch initial-20260920 --learners 10 --tests 5 `
  --output "$data\admin\invitations-initial-20260920.json"
```

终端只显示文件路径、数量、批次和 `already_exists` 等结果，不显示邀请码。同一批次、同一参数和同一输出路径可重复执行；并发调用也只创建一批。已完成批次不会因邀请码后来被更换而再创建账号。新清单记录默认权限模式，新增 Topic 不影响默认批次的幂等性；人数、显式权限、权限模式或输出路径改变仍返回冲突。旧清单保留原快照校验，不自动改写。

JSON 是初始邀请码的唯一账本。它包含批次、数据库精确路径、生成时间，以及每个账号的 `learner_id`、`display_name`、`purpose`、`topic_ids`、`features` 和 `invitation_code`。账号初始名称为“学习者 01”等及“测试账号 01”等。按 `purpose=test` 筛选测试账号，勿依赖列表位置或猜测账号标识。同一私密目录的“邀请码清单.md”可以提供易读视图，但不能替代 JSON 作为批次重试输入；换码另存新的私密 JSON。

导出路径必须是对应数据目录 `admin` 内的绝对 `.json` 路径。禁止导出到网站、源码目录、相对路径或通过符号链接、目录联接跳出私密目录。写入邀请码前会验证权限：Windows 文件和目录仅当前用户与 SYSTEM 获得访问权限；Unix 文件为 `0600`，目录为 `0700`。权限设置或验证失败时不写邀请码、不创建账号。

## 分发与登录

- 一个邀请码对应一个账号，可重复登录，不是用完即失效的一次性口令。按人分配，按密码保管，不公开发布，也不要让多人共用。
- 同一个账号在不同设备登录会读取同一份学习进度。账号会话有效期为 30 天；到期后重新输入邀请码。
- 游客和账号进度保持独立。登录账号不会合并已有游客记录；退出后可以回到原浏览器的游客身份。
- 数据库只保存邀请码和会话凭据的哈希。私密 JSON 不应进入 Git、公开静态资产、应用日志或测试回执。通过可信渠道单独交付对应学习者的那一个邀请码。

## 查看、建号与权限

账号支持两个独立资格：普通／高阶学习者、学员／非学员。两项交叉组合，默认普通、非学员。现有学习功能仍按原权限规则开放；资格不替代明确的 Topic 或功能授权。管理员继续拥有全部功能。学员获得 `request_help`（请求答疑）；高阶学习者获得 `course_hierarchy`（学习页课程层级，默认开启，可在设置关闭），见 [课程层级](course-hierarchy.md)。

新功能在共享注册表 `access.FEATURE_REQUIREMENTS` 声明要求：`{'advanced_learner': True}`、`{'student': True}` 或 `{'advanced_learner': True, 'student': True}`。同时声明时须同时满足。服务端与前端返回的有效功能列表统一应用资格和账号功能授权。

在服务器将一个邀请码账号设为高阶或恢复普通资格：

```sh
cd /opt/graspmemoedu/app
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu account-permissions --learner '<learner_id>' --advanced-learner
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu account-permissions --learner '<learner_id>' --standard-learner
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu account-permissions --learner '<learner_id>' --student
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu account-permissions --learner '<learner_id>' --non-student
```

`--advanced-learner` 与 `--standard-learner` 互斥，`--student` 与 `--non-student` 互斥；两个维度可以组合，例如 `--advanced-learner --student`。可与现有权限选项一起使用；省略某维度时保留该维度原值。`account-list`、权限修改回执及登录身份均返回 `is_advanced_learner` 和 `is_student`。调整下一次请求生效，不需要换邀请码或清空进度；网页显示两个资格，不能修改它们。普通和高阶账号均正常记录进度与复习参数，资格随数据库备份恢复。

```powershell
& $python -m graspmemoedu.cli --data-dir $data account-list

& $python -m graspmemoedu.cli --data-dir $data account-create `
  --name '新的学习者' --purpose learner `
  --output "$data\admin\new-learner.json"

& $python -m graspmemoedu.cli --data-dir $data account-permissions `
  --learner '<account-list返回的learner_id>' `
  --topics 'digits-to-letters' --features 'learn,submit_answer,review_history'
```

`account-list` 返回用途、启用状态、所属批次和权限，不返回邀请码。`account-create` 支持 `--purpose learner|test`；省略 `--topics` 或 `--features` 时使用当前全部权限。单账号的同一私密输出文件也支持安全重试。

为了兼容原管理命令，`account-create` 省略 `--output` 时仍会在终端显示一次邀请码。日常管理应使用 `--output`，避免终端历史、共享屏幕或重定向日志收录邀请码。

## 停用、换码和注销

```powershell
& $python -m graspmemoedu.cli --data-dir $data account-disable --learner '<learner_id>'
& $python -m graspmemoedu.cli --data-dir $data account-enable --learner '<learner_id>'

& $python -m graspmemoedu.cli --data-dir $data account-rotate `
  --learner '<learner_id>' --output "$data\admin\rotation-20260920.json"

& $python -m graspmemoedu.cli --data-dir $data account-revoke-sessions --learner '<learner_id>'
```

停用会拒绝该账号登录并撤销全部会话，保留学习记录。重新启用不会恢复旧会话，学习者需要重新登录。

换码同时使旧邀请码和全部会话失效，保留账号与进度。新码必须先安全写入指定文件，随后数据库才提交；若导出或提交失败，旧码和原会话保持有效。重复同一个换码输出路径只确认原操作，不再次换码；下次有意换码时使用新的文件名。初始批次清单保持原样，其中已经换码的旧邀请码不能再用于登录。

`account-revoke-sessions` 注销该账号全部设备的会话，但不更换邀请码；学习者可以用原码重新登录。网页退出只处理该浏览器当前会话。

## 失败恢复与备份

完整清单会以排他方式新建，权限确认后写入并刷新到磁盘，然后在一个 SQLite 事务中创建账号和记录批次。任何账号插入或数据库提交失败，整批账号都会回滚；已完整写出的私密 JSON 会保留，使用相同命令重试即可复用原 ID 与邀请码，不会重复建号。

原文件不会被覆盖。重试会核对批次、数据库、名称、用途、权限、账号数量及 ID/邀请码唯一性；完成批次还会核对清单摘要。损坏、截断或属于其他操作的文件会被拒绝，不应手改清单绕过校验。如果进程在清单尚未完整写出时中断，数据库不会创建账号；确认该批次尚未完成后，可以使用新的私密输出路径重试，保留原文件供核查。

若数据库已记录批次完成，但原清单丢失，重复执行只会返回 `already_exists=true` 和 `manifest_available=false`，不会重建账号或伪造原码。需要恢复访问时，对精确账号执行换码并保存新的私密文件。

常规应用备份包含账号哈希、权限、会话与学习进度，**不能恢复原始邀请码**。`admin` 下的原始 JSON 清单应另做受访问控制的私密备份；数据库恢复目录变更后，不要把旧清单直接当作新目录的建号输入。

## 学员答疑

普通学员和高阶学员在学习、复习和可查看的历史内容框右上角均显示“请求答疑”，沿用内容反馈弹窗。补充说明可留空或只填空白，直接针对当前内容提交；内容位置、版本与正文快照仍保存，管理员看到“未填写补充说明”。取消不保存，失败保留输入，重复提交同一请求只保存一次。服务端同时核验学员资格、Topic 与功能权限、正文版本和内容位置。每个身份每分钟最多提交三条新请求。

请求保存在正式 SQLite 的 `help_requests` 表，随完整网站备份恢复；不进入学习状态、评分或 FSRS 推送。记录包括学习者身份与当时名称、提交时间、问题、课程、Topic、内容位置、版本及正文快照。邀请码和会话凭据不进入答疑记录。

转换题目按页面携带的不可变题库版本核验并保存该版本的正文；原题和指导按 Topic 原稿版本核验。切换当前题库不会使历史题目或已开始复习中的旧题库版本失效。无法读取、被修改或与原稿绑定不符的版本仍拒绝提交，不用新版正文替换旧版快照。

管理员从账号菜单打开“答疑请求”（`#/help-requests`），分页查看自己所负责学员的请求和正文快照，并可前往对应 Topic。服务端按学员与管理员的对应关系过滤记录、总数和分页；其他管理员不能借管理员身份、查询参数或分页读取这些请求。其他学习者不能读取自己或他人的请求。网页仍不提供建号、权限修改或解锁接口。

当前所有学员默认对应邀请码清单中的 1 号管理员，只有该管理员可以看到他们的答疑。以后新增的学员或升级为学员的账号也继承默认归属；可以通过服务器 CLI 单独调整，调整后已有和后续请求都跟随该学员的新归属，不改写请求内容或学习记录。未配置默认管理员或负责管理员被停用时，新提交返回明确错误，不能自动转给其他管理员。

```sh
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu help-admin-default --admin '<1号管理员 learner_id>'
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu help-assignments
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu help-assign --learner '<学员 learner_id>' --admin '<管理员 learner_id>'
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -m graspmemoedu --data-dir /var/lib/graspmemoedu help-assign --learner '<学员 learner_id>' --default-admin
```

对应关系保存在同一 SQLite 的 `help_admin_config` 与 `help_admin_assignments`，随网站备份恢复；不在网页开放修改接口。
