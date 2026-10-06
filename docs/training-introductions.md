# 题库中的原书正文

独立题库的 group 可含 `introductions: [{id,title,text,html}]`，并以 `content_order: [{type:"introduction"|"question",id}]` 明确覆盖每项一次、保持原书顺序。没有正文的 group 沿用原接口；只有正文的 group 和整本纯正文题库也合法。不新加单节点包装层，不生成习题或 Practice。

`scripts/import_training_bank.mjs` 用原编译器生成指导 HTML、离线公式和图片；`training_content.validate` 校验身份、正文、范围及顺序。`TrainingService.bank/group` 在原权限内只读投影，指导不进入作答、FSRS 或复习队列。只有 Introduction 的目录内容统一称为“引论”，含习题的内容保留“题组”。

进入题库后，Lesson 与题库共用 `web/learning-ui.js` 的阅读框架、正文和标题、侧栏分组、进度、翻页、选项和已提交答案、参考答案及解析显示；自评分按钮共用 `web/self-assessment.js`。页面控制器仅负责各自的状态、权限和提交，不另写正文阅读器。侧栏和翻页按原书 `content_order` 穿插指导与习题；指导阅读不创建作答轮次。正式 Topic 的 v2 Introduction、阅读记录和 Practice 门槛保持既有规范。

DOCX 提取、Codex Exec luna 来源清洗、编号答案附录、格式和打包发布在 `D:/Projects/GraspMemoEduSourcePrep/BOOK_WORKFLOW.md` 维护。本站仍只负责机械编译与呈现，不推导或审核原书答案。
