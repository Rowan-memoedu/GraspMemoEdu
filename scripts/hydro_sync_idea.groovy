import com.intellij.openapi.application.*
import com.intellij.openapi.project.*
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.command.WriteCommandAction
import com.intellij.openapi.vfs.LocalFileSystem
import com.intellij.ide.plugins.PluginManagerCore
import com.intellij.openapi.extensions.PluginId
import com.intellij.openapi.externalSystem.util.ExternalSystemUtil
import com.intellij.openapi.externalSystem.service.execution.ProgressExecutionMode
import com.intellij.ide.projectView.ProjectView
import com.google.gson.*
import java.nio.file.*
def data=Path.of('D:/AppData/GraspMemoEduHydro/sync'),r=[:]
try{
 def cfg=JsonParser.parseString(Files.readString(data.resolve('config.json'))).asJsonObject
 def input=JsonParser.parseString(Files.readString(data.resolve('current-input.json'))).asJsonObject
 def manifest=JsonParser.parseString(Files.readString(Path.of(cfg.get('manifest').asString))).asJsonObject.getAsJsonObject('tasks')
 def loader=PluginManagerCore.getPlugin(PluginId.getId('com.jetbrains.edu')).pluginClassLoader
 def p=ProjectManager.instance.openProjects.find{Path.of(it.basePath).toAbsolutePath().normalize()==Path.of(cfg.get('student').asString).toAbsolutePath().normalize()}
 if(p==null){r.deferred=true;r.ok=true}else{
  ApplicationManager.application.invokeAndWait({
   def c=p.getService(loader.loadClass('com.jetbrains.edu.learning.StudyTaskManager')).course;assert c!=null
   def checking=p.getService(loader.loadClass('com.jetbrains.edu.learning.actions.CheckAction$CheckActionState'))
   assert !checking.locked:'A learner Check is running; defer status synchronization'
   def lesson=c.getSection('OJ').items.find{it.name=='Practice'};assert lesson!=null
   def sync=loader.loadClass('com.jetbrains.edu.learning.yaml.YamlFormatSynchronizer').getField('INSTANCE').get(null)
   def store=loader.loadClass('com.jetbrains.edu.learning.storage.SQLiteLearningObjectsStorage').getField('Companion').get(null).openOrCreateDB(p)
   r.added=[];r.accepted=[];r.submitted=[]
   try{input.getAsJsonArray('tasks').each{entry->
    def e=entry.asJsonObject,rel=e.get('task_path').asString,name=rel.split('/')[-1]
    def task=lesson.taskList.find{it.name==name}
    if(task==null){
     def binding=manifest.entrySet().find{it.value.asJsonObject.get('task_path').asString==rel}.value.asJsonObject
     task=loader.loadClass('com.jetbrains.edu.learning.courseFormat.tasks.EduTask').getConstructor().newInstance()
     task.name=name;task.index=lesson.taskList.size()+1
     binding.getAsJsonArray('files').each{f->
      def file=f.asJsonObject,filename=file.get('path').asString
      def source=Files.readString(Path.of(p.basePath,rel,filename)).replace('\r\n','\n')
      task.addTaskFile(loader.loadClass('com.jetbrains.edu.learning.courseFormat.TaskFile').getConstructor(String,String,boolean).newInstance(filename,source,true))
      store.store(rel+'/'+filename,Base64.decoder.decode(file.get('template').asString))
     }
     task.descriptionText=Files.readString(Path.of(p.basePath,rel,'task.md'))
     lesson.addTask(task);task.init(lesson,false);r.added.add(e.get('pid').asString)
    }
    def state=e.getAsJsonObject('status'),accepted=state.get('accepted').asBoolean,submitted=state.get('submitted').asBoolean
    if(accepted){
     task.status=loader.loadClass('com.jetbrains.edu.learning.courseFormat.CheckStatus').enumConstants.find{it.toString()=='Solved'}
     task.feedback=null;r.accepted.add(e.get('pid').asString)
    }else if(submitted)r.submitted.add(e.get('pid').asString)
    def statusText='# OJ 同步状态\n\n'+(accepted?'OJ 已 AC':submitted?'OJ 已提交，尚未 AC':'OJ 尚无提交')+'\n\n本文件由定时同步更新；不修改练习源码或产生学习评分。\n'
    def file=Path.of(p.basePath,rel,'oj-status.md')
    def vf=LocalFileSystem.instance.refreshAndFindFileByNioFile(file)
    def doc=vf==null?null:FileDocumentManager.instance.getCachedDocument(vf)
    if(doc!=null){WriteCommandAction.runWriteCommandAction(p,{doc.setText(statusText)} as Runnable);FileDocumentManager.instance.saveDocument(doc)}else Files.writeString(file,statusText)
    if(!task.taskFiles.containsKey('oj-status.md'))task.addTaskFile(loader.loadClass('com.jetbrains.edu.learning.courseFormat.TaskFile').getConstructor(String,String,boolean).newInstance('oj-status.md',statusText,true))
    sync.saveItem(task,sync.mapper(c),'task-info.yaml')
   }}finally{store.dispose()}
   sync.saveItem(lesson,sync.mapper(c),'lesson-info.yaml')
   LocalFileSystem.instance.refreshAndFindFileByPath(p.basePath+'/OJ/Practice').refresh(false,true)
   def progress=loader.loadClass('com.jetbrains.edu.learning.projectView.ProgressUtil')
   progress.getMethod('updateCourseProgress',Project).invoke(progress.getField('INSTANCE').get(null),p)
   ProjectView.getInstance(p).refresh()
   if(!r.added.isEmpty()){
    def gradle=PluginManagerCore.getPlugin(PluginId.getId('org.jetbrains.plugins.gradle')).pluginClassLoader
    ExternalSystemUtil.refreshProject(p,gradle.loadClass('org.jetbrains.plugins.gradle.util.GradleConstants').getField('SYSTEM_ID').get(null),p.basePath,false,ProgressExecutionMode.IN_BACKGROUND_ASYNC)
   }
   r.ok=true
  } as Runnable,ModalityState.any())
 }
}catch(Throwable e){r.error=e.toString();r.ok=false;r.stack=e.stackTrace.take(5).collect{it.toString()}}
Files.writeString(data.resolve('native-receipt.json'),new GsonBuilder().setPrettyPrinting().create().toJson(r))
