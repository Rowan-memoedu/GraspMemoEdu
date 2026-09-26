"""Local review administration against isolated synthetic learner data."""
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch
import uuid

from graspmemoedu import cli
from graspmemoedu.config import Settings
from graspmemoedu.review import ReviewBackend
from graspmemoedu.service import LearningService
from tests.fixtures import synthetic_course, temporary_test_directory, DeterministicJudge


class ReviewCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_test_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.course = synthetic_course(1)
        path = self.root / 'courses' / 'digits-to-letters.json'
        path.parent.mkdir()
        path.write_text(json.dumps(self.course), encoding='utf-8')
        self.settings = Settings(data_dir=self.root)
        self.learning = LearningService(self.course, self.root, DeterministicJudge(), learner_id='fixture')
        self.backend = ReviewBackend(self.learning)
        self.time = datetime.now(timezone.utc)
        self.backend.clock = lambda: self.time

    def invoke(self, command, *extra, status=0):
        stdout, stderr = io.StringIO(), io.StringIO()
        code = 0
        with patch.object(sys, 'argv', ['graspmemoedu', '--data-dir', str(self.root), command,
                                        '--learner', 'fixture', '--topic', self.course['id'], *extra]), redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                cli.main()
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, status, stderr.getvalue())
        self.assertNotIn('REFERENCE_ONLY', stdout.getvalue())
        return json.loads(stdout.getvalue()) if code == 0 else stderr.getvalue()

    def payload(self, **extra):
        state = self.learning.state()
        return {'request_id': str(uuid.uuid4()), 'expected_revision': state['revision'],
                'attempt_id': state['attempt_id'], 'step_id': state['active_step_id'], **extra}

    def complete_lesson(self):
        self.learning.choose(self.payload(choice='mastered'))
        item = self.learning.submit(self.payload(answer='correct'))
        self.learning.process_submission(item['submission_id'])
        self.learning.advance(self.payload())
        self.assertEqual(self.learning.state()['status'], 'completed')

    def pause_review(self):
        self.time = datetime.fromisoformat(self.backend.next_due_at())
        state = self.backend.begin('module-1', request_id=str(uuid.uuid4()))
        return self.backend.pause(state['session_id'], request_id=str(uuid.uuid4()))

    def test_completed_lesson_review_unlock_exact_event_and_replay(self):
        self.complete_lesson()
        paused = self.pause_review()
        summary = self.invoke('review-status')
        self.assertEqual(summary['lesson_status'], 'completed')
        self.assertEqual(summary['pause']['event_id'], paused['pause_event_id'])
        self.assertEqual(summary['policy']['ratings'], [4, 2, 1])
        self.invoke('review-unlock', '--pause-event', 'wrong-event', status=2)
        self.assertEqual(self.invoke('review-status')['pause']['event_id'], paused['pause_event_id'])
        result = self.invoke('review-unlock', '--pause-event', paused['pause_event_id'])
        self.assertTrue(result['unlocked'])
        self.assertIsNone(self.invoke('review-status')['pause'])
        self.assertEqual(self.invoke('review-unlock', '--pause-event', paused['pause_event_id']), result)
        state = self.backend.begin('module-1', request_id=str(uuid.uuid4()))
        self.assertNotEqual(state['session_id'], paused['session_id'])
        self.assertEqual(state['practice_error_count'], 0)
        self.assertEqual(self.learning.state()['status'], 'completed')

    def test_original_lesson_unlock_thaws_review(self):
        paused = self.learning.pause({'request_id': 'lesson-pause'})['pause']
        self.assertIsNotNone(self.invoke('review-status')['pause'])
        result = self.invoke('unlock', '--module', paused['module_id'], '--pause-event', paused['event_id'])
        self.assertEqual(result['status'], 'in_progress')
        self.assertIsNone(self.invoke('review-status')['pause'])

    def test_review_unlock_also_restores_paused_lesson(self):
        paused = self.learning.pause({'request_id': 'lesson-pause'})['pause']
        self.invoke('review-unlock', '--pause-event', paused['event_id'])
        self.assertEqual(self.learning.state()['status'], 'in_progress')
        self.assertIsNone(self.invoke('review-status')['pause'])

    def test_status_and_backup_service_do_not_initialize_review(self):
        other = self.root / 'other'
        other.mkdir()
        course_path = other / 'course.json'
        course_path.write_text(json.dumps(self.course), encoding='utf-8')
        settings = Settings(data_dir=other, course_path=course_path)
        result = cli.review_status(settings, 'unseen', self.course['id'])
        self.assertFalse(result['enabled'])
        self.assertFalse((other / 'learning.sqlite3').exists())
        service = cli.make_service(settings)
        self.assertIsNone(service.review_backend)
        cli.review_status(settings, 'unseen', self.course['id'])
        with closing(sqlite3.connect(service.db_path)) as con:
            self.assertIsNone(con.execute("SELECT 1 FROM sqlite_master WHERE name='review_cards'").fetchone())
            self.assertEqual(con.execute('SELECT COUNT(*) FROM topics').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
