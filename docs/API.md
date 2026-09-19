# Implementation contract

Backend: FastAPI + SQLite; browser: plain HTML/CSS/JS using the supplied MA geometry. Course HTML includes offline MathJax SVG; no browser CDN or API secret. Source course and derived course JSON remain outside Git.

## Course schema

`{id, title, version, source_sha256, introduction:{id,title,html}, modules:[{id,title,example:{id,title,question,html,explanation_html,reference_answer,answer_type,constraints}, practices:[same question schema]}]}`.
`question` is plain source text including LaTeX. `constraints` is a nonempty string. The judge adapter appends constraints. Each module has 15 practices. The importer verifies all 96 answers independently of extracting the supplied explanations. Version is a stable hash of the entire imported content and answer metadata.

Default course: `D:/AppData/MathLearningWeb/courses/digits-to-letters.json`; id `digits-to-letters`; module ids `example-1` ... `example-6`; example question id `example-1-question`; practice id `example-1-practice-1` etc. Introduction id `introduction`.

The imported schema above is a **Topic**, retained for compatibility with the original importer. The separate `catalog.json` groups globally unique Topic IDs into Courses: `{courses:[{id,title,description,topics:[{id,path,prerequisites:[]}],units:[{id,title,topic_ids:[]}]}]}`. Topic paths resolve relative to the catalog. Units are optional, cover every Topic exactly once, and never create learning state. Prerequisite references must belong to the same Course and be acyclic. Without a catalog, the original `course_path` remains usable under `arithmetic-revisited` / `再探小学算术`.

## Browser API

All paths begin `/api`. GET `state` returns the state directly. POST `choose`, `continue`, `pause`, `select` return state directly. POST `submit` and GET `submissions/{submission_id}` return `{submission_id,status,correct,reason,state}`. Submission status: `pending`, `judged`, `error`, `discarded`. Errors are `{detail:{code,message}}` with appropriate HTTP status; 409 should refresh state. No learning API unlocks a pause.

Learning endpoints accept optional `?topic_id=<id>`; omission uses the default Topic for compatibility. Service caches are keyed by learner and Topic. Topic-specific drafts and replay payloads must not cross those scopes. Catalog/homepage reads do not initialize Topic progress.

The optional guest experience mode is an explicitly authorized exception to reference-answer secrecy: `GET /api/demo-answer?topic_id=...` returns `{step_id,question_id,answer}` only for a guest's current active question that can currently be submitted. Choice, historical review, pending judging, paused and completed states return 409; account roles return 403. The regular state response does not include reference answers. The checkbox defaults off, is scoped to guest identity, fills only an untouched answer box, and retains normal editable answers and backend judging. This is a convenience mode, not an assessment boundary against a guest deliberately calling that endpoint.

All learning requests send `X-Learning-Identity` with the browser's opaque identity token. Public HTTPS requests require the exact configured Host and an allowed Origin; local requests additionally use the session nonce. `POST access/guest` with `{}` creates an isolated guest and returns `{identity_token,access}`. `POST access/login` with `{invitation_code}` has the same response shape. `GET access` returns the current access object, or `401 identity_required/invalid_identity`. Only these identity errors permit replacement guest creation. Network, configuration, transport, and permission failures must retain existing identity and show retry.

`access` is `{role:'guest'|'account',learner_id,display_name,features:[...],topics:[{id,title}]}` and is also returned within every state. Guest capabilities are `learn`, `submit_answer`, `review_history`; accounts have an administrator-selected subset, optionally including `pause_topic`. Topic and feature grants are enforced on every API request. `choose/continue` require `learn`, `submit` requires `submit_answer`, `select` requires `review_history`, and `pause` requires `pause_topic`. Guest pause returns `403 feature_forbidden` with the exact message `该功能需注册账号才能使用`, without changing progress. Automatic fourth-error suspension remains part of learning rules.

