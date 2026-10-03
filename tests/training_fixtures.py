from graspmemoedu.training_content import fingerprint


def training_bank(subject='math'):
    def question(qid, spec=None):
        row = {'id': qid, 'title': '隔离习题 ' + qid, 'question': '一加一等于多少？',
               'html': '<p>一加一等于多少？</p>', 'reference_answer': '2',
               'explanation_html': '<p>一加一等于二。</p>', 'answer_type': 'text', 'constraints': '写出答案。'}
        if spec:
            row['interaction'] = {'version': 1, **spec}
        return row
    blank = question('blank', {'type': 'fill_blank', 'grading': 'exact', 'blanks': [{'id': 'a', 'answers': ['2']}]})
    blank.update(question='一加一等于{{blank:a}}。', html='<p>一加一等于{{blank:a}}。</p>')
    bank = {'schema': 1, 'id': 'isolated-bank', 'subject_id': subject, 'title': '隔离题库', 'description': '仅用于验收',
            'math_css': '', 'groups': [
                {'id': 'chapter', 'title': '第一章', 'parent_id': None, 'questions': []},
                {'id': 'group-a', 'title': '第一题组', 'parent_id': 'chapter', 'questions': [
                    question('essay'), question('exact', {'type': 'text', 'grading': 'exact', 'answers': ['2']}),
                    question('boolean', {'type': 'true_false', 'statements': [{'id': 'a', 'text': '一加一等于二', 'correct': True}]}),
                    question('choice', {'type': 'choice', 'options': [{'id': 'a', 'text': '2'}, {'id': 'b', 'text': '3'}], 'correct_id': 'a'}), blank]},
                {'id': 'group-b', 'title': '第二题组', 'parent_id': 'chapter', 'questions': [question('essay-b')]}]}
    bank['version'] = fingerprint(bank)
    return bank
