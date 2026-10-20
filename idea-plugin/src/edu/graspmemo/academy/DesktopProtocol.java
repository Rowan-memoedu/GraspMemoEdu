package edu.graspmemo.academy;

import com.intellij.ide.CliResult;
import com.intellij.ide.ProtocolHandler;
import com.intellij.openapi.application.JBProtocolHandler;
import kotlin.coroutines.Continuation;

/** Dedicated OS scheme; IDEA still dispatches through its native protocol command. */
public final class DesktopProtocol implements ProtocolHandler {
    @Override public String getScheme() { return "graspmemoedu"; }

    @Override public Object process(String query, Continuation<? super CliResult> continuation) {
        if (!query.matches("academy/open\\?ticket=[A-Za-z0-9_-]{40,100}"))
            return new CliResult(1, "无效的 GraspMemoEdu 练习链接。");
        return new JBProtocolHandler().process(
            "idea/graspmemoedu/open?ticket=" + query.substring("academy/open?ticket=".length()),
            continuation);
    }
}
