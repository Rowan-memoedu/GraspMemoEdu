package edu.graspmemo.academy;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.startup.StartupActivity;

public final class BridgeStartup implements StartupActivity.DumbAware {
    @Override public void runActivity(Project project) { BridgeService.instance().start(); }
}
