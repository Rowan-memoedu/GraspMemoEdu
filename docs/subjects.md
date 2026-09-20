# GraspMemoEdu 学科配置与扩展

课程图谱可以独立于教学内容导入，见[独立课程图谱](course-graph.md)。尚无教学课程的学科也可显示其已导入图谱；这不改变 `course_ids`、课程数量、账号权限或学习进度。

总首页为 `#/`，六科固定标识为 `math`、`physics`、`english`、`chinese`、`biology`、`chemistry`。六科共用一个前端、账号系统和学习后端。数学只保留「再探小学算术」，不新增测试课；其余五科当前无已发布课程。原五门测试课程及六个测试 Topic（含物理作者导入测试）、配套测试指南均已下线，原文件和学习记录保留在本机。

古典衬线风格仅作用于总首页。数学及全局设置使用原模板视觉，其他科使用同一模板并替换学科色调、名称和徽标。学科导航栏最左侧的 GraspMemoEdu 品牌返回总首页，其右侧保留独立学科标识（数学为原来的 Σ 与「数学学习」）。平台返回入口的样式不覆盖学科主题。

六科共用组件与颜色变量，以 `web/subjects.js` 的 `mathPalette` 为基准，通过色调、饱和度和强调强度生成层次。英语紫色保持与数学相同的相对亮度；生物、语文强化强调色对比；物理使用明确指定的较暗橙色；化学使用黑白层次。浅背景、排版与交互结构共用，正确/错误和重置按钮等语义色保持原规则。

| 学科 | 主强调色 | 标志 | 主按钮文字 |
|---|---|---|---|
| 数学 | `#0577C7` | 原数学符号 | 白色 |
| 物理 | `#E67E00` | 单摆（P2） | 纯黑、700 字重 |
| 生物 | `#007531` | 细胞（B3） | 白色 |
| 英语 | `#9954CA` | Aa | 白色 |
| 语文 | `#C71E00` | 文 | 白色 |
| 化学 | `#111111` | 分子环（C2） | 白色 |

首页卡片与学科导航栏统一调用 `subjectLogo()`；导航栏的学科名称使用该科强调色。按钮文字不使用描边或阴影。物理黑字与主按钮底色的对比度约 7.35:1。标题深色、悬停色和背景色按共享主题表定义，不将全部组件压成同一强调色。资源版本与发布验收流程见 [README](../README.md#打开公开页面)。

首页顶部导航、选中下划线及头像文字使用默认黑色，不继承数学蓝色。首页保留双语学科副标题；学科页的导航、按钮、状态与结构标签跟随设置语言。术语按 [界面语言规范](ui-language.md) 统一，Introduction 译为“指导”。模块和题目标题中的结构前缀仅在显示时本地化，其后作者标题、正文、答案与解析不翻译，不修改课程文件。

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
      "course_ids": ["arithmetic-revisited"],
      "guide_path": "guide/guide.md"
    },
    {
      "id": "physics",
      "guide_path": "subjects/physics/guide.md"
    }
  ]
}
```

除 `id` 外的字段均可省略。没有列出的学科仍保留默认显示。`course_ids` 引用现有 `catalog.json` 中的 Course ID，每门课程只能属于一个学科，未显式分配的仍属于数学。错误的字段、未知课程、重复归属会阻止启动并给出配置错误。

旧 `accent`（六位十六进制颜色）和 `emblem`（短文本或符号）仍由后端兼容读取，但不控制固定六科的前端主题或标志。修改视觉应使用 `web/subjects.js` 的统一定义。指南路径必须是数据目录内的相对 Markdown 路径，使用 `/`，不接受绝对路径、`..` 或越界符号链接。配图相对于各自指南目录。未配置或尚无 Markdown 的学科显示空指南，不回退到数学内容。

修改 `subjects.json` 后，通过既有本机入口重新加载后端：

```powershell
pwsh -NoProfile -File D:/Projects/MathLearningWeb/Start-PublicLearning.ps1 -RestartBackend
```

指南正文、图片仍会在打开页面时约每 5 秒检查更新，无需重启。配置仅在本机修改，公开网页没有管理或连接配置入口。

## 路由、身份与兼容

学科内页面为 `#/subjects/<id>/learn`、`courses`、`guide`、`topic/<topic-id>`、`review/<topic-id>/<module-id>`。顶栏 GraspMemoEdu 返回总首页；独立学科标识返回该学科学习页。全局设置和帮助保留 `#/settings`、`#/help`。每张课程卡片可直接进入学习进度或打开知识图谱，复用已有页面与组件，不会改变当前选课；未获课程访问权限时相应入口禁用。

