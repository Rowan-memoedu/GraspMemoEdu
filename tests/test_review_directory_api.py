from contextlib import closing
import unittest
import uuid

import test_review_papers as paper_tests


class ReviewDirectoryAPITests(unittest.TestCase):
    setUp = paper_tests.PaperTests.setUp
    call = paper_tests.PaperTests.call
    account = paper_tests.PaperTests.account
    service = paper_tests.PaperTests.service
    prime = paper_tests.PaperTests.prime
    inbox = paper_tests.PaperTests.inbox

    def test_all_materials_keeps_existing_admin_boundary_and_original_hierarchy(self):
        self.call('GET', '/api/review-papers/inbox?subject_id=math&materials=1', status=403)
        self.account(is_admin=True)
        bank = self.call('GET', '/api/review-papers/inbox?subject_id=math&materials=1')
        self.assertTrue(bank['all_materials'])
        self.assertEqual(bank['active'], [])
        self.assertEqual(bank['items'], [])
        self.assertEqual(bank['trees'][0]['course']['title'], self.bank['title'])
        self.assertEqual(bank['due_count'], sum(len(g.get('questions', [])) for g in self.bank['groups']))
        self.assertTrue(all(t['material'] for t in bank['trees'][0]['tasks']))
        atomic = self.call('GET', '/api/review-papers/inbox?subject_id=english&materials=1')
        self.assertTrue(atomic['trees'])
        self.assertTrue(all(tree['kind'] == 'atomic' for tree in atomic['trees']))
        self.assertTrue(all(t['card_id'] for tree in atomic['trees'] for t in tree['tasks']))
        with closing(self.app.state.access_store._connect()) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM review_papers').fetchone()[0], 0)

    def test_pre_timer_single_paper_remains_resumable_in_source_tree(self):
        self.prime()
        candidate = next(c for c in self.inbox()['items'] if c['kind'] == 'single')
        paper = self.call('POST', '/api/review-papers/open?subject_id=math',
                         {'candidate_id': candidate['id'], 'request_id': str(uuid.uuid4())})
        with self.service().transaction() as con:
            con.execute("UPDATE review_papers SET metadata='{}' WHERE id=?", (paper['id'],))
        inbox = self.inbox()
        resumed = [t for tree in inbox['trees'] for t in tree['tasks'] if t['id'] == paper['id']]
        self.assertEqual(len(resumed), 1)
        self.assertTrue(resumed[0]['active'])
        self.assertEqual(resumed[0]['source']['container'], self.bank['id'])
