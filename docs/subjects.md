# GraspMemoEdu 学科配置与扩展

总首页为 `#/`，六科固定标识为 `math`、`physics`、`english`、`chinese`、`biology`、`chemistry`。六科共用一个前端、账号系统和学习后端。当前数学有「再探小学算术」；其余五科显示空课程与空指南，不生成示范 Topic。

## 本机配置

可选文件 `D:/AppData/MathLearningWeb/subjects.json` 用于覆盖学科显示信息、课程归属、各自指南。不存在时使用内置六科；全部既有课程默认属于数学，数学指南仍读取原有 `settings.guide_path`。

```json
{
  "subjects": [
    {
      "id": "math",
      "title": "数学",
      "title_en": "Mathematics",
      "description": "理解规律，建立数学直觉。",
      "description_en": "Understand patterns and build mathematical intuition.",
      "accent": "#785744",
      "emblem": "∑",
      "course_ids": ["arithmetic-revisited"],
      "guide_path": "guide/guide.md"
    },
    {
      "id": "physics",
      "accent": "#456781",
      "guide_path": "subjects/physics/guide.md"
    }
  ]
}
```

除 `id` 外的字段均可省略。没有列出的学科仍保留默认显示。`course_ids` 引用现有 `catalog.json` 中的 Course ID，每门课程只能属于一个学科，未显式分配的仍属于数学。错误的字段、未知课程、重复归属会阻止启动并给出配置错误。

`accent` 为六位十六进制颜色；`emblem` 为短文本或符号，不执行 HTML。指南路径必须是数据目录内的相对 Markdown 路径，使用 `/`，不接受绝对路径、`..` 或越界符号链接。配图相对于各自指南目录。未配置或尚无 Markdown 的学科显示空指南，不回退到数学内容。

修改 `subjects.json` 后，通过既有本机入口重新加载后端：

```powershell
pwsh -NoProfile -File D:/Projects/MathLearningWeb/Start-PublicLearning.ps1 -RestartBackend
```

指南正文、图片仍会在打开页面时约每 5 秒检查更新，无需重启。配置仅在本机修改，公开网页没有管理或连接配置入口。

## 路由、身份与兼容

学科内页面为 `#/subjects/<id>/learn`、`courses`、`guide`、`topic/<topic-id>`、`review/<topic-id>/<module-id>`。顶栏品牌返回总首页；学科名称返回该学科学习页。全局设置和帮助保留 `#/settings`、`#/help`。

旧 `#/learn`、`#/courses`、`#/guide`、`#/topic/...` 和 `#/review/...` 会转入数学并原样保留查询参数。Topic ID、课程版本、学习记录、复习卡片和浏览器身份键不变。新增 `learner_subject_preferences` 保存每科选课；数学未产生新选择时读取旧选课记录，不改写旧行。

同一账号及游客身份在全部学科通用，权限仍由 Topic 与功能决定。学科归属只组织内容，不授予学习权限。未配置内容的学科没有学习任务，切换学科也不会重置进度。游客仍只能被自动暂停，不能主动暂停 Topic；体验浏览偏好保持原行为。

`GET /api/subjects` 仅公开展示字段与课程数，不包含本地文件路径。课程目录、选课、Dashboard、作答记录以及 Lesson/Review 请求接受可选 `subject_id`；显式学科与课程不匹配时拒绝请求。旧客户端不传学科时保留兼容行为，身份和权限校验依然生效。

## 共用实现与后续范围

- `web/subjects.js` / `subjects.css`：平台首页、空学科页面、主题与路径。
- `web/portal.js`：六科共用的课程、任务、指南、账号设置入口。
- `math_learning/subjects.py`：本地配置、课程归属与公开字段白名单。
- 现有 Lesson、Practice、FSRS 和账号代码：各学科复用，保持原规则。

本轮没有加入其他学科的课程、专属模块、判题提示词或不同评分算法。学科专属功能后续按已确认需求接入；当前不要在 `subjects.json` 中填写尚未支持的 `prompt`、`rating` 或插件字段。共用结构的后续修改直接作用于六科，无需复制页面。

完整备份继续使用格式 2，增加可选 `subjects.json` 和各学科指南。备份会转换本地指南路径为可移植相对路径，包含被正文引用的安全资源与全库偏好；还原仍兼容旧备份。

## 验证入口

`tests/test_subjects.py` 验证课程隔离、权限、旧进度与偏好保留、多指南资源和备份还原。`scripts/platform_fixture.py` / `platform_acceptance.mjs` 使用独立目录和实际 Google Chrome 验证六科页面、身份、旧链接、语言与手机布局。不得向正式账号写入验收进度。
