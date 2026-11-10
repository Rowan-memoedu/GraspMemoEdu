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

/** Only the registered local-code binding changes Check; the existing listener owns scoring. */
public final class LocalCodeChecker implements RemoteTaskChecker {
    private Path configPath() { return Path.of(PathManager.getConfigPath(), "graspmemoedu-academy.json"); }
    private JsonObject binding(Project project, Task task, JsonObject config) throws Exception {
        if (!task.getCourse().isStudy() || project.getBasePath() == null) return null;
        JsonObject tasks = BridgeCore.read(Path.of(config.get("manifest").getAsString())).getAsJsonObject("tasks");
        for (var entry : tasks.entrySet()) {
            JsonObject value = entry.getValue().getAsJsonObject();
            if (value.has("code_judge") && Path.of(value.get("project_root").getAsString()).toRealPath()
                .equals(Path.of(project.getBasePath()).toRealPath())
                && value.get("task_path").getAsString().equals(task.getPathInCourse().replace('\\', '/'))) return value;
        }
        return null;
    }
    @Override public boolean canCheck(Project project, Task task) {
        try { return binding(project, task, BridgeCore.read(configPath())) != null; }
        catch (Exception error) { return false; }
    }
    private CheckResult result(CheckStatus status, String message, String details) {
        CheckResult value = new CheckResult(status, CheckResultSeverity.Info, null, java.util.Collections.emptyList());
        value.setMessage(message); value.setDetails(details); return value;
    }
    @Override public CheckResult check(Project project, Task task, ProgressIndicator indicator) {
        Process process = null;
        try {
            JsonObject config = BridgeCore.read(configPath()), local = binding(project, task, config);
            if (local == null) throw new java.io.IOException("Missing local task binding");
            JsonObject runtime = config.getAsJsonObject("code_checker");
            Path source = BridgeCore.within(BridgeCore.taskRoot(local), local.get("source").getAsString());
            var vf = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(source);
            var document = vf == null ? null : FileDocumentManager.getInstance().getCachedDocument(vf);
            String code = document == null ? Files.readString(source) : document.getText();
            if (code.length() > 256 * 1024) throw new java.io.IOException("Code is too large");
            JsonObject input = new JsonObject(); input.addProperty("project_root", project.getBasePath());
            input.addProperty("task_path", task.getPathInCourse().replace('\\', '/')); input.addProperty("code", code);
            indicator.setText("正在检查代码…");
            process = new ProcessBuilder(runtime.get("python").getAsString(), runtime.get("helper").getAsString(),
                "--config", configPath().toString()).redirectError(ProcessBuilder.Redirect.DISCARD).start();
            final Process running = process;
            var output = java.util.concurrent.CompletableFuture.supplyAsync(() -> {
                try { return running.getInputStream().readNBytes(512 * 1024); }
                catch (java.io.IOException error) { throw new java.util.concurrent.CompletionException(error); }
            });
            try (var stream = process.getOutputStream()) { stream.write(BridgeCore.JSON.toJson(input).getBytes(StandardCharsets.UTF_8)); }
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(200);
            while (!process.waitFor(200, TimeUnit.MILLISECONDS)) {
                indicator.checkCanceled();
                if (System.nanoTime() > deadline) throw new java.io.IOException("Local checker did not finish");
            }
            JsonObject checked = JsonParser.parseString(new String(output.get(5, TimeUnit.SECONDS), StandardCharsets.UTF_8)).getAsJsonObject();
            if (!local.get("code_judge").equals(checked.get("code_judge"))) throw new java.io.IOException("Test package changed");
            CheckStatus status = CheckStatus.valueOf(checked.get("status").getAsString());
            JsonObject receipt = checked.deepCopy(); receipt.addProperty("task_path", task.getPathInCourse());
            receipt.addProperty("code_sha256", BridgeCore.hash(code.getBytes(StandardCharsets.UTF_8)));
            BridgeCore.write(Path.of(config.get("data_dir").getAsString(), "last-local-check.json"), receipt);
            return result(status, checked.get("message").getAsString(), checked.get("details").getAsString());
        } catch (com.intellij.openapi.progress.ProcessCanceledException cancel) { throw cancel; }
        catch (Exception error) {
            return result(CheckStatus.Unchecked, "检查环境或测试包暂不可用；当前作答未判为错误。", "请更新练习连接后重试。");
        } finally {
            if (process != null && process.isAlive()) {
                process.toHandle().descendants().forEach(p -> p.destroyForcibly()); process.destroyForcibly();
            }
        }
    }
}