旧 `#/learn`、`#/courses`、`#/guide`、`#/topic/...` 和 `#/review/...` 会转入数学并原样保留查询参数。Topic ID、课程版本、学习记录、复习卡片和浏览器身份键不变。新增 `learner_subject_preferences` 保存每科选课；数学未产生新选择时读取旧选课记录，不改写旧行。

同一账号在全部学科通用，课程权限仍由 Topic 与功能决定。默认权限账号继承新增 Topic，明确列表权限保持原授权；单独调整学科归属不会修改账号权限。游客目前只能进入数学，其他学科的课程、指南和资源均在后端拒绝游客；省略学科参数也不能绕过非数学 Topic 权限。游客不能主动暂停 Topic，体验浏览偏好保持原行为。

`GET /api/subjects` 返回展示字段、课程数、`available` 与 `requires_account`，不包含本地文件路径。课程目录、选课、Dashboard、作答记录以及 Lesson/Review 请求接受可选 `subject_id`；显式学科与课程不匹配时拒绝请求。旧客户端不传学科时保留兼容行为，身份和权限校验依然生效。非数学指南的图片和附件经账号认证后转为临时 Blob URL，离开页面或退出登录后撤销；不会向外部链接传递账号凭据。

## 共用实现与后续范围

- `web/subjects.js` / `subjects.css`：平台首页、空学科页面、主题与路径。
- `web/portal.js`：六科共用的课程、任务、指南、账号设置入口。
- `math_learning/subjects.py`：本地配置、课程归属与公开字段白名单。
- 现有 Lesson、Practice、FSRS 和账号代码：各学科复用，保持原规则。

五科测试内容沿用现有 Example、Introduction、Practice 与复习机制，不引入专属模块、判题提示词或不同评分算法。当前不要在 `subjects.json` 中填写尚未支持的 `prompt`、`rating` 或插件字段。共用结构的后续修改直接作用于六科，无需复制页面。

## 测试课程初始化

正式站点测试内容已下线。以下初始化工具仅保留供独立验收使用，不应重新应用到正式数据目录；恢复发布须有新的用户要求。

`scripts/initialize_subject_test_courses.py` 从确定性 Markdown 和答案 sidecar 调用正式导入管线生成五科测试内容。每个 Topic 有两个 Example，每个模块有十五道 Practice，可验证练习补题、完成评分及 FSRS 复习。默认只预览，加 `--apply` 才安装：

```powershell
& D:/AppData/MathLearningWeb/venv/Scripts/python.exe scripts/initialize_subject_test_courses.py --data-dir D:/Artifacts/MathLearningWeb/isolated-subject-fixture/data
# 在本机服务停止期间应用；随后用正常入口启动。
& D:/AppData/MathLearningWeb/venv/Scripts/python.exe scripts/initialize_subject_test_courses.py --data-dir D:/Artifacts/MathLearningWeb/isolated-subject-fixture/data --apply
```

数学课程和既有文件保持不变；已有指南不覆盖，尚未填写的预留指南会加入测试说明。重复执行幂等，测试文件被人工改动或 ID 冲突时停止，不强行覆盖。所有配置在写入前验证，异常回滚本轮写入。课程内容、源稿、答案和导入回执位于本机 `test-content/<学科>`，不进入公开静态仓库。

完整备份继续使用格式 2，增加可选 `subjects.json` 和各学科指南。备份会转换本地指南路径为可移植相对路径，包含被正文引用的安全资源与全库偏好；还原仍兼容旧备份。

## 验证入口

`tests/test_subjects.py` 验证课程隔离、权限、旧进度与偏好保留、多指南资源和备份还原。`scripts/platform_fixture.py` / `platform_acceptance.mjs` 使用独立目录和实际 Google Chrome 验证六科页面、身份、旧链接、语言与手机布局。`check_subject_themes.mjs` 验证色值和对比度；`theme_language_acceptance.mjs` 验证中英文、共享标志、首页中性导航、按钮样式和手机布局，语言切换后须等待异步设置页完成加载。不得向正式账号写入验收进度。
