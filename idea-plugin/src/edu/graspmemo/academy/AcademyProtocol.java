package edu.graspmemo.academy;

import com.intellij.openapi.application.JBProtocolCommand;
import java.util.Map;
import java.util.concurrent.*;

/** Registered with JetBrains' existing OS protocol handler. No custom shell launcher. */
public final class AcademyProtocol extends JBProtocolCommand {
    public AcademyProtocol() { super("graspmemoedu"); }
    @Override public Future<String> perform(String target, Map<String, String> parameters, String fragment) {
        String ticket = parameters.get("ticket");
        if (!"open".equals(target) || ticket == null || !ticket.matches("[A-Za-z0-9_-]{40,100}"))
            return CompletableFuture.completedFuture("无效的 GraspMemoEdu 练习链接。");
        return BridgeService.instance().open(ticket);
    }
}
