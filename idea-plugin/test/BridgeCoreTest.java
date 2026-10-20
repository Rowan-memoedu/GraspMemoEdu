package edu.graspmemo.academy;
import com.google.gson.*;
import java.nio.file.*;
import java.util.*;

public final class BridgeCoreTest {
    static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    public static void main(String[] args) throws Exception {
        Path data = Path.of(args[0]); Files.createDirectories(data);
        Path project = data.resolve("project"), task = project.resolve("Lesson/Task");
        Files.createDirectories(task.resolve("src"));
        Files.writeString(task.resolve("src/Main.java"), "original student solution");
        JsonObject binding = new JsonObject(); binding.addProperty("project_root", project.toString()); binding.addProperty("task_path", "Lesson/Task");
        JsonArray files = new JsonArray(); JsonObject file = new JsonObject(); file.addProperty("path", "src/Main.java"); files.add(file); binding.add("files", files);
        String before = BridgeCore.fingerprint(binding), round = UUID.randomUUID().toString();
        Path archive = BridgeCore.archive(data, "synthetic", round, binding);
        check(Files.readString(archive.resolve("src/Main.java")).equals("original student solution"), "exact archive");
        Files.writeString(task.resolve("src/Main.java"), "new code");
        check(!before.equals(BridgeCore.fingerprint(binding)), "code fingerprint");
        try { BridgeCore.archive(data, "synthetic", round, binding); throw new AssertionError("partial reset must stop"); }
        catch (java.io.IOException expected) {}
        BridgeCore.write(archive.resolve("complete.json"), new JsonObject());
        BridgeCore.archive(data, "synthetic", round, binding);
        check(Files.readString(archive.resolve("src/Main.java")).equals("original student solution"), "resume cannot overwrite archive");
        try { BridgeCore.within(task, "../../outside"); throw new AssertionError("path escape"); }
        catch (java.io.IOException expected) {}
        check(BridgeCore.category(false, false, "Compilation failed; see compiler error output").equals("compile"), "compile category");
        check(BridgeCore.category(false, false, "java.lang.ArrayIndexOutOfBoundsException").equals("runtime"), "runtime category");
        check(BridgeCore.category(false, false, "Time limit exceeded").equals("limit"), "limit category");
        check(BridgeCore.category(false, false, "Wrong answer").equals("wrong"), "wrong category");
        check(BridgeCore.category(false, false, "Could not download dependency").equals("environment"), "infra category");
        check(BridgeCore.category(true, false, "").equals("passed"), "passed category");
        JsonObject oldRound = new JsonObject(); oldRound.addProperty("round_id", UUID.randomUUID().toString());
        oldRound.addProperty("acked", 0); JsonArray pending = new JsonArray(); pending.add("unacknowledged result"); oldRound.add("events", pending);
        Path retained = BridgeCore.retainRound(data, "synthetic", oldRound);
        check(BridgeCore.read(retained).equals(oldRound), "unacknowledged evidence retained");
        check(BridgeCore.retainRound(data, "synthetic", oldRound).equals(retained), "retry is idempotent");
        oldRound.addProperty("acked", 1);
        try { BridgeCore.retainRound(data, "synthetic", oldRound); throw new AssertionError("retained evidence cannot change"); }
        catch (java.io.IOException expected) {}
        System.out.println("PASS 14 local archive, path, fingerprint, category and pending-result checks");
    }
}
