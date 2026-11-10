package edu.graspmemo.academy;

import com.google.gson.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;

/** Pure local operations, also exercised without launching an IDE. */
public final class BridgeCore {
    public static final Gson JSON = new GsonBuilder().setPrettyPrinting().create();
    public static JsonObject read(Path path) throws IOException {
        if (Files.size(path) > 32 * 1024 * 1024) throw new IOException("Local bridge file exceeds size limit");
        return JsonParser.parseString(Files.readString(path)).getAsJsonObject();
    }
    public static void write(Path path, JsonObject value) throws IOException {
        Files.createDirectories(path.getParent());
        Path pending = path.resolveSibling(path.getFileName() + ".pending");
        Files.writeString(pending, JSON.toJson(value), StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        Files.move(pending, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }
    public static String hash(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (Exception error) { throw new IllegalStateException(error); }
    }
    public static Path within(Path root, String relative) throws IOException {
        Path real = root.toRealPath(), candidate = real.resolve(relative).normalize();
        if (Path.of(relative).isAbsolute() || !candidate.startsWith(real) || candidate.equals(real))
            throw new IOException("File leaves the registered task");
        Path check = candidate;
        while (!Files.exists(check)) check = check.getParent();
        if (!check.toRealPath().startsWith(real)) throw new IOException("Task link leaves its registered root");
        return candidate;
    }
    public static Path taskRoot(JsonObject binding) throws IOException {
        return within(Path.of(binding.get("project_root").getAsString()), binding.get("task_path").getAsString());
    }
    public static String fingerprint(JsonObject binding) throws IOException {
        Path root = taskRoot(binding);
        StringBuilder value = new StringBuilder();
        for (JsonElement e : binding.getAsJsonArray("files")) {
            String name = e.getAsJsonObject().get("path").getAsString();
            Path file = within(root, name);
            value.append(name).append('\0').append(Files.exists(file) ? hash(Files.readAllBytes(file)) : "missing").append('\0');
        }
        return hash(value.toString().getBytes(StandardCharsets.UTF_8));
    }
    public static String category(boolean solved, boolean unchecked, String message) {
        if (solved) return "passed";
        String text = message.toLowerCase(Locale.ROOT);
        // Only positively identified infrastructure failures are excluded.
        if (unchecked || text.contains("could not resolve all files") || text.contains("could not download")
            || text.contains("no jdk specified") || text.contains("jdk is not configured")
            || text.contains("connection timed out") || text.contains("network is unreachable")) return "environment";
        if (text.contains("compilation failed") || text.contains("compile error") || text.contains("cannot find symbol")
            || text.contains("编译失败") || text.contains("编译错误") || text.contains("找不到符号")) return "compile";
        if (text.contains("time limit") || text.contains("memory limit") || text.contains("timed out")
            || text.contains("超时") || text.contains("内存限制")) return "limit";
        if (text.contains("答案未通过测试")) return "wrong";
        if (text.contains("exception in thread") || text.contains("runtime error") || text.contains("运行时异常")
            || text.matches("(?s).*(nullpointer|arithmetic|arrayindexoutofbounds|nosuchelement)exception.*")) return "runtime";
        return "wrong";
    }
    public static Path retainRound(Path data, String key, JsonObject round) throws IOException {
        String id = round.get("round_id").getAsString();
        if (!key.matches("[A-Za-z0-9_.-]{1,128}") || !id.matches("[a-f0-9-]{36}")) throw new IOException("Invalid round identity");
        Path path = data.resolve("round-history").resolve(key).resolve(id + ".json");
        if (Files.exists(path)) {
            if (!read(path).equals(round)) throw new IOException("Retained round differs; pending results were not replaced");
        } else write(path, round);
        return path;
    }
    public static Path archive(Path data, String key, String round, JsonObject binding) throws IOException {
        if (!key.matches("[A-Za-z0-9_.-]{1,128}") || !round.matches("[a-f0-9-]{36}")) throw new IOException("Invalid archive identity");
        Path destination = data.resolve("archives").resolve(key).resolve(round);
        if (Files.exists(destination.resolve("complete.json"))) return destination;
        if (Files.exists(destination)) throw new IOException("An interrupted reset exists. Restore its archive before continuing: " + destination);
        Files.createDirectories(destination);
        Path root = taskRoot(binding);
        JsonObject journal = new JsonObject();
        JsonArray files = new JsonArray();
        for (JsonElement e : binding.getAsJsonArray("files")) {
            String name = e.getAsJsonObject().get("path").getAsString();
            Path source = within(root, name), target = within(destination, name);
            JsonObject entry = new JsonObject(); entry.addProperty("path", name);
            entry.addProperty("existed", Files.exists(source));
            if (Files.exists(source)) {
                byte[] bytes = Files.readAllBytes(source);
                Files.createDirectories(target.getParent()); Files.write(target, bytes, StandardOpenOption.CREATE_NEW);
                entry.addProperty("sha256", hash(bytes));
            }
            files.add(entry);
        }
        Path yaml = root.resolve("task-info.yaml");
        if (Files.isRegularFile(yaml)) Files.copy(yaml, destination.resolve("task-info.yaml"));
        journal.add("files", files); journal.addProperty("task_key", key); journal.addProperty("round_id", round);
        write(destination.resolve("archive.json"), journal);
        return destination;
    }
}
