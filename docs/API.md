# Implementation contract

Backend: FastAPI + SQLite; browser: plain HTML/CSS/JS using the supplied MA geometry. Course HTML includes offline MathJax SVG; no browser CDN or API secret. Source course and derived course JSON remain outside Git.

## Course schema

`{id, title, version, source_sha256, introduction:{id,title,html}, modules:[{id,title,example:{id,title,question,html,explanation_html,reference_answer,answer_type,constraints}, practices:[same question schema]}]}`.
`question` is plain source text including LaTeX. `constraints` is a nonempty string. The judge adapter appends constraints. Each module has 15 practices. The importer verifies all 96 answers independently of extracting the supplied explanations. Version is a stable hash of the entire imported content and answer metadata.

Default course: `D:/AppData/MathLearningWeb/courses/digits-to-letters.json`; id `digits-to-letters`; module ids `example-1` ... `example-6`; example question id `example-1-question`; practice id `example-1-practice-1` etc. Introduction id `introduction`.

## Browser API

All paths begin `/api`. GET `state` returns the state directly. POST `choose`, `continue`, `pause`, `select` return state directly. POST `submit` and GET `submissions/{submission_id}` return `{submission_id,status,correct,reason,state}`. Submission status: `pending`, `judged`, `error`, `discarded`. Errors are `{detail:{code,message}}` with appropriate HTTP status; 409 should refresh state. No learning API unlocks a pause.

All learning requests additionally send `X-Learning-Identity` with the browser's opaque identity token. This is independent of the local session nonce or remote pairing credential; both layers are required. `POST access/guest` with `{}` creates an isolated guest and returns `{identity_token,access}`. `POST access/login` with `{invitation_code}` has the same response shape. `GET access` returns the current access object, or `401 identity_required/invalid_identity`. Only these identity errors should trigger replacement guest creation; transport authentication failures must retain the pairing prompt.

`access` is `{role:'guest'|'account',learner_id,display_name,features:[...],topics:[{id,title}]}` and is also returned within every state. Guest capabilities are `learn`, `submit_answer`, `review_history`; accounts have an administrator-selected subset, optionally including `pause_topic`. Topic and feature grants are enforced on every API request. `choose/continue` require `learn`, `submit` requires `submit_answer`, `select` requires `review_history`, and `pause` requires `pause_topic`. Guest pause returns `403 feature_forbidden` with the exact message `该功能需注册账号才能使用`, without changing progress. Automatic fourth-error suspension remains part of learning rules.

Each identity maps to a distinct LearningService learner. Browser identity tokens may persist in localStorage scoped by API address; keep the guest token when switching to an invitation account. The SQLite database stores only token and invitation hashes in `access_` tables, included in the existing consistent backup. Invitations and permission management are CLI-only; no account administration is exposed through the public browser API. This release intentionally does not include account-login usage acceptance tests.

Mutation body: `request_id` (UUID), `expected_revision` (integer), `attempt_id`, `step_id`; choose adds `choice: mastered|unmastered`; submit adds `answer`; select uses target `step_id`. Pause needs only `request_id` and always freezes latest committed state. Retry a request with identical ID and payload. A changed answer after a service error uses a new request ID. Pending submission IDs are persisted in server state; poll once per second after refresh or interrupted response.

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

Only visited content is returned; unvisited questions and reference answers stay on the backend. Module summaries may include locked entries without their content. `kind`: example, introduction, practice, completion. `phase`: choice, answer, unmastered, feedback, reading, completed. `actions`: mastered, unmastered, submit, continue. Active step has available learning actions; historical review has no learning actions, even if it is current. All actions are empty while paused or waiting on judging. Previously visited content can still be selected. During Introduction insertion, current module steps are `visible:false`, their history remains selectable. The page primarily renders the selected step and history navigation; returning to active uses select with active_step_id. `feedback`: `{correct,reason,submission_id}`. Pause: `{event_id,reason,message,module_id,attempt_id}`.

No answer box before mastered; feedback never auto-advances. Unmastered requires Continue before Introduction/Explanation. The pause control stays visible while waiting for judge (except completed/paused Topic); guests receive the registration hint, and only accounts granted pause_topic may submit the action. A completed last module can be paused before Continue without revoking its score. On unlock, advance to the next unfinished module, or complete if none remains. History starts expanded on the left; previous/next use select only for already unlocked steps and sit at the bottom right alongside Continue.

## Local pairing

Same-origin browser obtains a nonsecret session CSRF nonce from GET `/api/session`, then sends `X-Learning-Session`. This endpoint only accepts exact same origin or same-origin fetch metadata; scripts from another site cannot bootstrap it. A remote Pages frontend uses configurable loopback API URL and pairing credential, sends `Authorization: Bearer ...`; allowed remote origins are explicit backend config. Cross-origin callers never receive pairing credentials. Pairing credentials stay in sessionStorage only; API URL may use localStorage. Host and Origin checked server-side. No credentials embedded in static export.

## Python service for deterministic tests

`LearningService(course:dict, data_dir:Path, judge:callable|None=None, learner_id='local')` from `math_learning.service`. `state()`, `choose(payload)`, `advance(payload)`, `pause(payload)`, `select(payload)`, `submit(payload)` (reserve; no outbound call), `process_submission(id)` (perform judge), `submission(id)` and `unlock(topic_id,module_id,pause_event_id)`; `unlock` is local CLI only. `DomainError.code` and `.status_code`. Judge callable receives `(question, reference_answer, student_answer)` and returns a dict with `status`, `correct`, `reason`. `backup(path)` writes a consistent SQLite backup. The API schedules process_submission off the request thread; paused topics remain responsive.
