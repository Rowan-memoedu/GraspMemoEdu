# 题库中的原书正文

独立题库的 group 可含 `introductions: [{id,title,text,html}]`，并以 `content_order: [{type:"introduction"|"question",id}]` 明确覆盖每项一次、保持原书顺序。没有正文的 group 沿用原接口；只有正文的 group 和整本纯正文题库也合法。不新加单节点包装层，不生成习题或 Practice。

`scripts/import_training_bank.mjs` 用原编译器生成指导 HTML、离线公式和图片；`training_content.validate` 校验身份、正文、范围及顺序。`TrainingService.bank/group` 在原权限内只读投影，指导不进入作答、FSRS 或复习队列。前端沿用 `readerFrame`，侧栏按原顺序穿插指导与习题；指导阅读不创建作答轮次。正式 Topic 的 v2 Introduction、阅读记录和 Practice 门槛保持既有规范。

DOCX 提取、Codex Exec luna 来源清洗、编号答案附录、格式和打包发布在 `D:/Projects/GraspMemoEduSourcePrep/BOOK_WORKFLOW.md` 维护。本站仍只负责机械编译与呈现，不推导或审核原书答案。
