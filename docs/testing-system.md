# 复习与答疑测试系统

## 账号与访问

- 原五个测试账号保留邀请码，资格为“普通且非学员”，用于验证不可见范围。
- 新增高阶测试学员 01/02、测试管理员 01/02，均为 `purpose=test`。01、02 分别配对答疑和暂停管理，不修改正式学员的默认管理员。
- 邀请码只在本机私密管理目录 `D:/AppData/GraspMemoEdu/admin/邀请码清单.md` 及原始 JSON 账本保存，源码、报告、网页资产均不含邀请码。
- 只有高阶学员测试账号和管理员测试账号可见测试材料，必须同时满足测试用途与对应资格。正式高阶学员、正式管理员、普通测试账号、高阶非学员和游客均不可见。
- 管理员保留临时浏览模式，通过“全部卡片”、答疑、暂停管理及学员学习情况测试管理功能。学员账号测试真实持久学习与复习排期。

## 材料与预置场景

- 数学题库 `test-review-math`：34 题，包括 16 单选、6 多选、6 填空及 6 个其他流程样例。每名测试学员的前 28 题已预置为到期错题，五种卷型各可形成两份候选卷，同时提供单卡入口。
- 3 个源题共挂接 9 张预生成 Practice，覆盖单选、多选、填空。使用 MathTopicPipeline 的 Practice 提示词和原题/解析输入，校验后私密导入；不会创建新的用户排期。
- 保留原 ID 和版本恢复 `atomic-english/chinese/biology/chemistry`，分别预置到期、未开始、首次复习尚未到期、已暂停；同时恢复 `test-english-topic` 供范式内容和答疑测试。四科原子样例均为合成标签内容，不是正式学科教材。
- 原子题保留指导、组块、同组块 180 秒交错、初学答对不评分且 6 小时后首次复习、同点变式、多点打包、暂停及学员历史等原规则。
- 题库另有未开始的首次答对测试题、已经首次答对记 5 停推的题、手动停推的题，以及语义、判断、配对题。自评仍须在高阶设置中开启。
- 每对账号预置 12 条答疑，包含待回复、已回复未读、已读和回复修订后重新未读，可测试分页、正文快照与对应管理员隔离。已有答疑在场景重置时保留。

预置记录明确标注测试用途，其中原子初学记录是合成状态，不作为真实 AI 判题通过的证据。正式计时和 FSRS 规则不变；只有隔离验收服务可推进测试时钟。

## 建议测试顺序

1. 用原普通测试账号验证目录、直达链接和复习入口均不泄漏测试材料。
2. 用高阶测试学员打开数学复习：先看候选卷重叠，再完成一卷，观察原候选消失和剩余卷重组；默认 B 整卷提交。设置切换 A 后再验证逐题提交、部分完成和恢复。
3. “我的错题本”中的“测试变式”可直接验证不同设问；也可在客观题综合卷中遇到变式。从题面和解析请求答疑，管理员应看到对应变式的正文。
4. 英语复习测试原子单卡与多点打包；语文学习测试指导/组块/初学；生物查看未来排期；化学由配对管理员解除暂停，再由学员继续。
5. 学员进入答疑查看回复，进入可见范围后会自动标为已读，也保留手动入口；管理员回复或修订，学员应重新出现未读。每页 10 条，“加载更多”可继续查看。另一个管理员不能读取或回复本组请求。
6. 管理员打开全部卡片、学员学习情况、暂停管理及答疑正文；分别在桌面和手机宽度检查。

## 私密复现命令

新管理员测试账号可用 `account-create --purpose test --admin --output <admin内绝对JSON路径>` 创建；管理员必须私密导出，不能沿用终端显示邀请码的兼容路径。

变式安装：

```sh
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -B /opt/graspmemoedu/app/scripts/import_review_variants.py --data-dir /var/lib/graspmemoedu --input /var/lib/graspmemoedu/admin/testing-materials/practice-variants.json
```

场景清单位于私密数据目录 `admin/test-scenarios.json`，精确指定测试学员、测试 Topic 和题库。重置先验证全部对象、在 `admin/test-scenario-backups` 保存 SQLite 快照，拒绝正式账号、未标记内容、混入其他材料的试卷及在途判题。只重置清单中的测试学习状态，保留邀请码、资格、个人设置和答疑。后台保持停止直到预置结束；健康监测计时器必须同时停止，避免自动拉起服务。

```sh
sudo systemctl stop graspmemoedu-healthcheck.timer graspmemoedu
sudo -u graspmemoedu /opt/graspmemoedu/venv/bin/python -B /opt/graspmemoedu/app/scripts/reset_test_scenarios.py --data-dir /var/lib/graspmemoedu --plan /var/lib/graspmemoedu/admin/test-scenarios.json --apply
sudo systemctl start graspmemoedu
sudo systemctl start graspmemoedu-healthcheck.timer
```

省略 `--apply` 只校验。失败时保留错误和私密备份，不用整库回滚覆盖其他人的新记录。预置答疑使用 `scripts/seed_test_help.py`；同一请求键重复运行会跳过，不覆盖用户后续回复。

自动验证入口：`tests/test_test_accounts.py`、`test_test_materials.py`、`test_review_variants.py`、`test_test_scenarios.py`，以及隔离 `scripts/test_system_fixture.py` 和 `scripts/test_system_acceptance.mjs`。

## 上线验收

2026-10-05 已完成账号、材料与场景安装。回归测试共 702 项，700 项通过、2 项跳过；隔离 Chrome 验收通过 11 组流程，含真实语义判题、整卷/逐题、重叠卷失效、草稿恢复、原子打包、超时交卷、答疑读写及管理员归属隔离。

公网 API 和独立浏览器已验证两对新账号：每名学员保留 28 个到期题库记忆点、五种卷型各两卷、三项原子到期点及 12 条答疑；管理员能查看完整原子卡库、所属暂停和答疑，并可分页至第 12 条。游客和原普通测试账号无法通过目录或直达链接访问。公网验收未提交复习答案，预置排期留给用户测试。

发布前备份位于服务器 `/var/backups/graspmemoedu/test-materials-20261005T090925Z`；发布事务核对原有数据库行全部保留且值不变。本地安全回执、完整回归日志、浏览器截图及生成校验记录位于 `D:/Artifacts/GraspMemoEdu/testing-system-20261005`，邀请码单独留在私密管理目录。

同日根据用户补充收紧为“高阶学员测试账号或管理员测试账号”。完整回归共 722 项，720 项通过、2 项跳过；线上只读投影核验 33 个正式账号与 5 个普通测试账号均无测试材料权限，两名高阶测试学员及两名测试管理员继续可见，且九个测试账号的公网登录与内容访问符合预期。管理内容库、学员报告、暂停管理及答疑旧入口也已纳入该边界。此次仅发布六个后端文件，学习记录保持不变，无复习答案提交；证据在 `D:/Artifacts/GraspMemoEdu/test-account-visibility-20261005`，源码恢复包在服务器 `/var/backups/graspmemoedu/review-release-20261005T113856Z`。
