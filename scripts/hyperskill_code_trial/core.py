"""Isolated Java trial primitives. All generated code is untrusted."""
from __future__ import annotations
import hashlib,json,os,re,signal,subprocess,tempfile,time
from pathlib import Path

VERSION='code-trial-2'

def declares_main(template):
    # Debugging exercises can intentionally misspell modifiers or the return type.
    return bool(re.search(r'\bmain\s*\(',template))
def digest(value):
    if not isinstance(value,str):value=json.dumps(value,sort_keys=True,ensure_ascii=False)
    return hashlib.sha256(value.encode()).hexdigest()
def read(path,default=None):
    p=Path(path)
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else default
def save(path,data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    t=p.with_suffix(p.suffix+'.pending');t.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');t.replace(p)
def parse_output(raw):
    text=raw['output'].strip()
    if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
    obj=json.loads(text)
    if str(obj['id'])!=str(raw['id']):raise ValueError('Question identity mismatch')
    return obj
def normalize(text,kind='lines_rstrip'):
    text=text.replace('\r\n','\n').replace('\r','\n')
    if kind=='exact':return text.rstrip('\n')
    if kind=='lines_rstrip':return '\n'.join(line.rstrip(' \t') for line in text.split('\n')).rstrip('\n')
    if kind=='tokens':return text.split()
    if kind=='unordered_lines':return sorted(normalize(text).split('\n'))
    raise ValueError('Unknown comparison policy')

CHECKS=r'''import java.util.*;
import java.lang.reflect.Array;
import java.io.*;
import java.nio.charset.StandardCharsets;
final class TrialChecks {
    static int checks = 0;
    interface Action { void run() throws Exception; }
    static void check(boolean value, String label) { checks++; if (!value) throw new AssertionError(label); }
    static void eq(Object actual, Object expected, String label) { check(Objects.deepEquals(actual, expected), label + ": actual=" + render(actual) + ", expected=" + render(expected)); }
    static String render(Object x) { if(x == null) return "null"; if(x.getClass().isArray()) { List<Object> a=new ArrayList<>(); for(int i=0;i<Array.getLength(x);i++)a.add(Array.get(x,i)); return a.toString(); } return String.valueOf(x); }
    static Map<Object,Integer> bag(Object input) { List<Object> a=new ArrayList<>(); if(input==null)throw new AssertionError("null collection"); if(input instanceof Iterable<?> it){for(Object x:it)a.add(x);} else if(input.getClass().isArray()){for(int i=0;i<Array.getLength(input);i++)a.add(Array.get(input,i));} else throw new AssertionError("Expected array or Iterable"); Map<Object,Integer> counts=new HashMap<>();for(Object x:a)counts.merge(x,1,Integer::sum);return counts; }
    static void unorderedEq(Object actual,Object expected,String label) { eq(bag(actual),bag(expected),label); }
    static void throwsType(Class<? extends Throwable> type, Action action, String label) throws Exception { checks++;try{action.run();}catch(Throwable e){if(type.isInstance(e))return;throw new AssertionError(label+": unexpected "+e,e);}throw new AssertionError(label+": no exception"); }
    static String capture(Action action) throws Exception { PrintStream prior=System.out;ByteArrayOutputStream bytes=new ByteArrayOutputStream();try(PrintStream out=new PrintStream(bytes,true,StandardCharsets.UTF_8)){System.setOut(out);action.run();}finally{System.setOut(prior);}return bytes.toString(StandardCharsets.UTF_8); }
    static String output(String s) { return s.replace("\r\n","\n").replace('\r','\n').replaceAll("[ \\t]+(?=\\n|$)","").replaceAll("\\n+$",""); }
    static String methodName(String descriptor) { String s=descriptor.trim(); int paren=s.indexOf('(');if(paren>=0)s=s.substring(0,paren).trim();java.util.regex.Matcher m=java.util.regex.Pattern.compile("([A-Za-z_$][A-Za-z0-9_$]*)$").matcher(s);if(!m.find())throw new IllegalArgumentException("No method name in descriptor");return m.group(1); }
    static <S> void restores(java.util.function.Supplier<S> save,java.util.function.Consumer<S> restore,Action mutate,java.util.function.Supplier<?> observe,String label) throws Exception {S state=save.get();check(state!=null,label+": snapshot exists");Object before=observe.get();mutate.run();restore.accept(state);eq(observe.get(),before,label+": restore");mutate.run();restore.accept(state);eq(observe.get(),before,label+": snapshot remains independent");}
    static boolean methodHasAnnotation(String className,String methodName,String annotationName) throws Exception {
        javax.tools.JavaCompiler compiler=javax.tools.ToolProvider.getSystemJavaCompiler();
        try(javax.tools.StandardJavaFileManager manager=compiler.getStandardFileManager(null,null,StandardCharsets.UTF_8)) {
            Iterable<? extends javax.tools.JavaFileObject> units=manager.getJavaFileObjects(System.getProperty("trial.candidate.file"));
            com.sun.source.util.JavacTask task=(com.sun.source.util.JavacTask)compiler.getTask(null,manager,null,List.of("-proc:none"),null,units);
            boolean[] found={false};
            for(com.sun.source.tree.CompilationUnitTree unit:task.parse())new com.sun.source.util.TreeScanner<Void,Boolean>() {
                public Void visitClass(com.sun.source.tree.ClassTree tree,Boolean parent) {return super.visitClass(tree,tree.getSimpleName().contentEquals(className));}
                public Void visitMethod(com.sun.source.tree.MethodTree tree,Boolean matched) {if(Boolean.TRUE.equals(matched)&&tree.getName().contentEquals(methodName))for(var annotation:tree.getModifiers().getAnnotations()){String type=annotation.getAnnotationType().toString();if(type.equals(annotationName)||type.endsWith("."+annotationName))found[0]=true;}return super.visitMethod(tree,matched);}
            }.scan(unit,false);
            return found[0];
        }
    }
}
'''
DRIVER='''class TrialMain { public static void main(String[] args) throws Exception { TrialHarness.run(); if(TrialChecks.checks<1)throw new AssertionError("No assertions executed");System.out.println("HS_TRIAL_OK:"+TrialChecks.checks); } }'''

def validate_bundle(b):
    errors=[]
    if b.get('status')!='ready':return ['unsupported: '+str(b.get('reason',''))]
    if b.get('mode') not in ('io','harness'):errors.append('mode must be io or harness')
    if b.get('checker','lines_rstrip') not in ('exact','lines_rstrip','tokens','unordered_lines'):errors.append('unsupported comparison policy')
    if b.get('mode')=='io':
        if b.get('template_has_main') is False and not any(re.search(r'\bstatic\s+void\s+main\s*\(',f.get('code','')) for f in b.get('support_files',[])):
            errors.append('No native main or supplied IO driver; use a direct API harness')
        if not re.fullmatch(r'[\w.$]+',b.get('main_class') or ''):errors.append('explicit IO main class required')
        if not b.get('cases'):errors.append('IO cases missing')
        for case in b.get('cases',[]):
            if not isinstance(case.get('input'),str) or not isinstance(case.get('output'),str):errors.append('IO input/output must be strings')
            if not isinstance(case.get('argv',[]),list) or any(not isinstance(x,str) for x in case.get('argv',[])):errors.append('argv must be string array')
    if b.get('mode')=='harness':
        code=b.get('harness_code') or ''
        if not re.search(r'\bclass\s+TrialHarness\b',code) or not re.search(r'\bstatic\s+void\s+run\s*\(',code):errors.append('Harness must define static void TrialHarness.run()')
        if not re.search(r'TrialChecks\.(?:eq|check|unorderedEq|throwsType|restores)\s*\(',code):errors.append('Harness must call shared assertions')
        if 'HS_TRIAL_OK' in code or re.search(r'TrialChecks\.checks\s*(?:=|\+\+)',code):errors.append('Harness must not forge success or assertion counts')
        if re.search(r'(?m)^\s*java\.util\s+\w+',code):errors.append('Package name used as a type')
        if re.search(r'TrialChecks\.capture\s*\([^)]*::toString\s*\)',code):errors.append('toString returns a value; it does not print stdout')
        if b.get('fixed_to_string') and re.search(r'TrialChecks\.eq\([^;\n]*\.toString\(\)\s*,\s*"',code):errors.append('Use relational observations for fixed template toString; do not invent its display literal')
    names=set()
    for f in b.get('support_files',[]):
        n=f.get('name','')
        if not re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*\.java',n) or n in names or n in ('TrialChecks.java','TrialMain.java','TrialHarness.java'):errors.append('Invalid/duplicate/reserved support file')
        names.add(n)
    return errors

def source_filename(code):
    m=re.search(r'\bpublic\s+(?:(?:abstract|final|sealed|non-sealed)\s+)*(?:class|interface|enum|record)\s+(\w+)',code)
    return (m.group(1) if m else 'Candidate')+'.java'
def derive_fixture_sources(question):
    """Recover exact no-argument generic method fixtures from displayed declarations."""
    methods=[]
    for sample in question.get('existing_samples',[]):
        m=re.fullmatch(r'\s*(?:public\s+)?(<\s*[A-Za-z]\w*(?:\s*,\s*[A-Za-z]\w*)*\s*>\s+)?void\s+([A-Za-z_$]\w*)\s*(?:\(\s*\))?\s*',sample[0])
        if not m:return []
        methods.append('public '+(m.group(1) or '')+'void '+m.group(2)+'() {}')
    if not methods:return []
    template=question.get('template','');defined=set(re.findall(r'\b(?:class|interface|enum|record)\s+(\w+)',template))
    params=re.findall(r'\(\s*([A-Z]\w*)\s+\w+\s*,\s*String\s+\w+',template)
    missing=set(params)-defined-{'Object','Class','String','Integer','Long'}
    if len(missing)!=1:return []
    name=missing.pop();return [{'name':name+'.java','code':'class '+name+' {\n'+'\n'.join(methods)+'\n}'}]
def _limits():
    import resource
    os.setsid();resource.setrlimit(resource.RLIMIT_CPU,(25,27));resource.setrlimit(resource.RLIMIT_FSIZE,(1048576,1048576));resource.setrlimit(resource.RLIMIT_NOFILE,(256,256))
def sandbox(work,command,jdk,input_text='',timeout=20):
    bwrap=['bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL','--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--ro-bind',jdk,'/jdk','--bind',str(work),'/work','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--chdir','/work','--clearenv','--setenv','PATH','/jdk/bin:/usr/bin:/bin','--setenv','HOME','/tmp','--setenv','LANG','C.UTF-8',*command]
    cmd=['systemd-run','--user','--scope','--quiet','--collect','-p','MemoryMax=768M','-p','TasksMax=64','-p','CPUQuota=100%',*bwrap]
    start=time.monotonic()
    with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=out,stderr=err,preexec_fn=_limits);timed=False
        try:proc.communicate(input_text.encode(),timeout=timeout)
        except subprocess.TimeoutExpired:timed=True;os.killpg(proc.pid,signal.SIGKILL);proc.wait(timeout=10)
        out.seek(0);err.seek(0);stdout=out.read(131073);stderr=err.read(131073)
    return {'returncode':proc.returncode,'timed_out':timed,'elapsed_ms':round((time.monotonic()-start)*1000),'stdout':stdout[:131072].decode(errors='replace'),'stderr':stderr[:131072].decode(errors='replace'),'output_truncated':len(stdout)>131072 or len(stderr)>131072}
