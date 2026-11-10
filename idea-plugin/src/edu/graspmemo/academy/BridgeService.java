package edu.graspmemo.academy;

import com.google.gson.*;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.*;
import com.intellij.openapi.command.WriteCommandAction;
import com.intellij.openapi.fileEditor.*;
import com.intellij.openapi.editor.Document;
import com.intellij.openapi.project.*;
import com.intellij.openapi.vfs.*;
import com.intellij.ide.impl.ProjectUtil;
import com.intellij.notification.*;
import com.jetbrains.edu.learning.StudyTaskManager;
import com.jetbrains.edu.learning.courseFormat.*;
import com.jetbrains.edu.learning.courseFormat.tasks.Task;
import com.jetbrains.edu.learning.navigation.NavigationUtils;
import com.jetbrains.edu.learning.taskToolWindow.ui.TaskToolWindowView;
import com.intellij.openapi.wm.ToolWindowManager;
import com.jetbrains.edu.learning.yaml.YamlFormatSynchronizer;
import java.io.IOException;
import java.net.URI;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;

public final class BridgeService implements Disposable {
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "GraspMemoEdu-Academy"); t.setDaemon(true); return t;
    });
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(15)).build();
    private record CheckCapture(String roundId, String codeHash, JsonObject codeJudge) {}
    private final Map<String, CheckCapture> checking = new ConcurrentHashMap<>();
    private JsonObject rounds = new JsonObject();
    private Path data;
    private boolean started;

    public static BridgeService instance() { return ApplicationManager.getApplication().getService(BridgeService.class); }
    private JsonObject config() throws IOException {
        return BridgeCore.read(Path.of(PathManager.getConfigPath(), "graspmemoedu-academy.json"));
    }
    private JsonObject bindings() throws IOException {
        return BridgeCore.read(Path.of(config().get("manifest").getAsString())).getAsJsonObject("tasks");
    }
    public synchronized void start() {
        if (started) return;
        try {
            JsonObject cfg = config();
            data = Path.of(cfg.get("data_dir").getAsString()).toAbsolutePath().normalize();
            Files.createDirectories(data);
            Path state = data.resolve("rounds.json");
            if (Files.exists(state)) rounds = BridgeCore.read(state);
            started = true;
            worker.scheduleWithFixedDelay(this::flush, 5, 15, TimeUnit.SECONDS);
        } catch (Exception error) { notifyFailure("读取连接配置失败：" + error.getMessage()); }
    }
    private void save() throws IOException { BridgeCore.write(data.resolve("rounds.json"), rounds); }
    private JsonObject request(String path, String ticket, JsonObject body) throws IOException, InterruptedException {
        String origin = config().get("api_origin").getAsString();
        URI base = URI.create(origin);
        if (!"https".equals(base.getScheme()) && !("http".equals(base.getScheme()) && "127.0.0.1".equals(base.getHost())))
            throw new IOException("Only HTTPS or an explicit loopback test server is supported");
        if (!base.getPath().isEmpty() || base.getRawQuery() != null || base.getUserInfo() != null) throw new IOException("Invalid API origin");
        HttpRequest req = HttpRequest.newBuilder(URI.create(origin + "/api/academy/" + path))
            .timeout(Duration.ofSeconds(25)).header("Origin", origin).header("Authorization", "Bearer " + ticket)
            .header("Content-Type", "application/json").POST(HttpRequest.BodyPublishers.ofString(BridgeCore.JSON.toJson(body))).build();
        HttpResponse<String> response = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 200) throw new IOException("连接返回 " + response.statusCode() + "；请回到网页刷新练习。结果已保留在本机。");
        return JsonParser.parseString(response.body()).getAsJsonObject();
    }
    public CompletableFuture<String> open(String ticket) {
        start();
        return CompletableFuture.supplyAsync(() -> {
            try {
                JsonObject remote = request("claim", ticket, new JsonObject());
                String key = remote.get("task_key").getAsString(), roundId = remote.get("round_id").getAsString();
                JsonObject local = bindings().getAsJsonObject(key);
                if (local == null || !remote.get("version").equals(local.get("version"))) throw new IOException("本机课程与网站版本不匹配，请更新课程连接配置。");
                if (remote.has("code_judge") != local.has("code_judge")
                    || remote.has("code_judge") && !remote.get("code_judge").equals(local.get("code_judge")))
                    throw new IOException("本机测试包与当前作答轮次不一致，未重置代码。");
                JsonObject run;
                synchronized (this) {
                    run = rounds.getAsJsonObject(key);
                    if (run == null || !roundId.equals(run.get("round_id").getAsString())) {
                        if (checking.containsKey(key)) throw new IOException("本题仍在检查，请等检查结束后再打开新一轮。");
                        if (run != null) {
                            BridgeCore.retainRound(data, key, run);
                            if (run.getAsJsonArray("events").size() > run.get("acked").getAsInt())
                                notifyFailure("上一轮的未同步结果已保留在本机。网页已创建新轮次，旧结果不会计入新轮次。");
                        }
                        run = new JsonObject(); run.addProperty("round_id", roundId); run.addProperty("acked", 0);
                        run.add("events", new JsonArray()); run.addProperty("closed", false); run.addProperty("prepared", false);
                        rounds.add(key, run);
                    }
                    run.addProperty("ticket", ticket); save();
                }
                final JsonObject current = run;
                CompletableFuture<Void> opened = new CompletableFuture<>();
                ApplicationManager.getApplication().invokeLater(() -> {
                    try {
                        Project project = findProject(local);
                        if (project == null) project = ProjectUtil.openOrImport(Path.of(local.get("project_root").getAsString()), null, true);
                        if (project == null) throw new IOException("IDEA 未能打开已登记的课程项目。");
                        Project selected = project;
                        // Academy loads the course asynchronously after the project opens.
                        awaitCourse(selected, local, key, current, opened, 0);
                    } catch (Exception error) { opened.completeExceptionally(error); }
                });
                opened.get(120, TimeUnit.SECONDS);
                JsonObject ack = new JsonObject(); ack.addProperty("kind", "opened");
                request("event", ticket, ack);
                synchronized (this) { current.addProperty("opened", true); save(); }
                return null;
            } catch (Exception error) {
                Throwable reason = error.getCause() == null ? error : error.getCause();
                notifyFailure(reason.getMessage()); return reason.getMessage();
            }
        }, worker);
    }
    private void awaitCourse(Project project, JsonObject local, String key, JsonObject run, CompletableFuture<Void> done, int tries) {
        try {
            if (project.isDisposed()) throw new IOException("课程项目已关闭。");
            Course course = StudyTaskManager.Companion.getInstance(project).getCourse();
            if (course == null) {
                if (tries >= 100) throw new IOException("JetBrains Academy 尚未完成课程加载。");
                CompletableFuture.delayedExecutor(500, TimeUnit.MILLISECONDS).execute(() -> ApplicationManager.getApplication().invokeLater(
                    () -> awaitCourse(project, local, key, run, done, tries + 1)));
                return;
            }
            if (!course.isStudy()) throw new IOException("只能打开学生版 Practice，不能重置作者课程。");
            String[] parts = local.get("task_path").getAsString().split("/");
            Lesson lesson = course.getLesson(parts.length == 3 ? parts[0] : null, parts[parts.length - 2]);
            Task task = lesson == null ? null : lesson.getTask(parts[parts.length - 1]);
            if (task == null) throw new IOException("Academy 未找到登记的 Task。");
            synchronized (this) {
                if (!run.get("prepared").getAsBoolean()) {
                    for (JsonElement e : local.getAsJsonArray("files")) {
                        Path file = BridgeCore.within(BridgeCore.taskRoot(local), e.getAsJsonObject().get("path").getAsString());
                        VirtualFile vf = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(file);
                        Document doc = vf == null ? null : FileDocumentManager.getInstance().getCachedDocument(vf);
                        if (doc != null) FileDocumentManager.getInstance().saveDocument(doc);
                    }
                    Path archive = BridgeCore.archive(data, key, run.get("round_id").getAsString(), local);
                    if (!Files.exists(archive.resolve("complete.json"))) {
                        restoreTemplates(project, task, local);
                        JsonObject receipt = new JsonObject(); receipt.addProperty("completed_at", Instant.now().toString());
                        BridgeCore.write(archive.resolve("complete.json"), receipt);
                    }
                    run.addProperty("prepared", true); save();
                }
            }
            NavigationUtils.INSTANCE.navigateToTask(project, task, null, true, false, null, true);
            if (local.getAsJsonArray("files").isEmpty()) {
                VirtualFile description = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(BridgeCore.taskRoot(local).resolve("task.md"));
                if (description != null) FileEditorManager.getInstance(project).openFile(description, true);
            }
            project.getService(TaskToolWindowView.class).updateTaskDescriptionTab(task);
            var toolWindow = ToolWindowManager.getInstance(project).getToolWindow("Task");
            if (toolWindow != null) toolWindow.show(null);
            JsonObject evidence = new JsonObject(); evidence.addProperty("task_key", key); evidence.addProperty("task_path", task.getPathInCourse());
            evidence.addProperty("project_root", project.getBasePath()); evidence.addProperty("round_id", run.get("round_id").getAsString());
            JsonArray editors = new JsonArray();
            for (VirtualFile f : FileEditorManager.getInstance(project).getOpenFiles()) editors.add(f.getPath());
            evidence.add("open_files", editors); BridgeCore.write(data.resolve("last-open.json"), evidence);
            done.complete(null);
        } catch (Exception error) { done.completeExceptionally(error); }
    }
    private void restoreTemplates(Project project, Task task, JsonObject binding) throws IOException {
        Path root = BridgeCore.taskRoot(binding);
        Map<String, Document> documents = new LinkedHashMap<>();
        for (JsonElement e : binding.getAsJsonArray("files")) {
            JsonObject f = e.getAsJsonObject(); String name = f.get("path").getAsString();
            byte[] bytes = Base64.getDecoder().decode(f.get("template").getAsString());
            if (!BridgeCore.hash(bytes).equals(f.get("sha256").getAsString())) throw new IOException("模板校验失败。");
            VirtualFile vf = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(BridgeCore.within(root, name));
            Document doc = vf == null ? null : FileDocumentManager.getInstance().getDocument(vf);
            if (doc == null) throw new IOException("不能恢复已登记的代码文件：" + name);
            TaskFile tf = task.getTaskFiles().get(name);
            if (tf == null || tf.getAnswerPlaceholders().size() != f.getAsJsonArray("placeholders").size())
                throw new IOException("本机题目占位区与模板不匹配，未重置代码：" + name);
            documents.put(name, doc);
        }
        WriteCommandAction.runWriteCommandAction(project, "GraspMemoEdu: 恢复练习模板", null, () -> {
            for (JsonElement e : binding.getAsJsonArray("files")) {
                JsonObject f = e.getAsJsonObject(); String name = f.get("path").getAsString();
                documents.get(name).setText(new String(Base64.getDecoder().decode(f.get("template").getAsString()), StandardCharsets.UTF_8));
                TaskFile tf = task.getTaskFiles().get(name);
                JsonArray placeholders = f.getAsJsonArray("placeholders");
                if (tf != null && tf.getAnswerPlaceholders().size() == placeholders.size()) {
                    for (int i = 0; i < placeholders.size(); i++) {
                        AnswerPlaceholder ph = tf.getAnswerPlaceholders().get(i); JsonObject p = placeholders.get(i).getAsJsonObject();
                        ph.setOffset(p.get("offset").getAsInt()); ph.setLength(p.get("length").getAsInt()); ph.setStatus(CheckStatus.Unchecked);
                    }
                }
            }
            task.setStatus(CheckStatus.Unchecked); task.setFeedback(null);
        });
        for (Document doc : documents.values()) FileDocumentManager.getInstance().saveDocument(doc);
        YamlFormatSynchronizer.INSTANCE.saveItem(task, YamlFormatSynchronizer.INSTANCE.mapper(task.getCourse()), "task-info.yaml");
    }
    private Project findProject(JsonObject binding) {
        Path root = Path.of(binding.get("project_root").getAsString()).toAbsolutePath().normalize();
        for (Project project : ProjectManager.getInstance().getOpenProjects())
            if (project.getBasePath() != null && root.equals(Path.of(project.getBasePath()).toAbsolutePath().normalize())) return project;
        return null;
    }
    private String matching(Project project, Task task) throws IOException {
        if (!task.getCourse().isStudy() || task.getCourse() instanceof EduCourse ec && ec.isPreview()) return null;
        for (var entry : bindings().entrySet()) {
            JsonObject value = entry.getValue().getAsJsonObject();
            if ("automated".equals(value.get("kind").getAsString()) && findProject(value) == project
                && task.getPathInCourse().replace('\\', '/').equals(value.get("task_path").getAsString())) return entry.getKey();
        }
        return null;
    }
    public void beforeCheck(Project project, Task task) {
        start();
        try {
            String key = matching(project, task);
            if (key == null) return;
            synchronized (this) {
                JsonObject run = rounds.getAsJsonObject(key);
                if (run == null || !run.get("prepared").getAsBoolean() || run.get("closed").getAsBoolean()) return;
                // Check captures the editor contents even if the normal IDE autosave has not run yet.
                JsonObject local = bindings().getAsJsonObject(key);
                for (JsonElement e : local.getAsJsonArray("files")) {
                    VirtualFile vf = LocalFileSystem.getInstance().findFileByNioFile(BridgeCore.within(BridgeCore.taskRoot(local), e.getAsJsonObject().get("path").getAsString()));
                    Document doc = vf == null ? null : FileDocumentManager.getInstance().getCachedDocument(vf);
                    if (doc != null) FileDocumentManager.getInstance().saveDocument(doc);
                }
                checking.put(key, new CheckCapture(run.get("round_id").getAsString(), BridgeCore.fingerprint(local),
                    local.has("code_judge") ? local.getAsJsonObject("code_judge").deepCopy() : null));
            }
        } catch (Exception error) { notifyFailure("未开始记录此次检查：" + error.getMessage()); }
    }
    public void afterCheck(Project project, Task task, CheckResult result) {
        try {
            String key = matching(project, task);
            CheckCapture capture = key == null ? null : checking.remove(key);
            if (capture == null) return;
            synchronized (this) {
                JsonObject run = rounds.getAsJsonObject(key);
                if (run.get("closed").getAsBoolean() || !capture.roundId().equals(run.get("round_id").getAsString())) return;
                JsonArray events = run.getAsJsonArray("events");
                JsonObject e = new JsonObject(); e.addProperty("id", UUID.randomUUID().toString());
                e.addProperty("sequence", events.size() + 1); e.addProperty("code_hash", capture.codeHash());
                e.addProperty("occurred_at", Instant.now().toString());
                e.addProperty("category", BridgeCore.category(result.getStatus() == CheckStatus.Solved,
                    result.getStatus() == CheckStatus.Unchecked, result.getFullMessage() + " " + result.getDetails()));
                if (capture.codeJudge() != null) {
                    JsonObject receipt = capture.codeJudge().deepCopy();
                    // Compiler/test details can contain learner source. Keep them in IDEA.
                    String message = Objects.toString(result.getMessage(), "");
                    receipt.addProperty("feedback", message.substring(0, Math.min(message.length(), 4000)));
                    e.add("local_code", receipt);
                }
                events.add(e); if (result.getStatus() == CheckStatus.Solved) run.addProperty("closed", true);
                save();
            }
            worker.execute(this::flush);
        } catch (Exception error) { notifyFailure("保存检查结果失败：" + error.getMessage()); }
    }
    private void flush() {
        if (!started) return;
        List<String> keys;
        synchronized (this) { keys = new ArrayList<>(rounds.keySet()); }
        for (String key : keys) {
            try {
                while (true) {
                    JsonObject run, body = new JsonObject(); int index; String ticket; boolean opened;
                    synchronized (this) {
                        run = rounds.getAsJsonObject(key);
                        if (!run.get("prepared").getAsBoolean()) break;
                        opened = run.has("opened") && run.get("opened").getAsBoolean();
                        index = run.get("acked").getAsInt(); ticket = run.get("ticket").getAsString();
                        if (opened && index >= run.getAsJsonArray("events").size()) break;
                        body.addProperty("kind", opened ? "check" : "opened");
                        if (opened) body.add("event", run.getAsJsonArray("events").get(index).deepCopy());
                    }
                    request("event", ticket, body);
                    synchronized (this) {
                        if (opened) run.addProperty("acked", index + 1); else run.addProperty("opened", true);
                        run.remove("sync_error"); save();
                    }
                }
            } catch (Exception error) {
                // One revoked round must not prevent other exercises from synchronising.
                try { JsonObject status = new JsonObject(); status.addProperty("task_key", key);
                    status.addProperty("error", error.getMessage()); status.addProperty("time", Instant.now().toString());
                    synchronized (this) { rounds.getAsJsonObject(key).add("sync_error", status); save(); }
                    BridgeCore.write(data.resolve("sync-status.json"), status); }
                catch (IOException ignored) { /* The next user action will show any storage failure. */ }
            }
        }
    }
    public void showArchive() {
        start();
        if (data != null) com.intellij.ide.BrowserUtil.browse(data.resolve("archives").toUri());
    }
    private static void notifyFailure(String message) {
        NotificationGroupManager.getInstance().getNotificationGroup("GraspMemoEdu Academy")
            .createNotification("GraspMemoEdu", message == null ? "练习连接失败，请从网页重试。" : message, NotificationType.WARNING).notify(null);
    }
    @Override public void dispose() { worker.shutdownNow(); }
}