Each identity maps to a distinct LearningService learner. Browser identity tokens may persist in localStorage scoped by API address; keep the guest token when switching to an invitation account. The SQLite database stores only token and invitation hashes in `access_` tables, included in the existing consistent backup. Invitations and permission management are CLI-only; no account administration is exposed through the public browser API. This release intentionally does not include account-login usage acceptance tests.

Mutation body: `request_id` (UUID), `expected_revision` (integer), `attempt_id`, `step_id`; choose adds `choice: mastered|unmastered`; submit adds `answer`; select uses target `step_id`. Pause needs only `request_id` and always freezes latest committed state. Retry a request with identical ID and payload. A changed answer after a service error uses a new request ID. Pending submission IDs are persisted in server state; poll once per second after refresh or interrupted response.

Submit also accepts optional integer `elapsed_ms` in 0..86400000, measured only while the current question accepts an answer and the page is visible. An identical retry retains that value. Omission preserves the legacy request fingerprint; migrated records remain NULL, not fabricated zeroes. Time is informational and does not change scoring.

State shape:

```json
{
  "topic_id":"digits-to-letters", "title":"数字化为字母", "course_version":"hash", "learner_id":"guest-opaque",
  "access":{"role":"guest","learner_id":"guest-opaque","display_name":"游客","features":["learn","submit_answer","review_history"],"topics":[{"id":"digits-to-letters","title":"数字化为字母"}]},
  "revision":1, "status":"in_progress", "introduction_read":false,
  "active_step_id":"opaque", "current_step_id":"opaque", "attempt_id":"uuid",
  "pause":null, "pending_submission_id":null,
  "modules":[{"id":"example-1","title":"Example 1：相邻分数和","status":"in_progress","mastery":null,"learning_path":null,"practice_error_count":0,"practice_answered_count":0,"practice_target_count":2}],
  "steps":[{"id":"opaque","kind":"example","title":"Example 1：相邻分数和","module_id":"example-1","attempt_id":"uuid","html":"<p>...</p>","explanation_html":null,"visited":true,"visible":true,"unlocked":true,"current":true,"active":true,"phase":"choice","feedback":null,"answer":null,"actions":["mastered","unmastered"],"question_id":"example-1-question"}]
}
```

Only visited content is returned in the state response; unvisited questions and reference answers stay on the backend, apart from the separate guest experience endpoint described above. Module summaries may include locked entries without their content. `kind`: example, introduction, practice, completion. `phase`: choice, answer, unmastered, feedback, reading, completed. `actions`: mastered, unmastered, submit, continue. Active step has available learning actions; historical review has no learning actions, even if it is current. All actions are empty while paused or waiting on judging. Previously visited content can still be selected. During Introduction insertion, current module steps are `visible:false`, their history remains selectable. The page primarily renders the selected step and history navigation; returning to active uses select with active_step_id. `feedback`: `{correct,reason,submission_id}`. Pause: `{event_id,reason,message,module_id,attempt_id}`.

No answer box before mastered; feedback never auto-advances. Unmastered requires Continue before Introduction/Explanation. The pause control stays visible while waiting for judge (except completed/paused Topic); guests receive the registration hint, and only accounts granted pause_topic may submit the action. A completed last module can be paused before Continue without revoking its score. On unlock, advance to the next unfinished module, or complete if none remains. History starts expanded on the left; previous/next use select only for already unlocked steps and sit at the bottom right alongside Continue.

## Homepage, profile, feedback and Guide

All `/api` endpoints below require the same identity and transport validation as learning requests.

| Endpoint | Contract |
|---|---|
| `GET /api/catalog` | `{courses:[{id,title,description,available,topic_count}],selected_course_id}` |
| `POST /api/catalog/select` | Body `{course_id}`; persists this identity's selection without changing any Topic state. |
| `GET /api/dashboard?course_id=...&cursor=...&limit=25` | `{course,units,topics,tasks,history,history_cursor,has_more}`; progress is 0–100. Cursor is scoped to learner and Course; limit 1–50. |
| `GET /api/tasks/{task_id}/answers` | Completed `lesson:<topic_id>` only, with `review_history`; `{task,groups:[{id,title,answers}],math_css}`. Only the learner's judged submissions and their question explanations are returned. |
| `GET/POST /api/profile` | Display name, IANA timezone and selected Course. POST may change only display_name/timezone, never permissions. |
| `POST /api/feedback` | `{message,course_id?,topic_id?,task_id?,question_id?}`; 1–4000 characters. Context must match accessible content; question feedback requires a visited question. Returns `{id,status:'saved',created_at}` only after commit. |
| `GET /api/guide` | `{status:'ready'|'empty'|'unavailable',html,math_css,version,modified_at,message?}` from local Markdown; source changes and resource byte changes update version. |