JAVA=['/jdk/bin/java','-Xmx256m','-Xss512k','-XX:+UseSerialGC','-XX:MaxMetaspaceSize=128m','-XX:ReservedCodeCacheSize=64m','-XX:CompressedClassSpaceSize=64m','-Dfile.encoding=UTF-8','-cp','/work/out']
def compile_run(code,bundle,jdk,compile_only=False,stop_on_failure=False):
    errors=validate_bundle(bundle)
    if errors:return {'verdict':'test_error','errors':errors,'tests':[]}
    build=Path('/var/tmp/hyperskill-code-trial-build');build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='job-',dir=build) as folder:
        work=Path(folder);name=source_filename(code);sources={name:code,'TrialChecks.java':CHECKS}
        for f in bundle.get('support_files',[]):
            if f['name'] in sources:return {'verdict':'test_error','errors':['Support source duplicates candidate'],'tests':[]}
            sources[f['name']]=f['code']
        if bundle['mode']=='harness':sources.update({'TrialHarness.java':bundle['harness_code'],'TrialMain.java':DRIVER})
        for n,text in sources.items():(work/n).write_text(text,encoding='utf-8')
        (work/'out').mkdir()
        compiled=sandbox(work,['/jdk/bin/javac','-J-Xmx256m','-J-XX:+UseSerialGC','-proc:none','-encoding','UTF-8','-d','/work/out',*sources],jdk,timeout=35)
        if compiled['returncode'] or compiled['timed_out']:
            technical=any(n+':' in compiled['stderr'] for n in sources if n!=name)
            return {'verdict':'test_error' if technical else 'compile_error','compile':compiled,'tests':[]}
        if compile_only:return {'verdict':'compiled','compile':compiled,'tests':[]}
        runs=[]
        cases=bundle.get('cases',[]) if bundle['mode']=='io' else [{'input':'','argv':[]}]
        for i,case in enumerate(cases):
            main=bundle['main_class'] if bundle['mode']=='io' else 'TrialMain'
            r=sandbox(work,[*JAVA,'-Dtrial.candidate.file='+name,main,*case.get('argv',[])],jdk,case.get('input',''),timeout=15)
            ok=r['returncode']==0 and not r['timed_out'] and not r['output_truncated']
            if bundle['mode']=='io':ok=ok and normalize(r['stdout'],bundle.get('checker','lines_rstrip'))==normalize(case['output'],bundle.get('checker','lines_rstrip'))
            else:ok=ok and bool(re.search(r'(?m)^HS_TRIAL_OK:[1-9]\d*\s*$',r['stdout']))
            runs.append({'case_index':i,'passed':ok,**r})
            if stop_on_failure and not ok:break
        failed=next((r for r in runs if not r['passed']),None)
        verdict='accepted' if failed is None else ('wrong_answer' if failed['returncode']==0 else 'runtime_error')
        if failed and re.search(r'Could not find or load main class|No assertions executed',failed['stderr']):verdict='test_error'
        return {'verdict':verdict,'compile_elapsed_ms':compiled['elapsed_ms'],'tests':runs}
