package edu.graspmemo.academy;
import com.intellij.openapi.actionSystem.*;
public final class ArchiveAction extends AnAction {
    @Override public void actionPerformed(AnActionEvent event) { BridgeService.instance().showArchive(); }
}
