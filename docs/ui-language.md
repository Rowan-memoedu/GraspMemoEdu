# 界面语言规范

默认中文，可在设置切换英文。以下译法用于界面结构和操作，不改动作者的课程标题、正文、答案、解析及判题说明；首页学科双语副标题保留。旧格式的 `Introduction` 译为“指导”；Topic v2 保留各节原稿标题，纯引论的任务类型显示“引论”，内容更新任务显示“引论更新”。

| English | 中文 | 用途与区别 |
|---|---|---|
| Subject | 学科 | 数学、物理等学科入口 |
| Course | 课程 | 包含多个主题的课程 |
| Unit | 单元 | 课程内部的组织层级 |
| Topic | 主题 | 有独立学习进度的内容单元 |
| Lesson | 学习任务 | 学习主页的任务类型，不译成“课程”或单独一个“课” |
| Module | 模块 | 主题内部的学习模块 |
| Introduction | 指导 | 学习过程中出现的指导页面 |
| Guide | 指南 | 顶部导航中的网站使用指南 |
| Example | 例题 | 例题标签及结构性标题前缀 |
| Practice | 练习 | 练习题标签及结构性标题前缀 |
| Explanation | 解析 | 题目的讲解、解答 |
| Review | 复习 | 到期复习任务 |
| View history | 回看 | 查看已学内容，不表示开始一轮复习 |
| Learning history | 学习记录 | 历史学习步骤和作答记录 |
| Mastery | 熟练度 | 现有 1–4 分评分标签 |
| Prerequisites | 前置知识 | 学习所需的基础内容 |
| Continue learning / Resume | 继续学习 | 页面继续操作／恢复未完成任务 |
| Start | 开始学习 | 开始任务 |
| Previous / Next | 上一页／下一页 | 页面导航 |
| Submit | 提交 | 提交答案或反馈 |
| Pause learning | 暂时终止学习 | 保留用户指定按钮文案 |
| Paused | 已暂停 | 状态标签，与操作按钮区分 |
| Reset | 重置 | 清空指定主题进度后重新开始 |
| Full credit / Partial credit / No credit | 满分／部分得分／未得分 | 作答得分结果 |
| Sign in / Sign out | 登录／退出登录 | 统一采用 Sign in / Sign out |
| Invitation code | 邀请码 | 账号登录凭据 |
| Administrator | 管理员 | 管理权限与解锁的人员 |
| Settings / Help / Feedback | 设置／帮助／反馈 | 头像菜单 |
| Demo browsing | 体验专用浏览版 | 保留用户指定名称 |

题目难度的 Easy / Medium / Hard 对应“简单／中等／困难”；复习评分中的 Again / Hard / Good / Easy 对应“需再练习／较难／良好／轻松”。这两组属于不同使用场景，不强行用同一译法。

所有界面字符串集中在 `web/*-messages.js`，服务操作提示在 `web/i18n.js`。`translateMessage()` 只用于操作提示，不用于课程正文或判题解释；同一提示重复经过翻译时仍应保留原意。不要把未知提示误写成其他具体结果。

完整文案见 [完整界面中英对照表](ui-language-table.md)。它由 `node scripts/export_ui_language_table.mjs` 从实际词库生成，用 `--check` 检查是否一致。更新界面文案后须同步对照表、运行 `check_ui_i18n.mjs`，并按 README 更新资源版本、验证真实页面。
