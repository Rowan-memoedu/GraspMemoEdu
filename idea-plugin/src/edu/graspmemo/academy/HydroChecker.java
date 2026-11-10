package edu.graspmemo.academy;

import com.google.gson.*;
import com.intellij.openapi.application.PathManager;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.vfs.LocalFileSystem;
import com.jetbrains.edu.learning.checker.remote.RemoteTaskChecker;
import com.jetbrains.edu.learning.courseFormat.*;
import com.jetbrains.edu.learning.courseFormat.tasks.Task;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.concurrent.TimeUnit;

public final class HydroChecker implements RemoteTaskChecker {
    private CheckResult result(CheckStatus status, String message) {
        CheckResult value = new CheckResult(status, CheckResultSeverity.Info, null, java.util.Collections.emptyList());
        value.setMessage(message); return value;
    }
    private Path configPath() { return Path.of(PathManager.getConfigPath(), "graspmemoedu-hydro.json"); }
    private JsonObject binding(Project project, Task task, JsonObject cfg) {
        if (!task.getCourse().isStudy() || project.getBasePath() == null) return null;
        for (JsonElement item : cfg.getAsJsonArray("bindings")) {
            JsonObject b = item.getAsJsonObject();
            if (Path.of(b.get("project_root").getAsString()).toAbsolutePath().normalize().equals(Path.of(project.getBasePath()).toAbsolutePath().normalize())
                && b.get("task_path").getAsString().equals(task.getPathInCourse().replace('\\', '/'))) return b;
        }
        return null;
    }
    @Override public boolean canCheck(Project project, Task task) {
        try { return Files.isRegularFile(configPath()) && binding(project, task, BridgeCore.read(configPath())) != null; }
        catch (Exception error) { return false; }
    }
    @Override public CheckResult check(Project project, Task task, ProgressIndicator indicator) {
        Process process = null;
        try {
            JsonObject cfg = BridgeCore.read(configPath()), b = binding(project, task, cfg);
            if (b == null) return result(CheckStatus.Unchecked, "OJ 题目绑定不可用。");
            Path source = BridgeCore.within(BridgeCore.within(Path.of(project.getBasePath()), task.getPathInCourse()), b.get("source").getAsString());
            var vf = LocalFileSystem.getInstance().findFileByNioFile(source);
            var document = vf == null ? null : FileDocumentManager.getInstance().getCachedDocument(vf);
            String code = document == null ? Files.readString(source) : document.getText();
            JsonObject input = new JsonObject(); input.addProperty("project_root", project.getBasePath());
            input.addProperty("task_path", task.getPathInCourse().replace('\\', '/')); input.addProperty("code", code);
            indicator.setText("正在提交到 OJ 并等待判题…");
            process = new ProcessBuilder(cfg.get("node").getAsString(), cfg.get("helper").getAsString(), configPath().toString())
                .redirectError(ProcessBuilder.Redirect.DISCARD).start();
            final Process running = process;
            var outputFuture = java.util.concurrent.CompletableFuture.supplyAsync(() -> {
                try { return running.getInputStream().readNBytes(128 * 1024); }
                catch (java.io.IOException error) { throw new java.util.concurrent.CompletionException(error); }
            });
            try (var stream = process.getOutputStream()) { stream.write(BridgeCore.JSON.toJson(input).getBytes(StandardCharsets.UTF_8)); }
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(170);
            while (!process.waitFor(200, TimeUnit.MILLISECONDS)) {
                indicator.checkCanceled();
                if (System.nanoTime() > deadline) throw new java.io.IOException("OJ 等待超时；再次 Check 将查询原提交。");
            }
            String output = new String(outputFuture.get(5, TimeUnit.SECONDS), StandardCharsets.UTF_8);
            JsonObject r = JsonParser.parseString(output).getAsJsonObject();
            CheckStatus status = CheckStatus.valueOf(r.get("status").getAsString());
            var cases = new java.util.ArrayList<EduTestInfo>();
            if (r.has("cases")) for (JsonElement item : r.getAsJsonArray("cases")) {
                JsonObject c = item.getAsJsonObject(); int ojStatus = c.get("status").getAsInt();
                int nativeStatus = ojStatus == 1 ? 1 : (ojStatus == 0 || ojStatus >= 20 ? 2 : 6);
                cases.add(new EduTestInfo(c.get("name").getAsString(), nativeStatus, "OJ status: " + ojStatus, null, null, null));
            }
            CheckResult result = new CheckResult(status, CheckResultSeverity.Info, null, cases);
            result.setMessage(r.get("message").getAsString());
            if (r.has("details")) result.setDetails(r.get("details").getAsString());
            // Contains only verdict metadata and a code hash, never Cookie or learner code.
            JsonObject receipt = r.deepCopy(); receipt.addProperty("task_path", task.getPathInCourse());
            receipt.addProperty("code_hash", BridgeCore.hash(code.getBytes(StandardCharsets.UTF_8)));
            BridgeCore.write(Path.of(cfg.get("data_dir").getAsString(), "last-check.json"), receipt);
            return result;
        } catch (com.intellij.openapi.progress.ProcessCanceledException cancel) { throw cancel; }
        catch (Exception error) { return result(CheckStatus.Unchecked, "OJ 连接或检查未完成；请检查登录态后重试。当前进度未判为错误。"); }
        finally { if (process != null && process.isAlive()) process.destroyForcibly(); }
    }
}
