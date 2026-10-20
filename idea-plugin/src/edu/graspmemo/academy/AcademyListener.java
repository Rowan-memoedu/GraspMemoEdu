package edu.graspmemo.academy;

import com.intellij.openapi.project.Project;
import com.jetbrains.edu.learning.checker.CheckListener;
import com.jetbrains.edu.learning.courseFormat.CheckResult;
import com.jetbrains.edu.learning.courseFormat.tasks.Task;

public final class AcademyListener implements CheckListener {
    @Override public void beforeCheck(Project project, Task task) { BridgeService.instance().beforeCheck(project, task); }
    @Override public void afterCheck(Project project, Task task, CheckResult result) { BridgeService.instance().afterCheck(project, task, result); }
}
