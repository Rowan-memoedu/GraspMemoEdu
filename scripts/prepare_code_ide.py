"""Admit reviewed test packages and append native Academy bindings without replacing learner code."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graspmemoedu.code_judges import ENGINE, source_signature
from code_check import execute
from hyperskill_code_trial.core import source_filename, validate_bundle
import yaml


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    pending.replace(path)


def prepare(bank_path, spec_path, config_path, project, manifest_output, registry_output):
    bank = json.loads(Path(bank_path).read_text(encoding='utf-8-sig'))
    specs = json.loads(Path(spec_path).read_text(encoding='utf-8-sig'))
    config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    runtime = config['code_checker']
    manifest_path = Path(config['manifest'])
    baseline = manifest_path.read_bytes()
    manifest = json.loads(baseline)
    questions = {q['id']: q for g in bank['groups'] for q in g['questions']}
    project = Path(project).resolve()
    marker = project / '.graspmemo-code.json'
    if project.exists() and any(project.iterdir()) and not marker.exists():
        raise ValueError('Existing project is not managed by this adapter')
    project.mkdir(parents=True, exist_ok=True)
    managed = json.loads(marker.read_text()) if marker.exists() else {'schema': 1, 'tasks': []}
    definitions, records = {}, []
    prepared = []
    for spec in specs:
        if spec.get('reviewed') is not True or not spec.get('review_note', '').strip():
            raise ValueError('Explicit source review evidence is required')
        q = questions[spec['question_id']]
        if q['hyperskill']['type'] != 'code':
            raise ValueError('Only code questions are supported')
        source_template = q.get('hyperskill_presentation', {}).get('code_options', {}).get('code_templates', {}).get('java25')
        if not isinstance(source_template, str) or spec['template'].replace('\r\n', '\n') != source_template.replace('\r\n', '\n'):
            raise ValueError('Template differs from the current question source')
        signature = source_signature(q)
        bundle = spec['bundle']
        if validate_bundle(bundle):
            raise ValueError('Test bundle is not executable')
        positive = execute(spec['reference_code'], bundle, runtime)
        negative = execute(spec['negative_code'], bundle, runtime)
        if positive['verdict'] != 'accepted' or negative['verdict'] not in ('wrong_answer', 'runtime_error', 'compile_error'):
            raise ValueError('Reference/negative admission failed for ' + q['id'])
        package = {'schema': 1, 'approved': True, 'source_sha256': signature, 'engine': ENGINE,
                   'language': 'java25', 'bundle': bundle, 'review_note': spec['review_note'],
                   'runner_sha256': hashlib.sha256((ROOT / 'scripts/hyperskill_code_trial/core.py').read_bytes()).hexdigest(),
                   'admission': {'positive': positive, 'negative': negative,
                                 'reference_sha256': hashlib.sha256(spec['reference_code'].encode()).hexdigest(),
                                 'negative_sha256': hashlib.sha256(spec['negative_code'].encode()).hexdigest()}}
        raw = (json.dumps(package, ensure_ascii=False, sort_keys=True) + '\n').encode()
        digest = hashlib.sha256(raw).hexdigest()
        definition = {'source_sha256': signature, 'package_sha256': digest, 'language': 'java25', 'engine': ENGINE}
        prepared.append((q, spec, package, raw, digest, definition))
    # All packages pass before modifying the managed course or its manifest.
    for q, spec, package, raw, digest, definition in prepared:
        package_path = Path(runtime['packages']) / (digest + '.json')
        package_path.parent.mkdir(parents=True, exist_ok=True)
        if package_path.exists() and package_path.read_bytes() != raw:
            raise ValueError('Immutable package conflict')
        if not package_path.exists():
            package_path.write_bytes(raw)
        task_name = 'step_' + str(q['hyperskill']['step_id'])
        rel = 'Practice/' + task_name
        target = project / rel
        if not target.resolve().is_relative_to(project):
            raise ValueError('Task path leaves project')
        target.mkdir(parents=True, exist_ok=True)
        template = spec['template'].replace('\r\n', '\n')
        filename = 'src/' + source_filename(template)
        source = target / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            source.write_text(template, encoding='utf-8', newline='\n')
        description = target / 'task.md'
        if not description.exists():
            description.write_text(q['html'], encoding='utf-8', newline='\n')
        metadata = target / 'task-info.yaml'
        if not metadata.exists():
            metadata.write_text(yaml.safe_dump({'type': 'edu', 'custom_name': q.get('title', task_name),
                'files': [{'name': filename, 'visible': True, 'placeholders': []}], 'status': 'Unchecked', 'record': -1},
                allow_unicode=True, sort_keys=False), encoding='utf-8')
        binding = {'project_root': project.as_posix(), 'task_path': rel, 'kind': 'automated',
                   'version': definition['source_sha256'], 'source': filename, 'code_judge': definition,
                   'files': [{'path': filename, 'template': base64.b64encode(template.encode()).decode(),
                              'sha256': hashlib.sha256(template.encode()).hexdigest(), 'placeholders': []}]}
        prior = manifest['tasks'].get(q['id'])
        if prior and (Path(prior['project_root']).resolve() != project or prior['task_path'] != rel):
            raise ValueError('Existing task key belongs to another project')
        manifest['tasks'][q['id']] = binding
        if task_name not in managed['tasks']:
            managed['tasks'].append(task_name)
        definitions[bank['id'] + '/' + q['id']] = definition
        records.append({'question_id': q['id'], 'step_id': q['hyperskill']['step_id'], 'code_judge': definition})
    (project / 'Practice/lesson-info.yaml').write_text(yaml.safe_dump({'content': managed['tasks']}, sort_keys=False), encoding='utf-8')
    course = {'type': 'marketplace', 'title': 'Hyperskill Practice', 'language': 'Chinese',
              'summary': '原题练习；学习记录仍由 GraspMemoEdu 统一管理。', 'programming_language': 'Java',
              'content': ['Practice'], 'mode': 'Study', 'environment_settings': {'jvm_language_level': 'JDK_25'}, 'yaml_version': 6}
    (project / 'course-info.yaml').write_text(yaml.safe_dump(course, allow_unicode=True, sort_keys=False), encoding='utf-8')
    (project / 'settings.gradle').write_text("rootProject.name = 'HyperskillPractice'\n" + ''.join(
        "include 'Practice:" + name + "'\n" for name in managed['tasks']), encoding='utf-8')
    build = "subprojects { apply plugin: 'java'; sourceSets { main.java.srcDirs = ['src'] }; tasks.withType(JavaCompile).configureEach { options.encoding = 'UTF-8' } }\n"
    if not (project / 'build.gradle').exists():
        (project / 'build.gradle').write_text(build, encoding='utf-8')
    if not (project / 'gradle.properties').exists():
        (project / 'gradle.properties').write_text('org.gradle.jvmargs=-Xmx512m -XX:MaxMetaspaceSize=256m\norg.gradle.workers.max=2\n', encoding='utf-8')
    write(marker, managed)
    if manifest_path.read_bytes() != baseline:
        raise ValueError('Original manifest changed during preparation; merge again')
    write(Path(manifest_output), manifest)
    write(Path(registry_output), {'schema': 1, 'bindings': definitions, 'revoked': []})
    print(json.dumps({'prepared': records, 'existing_bindings_preserved': True}, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    for name in ('bank', 'input', 'config', 'project', 'manifest-output', 'registry-output'):
        p.add_argument('--' + name, required=True)
    a = p.parse_args()
    prepare(a.bank, a.input, a.config, a.project, a.manifest_output, a.registry_output)