Tasks currently represent one Lesson per Topic. Status is `not_started`, `in_progress`, `paused` or `completed`; actual Example choice determines start, including old event records. Dashboard progress is weighted by module count. Estimated completion and missing metadata are null. Answers include `id,question_id,html,explanation_html,answer,correct,result,answered_at,elapsed_seconds,difficulty`; difficulty remains null until real data exists. Historical pagination never crosses identities or courses. Selection, timezone and feedback live in the same SQLite database as learning and are included in site backups. Feedback is capped at 5/minute/identity, enforced transactionally with 429 `feedback_rate_limited` and Retry-After. Feedback viewing is local CLI only.

The browser uses hash routes under any static-site base path, and completed task links use `#/learn?taskId=lesson%3A<topic-id>`. Guide checks run every 5 seconds while visible and preserve scroll; request/render failures are explicit. Sanitized Markdown supports tables/code, safe links and images, and MathJax SVG. Raw HTML is escaped; active TeX and invalid formulas reject the update. `/guide-assets/{relative:path}` is narrowly public because images do not carry identity headers: only explicitly referenced allowed files inside the Guide folder are accessible, never the source file, out-of-folder symlinks or arbitrary local files. Image files render inline; other allowed types download. The frontend does not expose local configuration or Markdown editing.

## Deployment and transport

The browser loads `deployment.json` with `cache:no-store`. An empty `api_origin` means local same-origin mode, which obtains a session nonce from GET `/api/session` and sends `X-Learning-Session`. A nonempty origin selects public mode even when the frontend and API share the same HTTPS origin; it sends no pairing credential and never calls `/api/session`. Invalid or unreachable deployment config must not fall back to localhost. The API serves deployment config dynamically for its exact local/public Host; Pages receives the fixed public origin at export.

Public mode requires `Settings.public_api_origin` to be an exact HTTPS origin without credentials, path, query, or wildcard. Forwarded host headers do not affect transport selection. Origin must be explicitly allowed; same-origin browser GET/HEAD may omit Origin only with `Sec-Fetch-Site: same-origin` and the exact public Host. POST still requires Origin. Public `/api/session` is forbidden, health exposes only status/service, and no administrator endpoints exist. Legacy CLI pairing remains available for compatibility but has no public webpage UI.

All API POST bodies are capped at 16384 actual streamed bytes, including chunked or underreported content. Public guest creation is globally capped at 20/minute, invitation requests at 10/minute, and accepted new submissions at 12/minute per learner. At most 8 submissions may be pending/running across public and local transports. Rejected submissions return 429 `rate_limited` or `judge_capacity` plus `Retry-After` before any learning-state mutation. Identical accepted replays do not consume another slot or another rate quota. These are process-local guardrails for the current single-process server.

## Python service for deterministic tests

`LearningService(course:dict, data_dir:Path, judge:callable|None=None, learner_id='local')` from `math_learning.service`. `state()`, `choose(payload)`, `advance(payload)`, `pause(payload)`, `select(payload)`, `submit(payload)` (reserve; no outbound call), `process_submission(id)` (perform judge), `submission(id)` and `unlock(topic_id,module_id,pause_event_id)`; `unlock` is local CLI only. `DomainError.code` and `.status_code`. Judge callable receives `(question, reference_answer, student_answer)` and returns a dict with `status`, `correct`, `reason`. `backup(path)` writes a consistent SQLite backup. The API schedules process_submission off the request thread; paused topics remain responsive.
