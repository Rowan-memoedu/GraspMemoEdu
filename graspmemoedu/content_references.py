"""Derived references over installed Topic snapshots; never writes learning state."""
from collections import defaultdict
from .idea_presentation import split_ideas


class ContentReferenceIndex:
    def __init__(self, topics):
        self.topics = topics
        self.nodes, self.targets = {}, defaultdict(list)
        self.edges = []
        for topic_id, topic in topics.items():
            nodes = {node['id']: node for node in topic.get('content_nodes', [])}
            for module in topic.get('modules', []):
                for question in [module['example'], *module['practices']]:
                    for node_id in split_ideas(question['html'])[2]:
                        if node_id in nodes:
                            node = nodes[node_id]
                            nodes[node_id] = {**node, 'owner': {**node['owner'], 'field': 'explanation_html'}}
            self.nodes[topic_id] = nodes
            if topic.get('source_rem_id'):
                self.targets[topic['source_rem_id']].append((topic_id, None))
            for node_id, node in nodes.items():
                if node.get('source_rem_id'):
                    self.targets[node['source_rem_id']].append((topic_id, node_id))
            self.edges.extend((topic_id, ref) for ref in topic.get('references', []) if ref.get('source_node_id') in nodes)

    def _visible(self, access, topic_id, snapshots):
        state = snapshots.get(topic_id)
        if not state:
            return {}
        topic = self.topics[topic_id]
        if state.get('_course_version', topic['version']) != topic['version']:
            return {}
        result = {}
        for step in state.get('steps', []):
            if 'review_history' not in access['features'] and step['id'] != state.get('active_step_id'):
                continue
            kind = step.get('kind')
            if kind == 'introduction':
                if state.get('atomic', {}).get('active') and state.get('display') == 'atomic':
                    introductions = [entry['id'] for entry in topic.get('content_order', [])
                                     if entry['kind'] == 'introduction']
                    current = introductions[state['section_index']] if introductions else 'introduction'
                    if step.get('introduction_id', 'introduction') == current:
                        continue
                owner = ('introduction', step.get('introduction_id', 'introduction'), 'html')
                result[owner] = step['id']
                for field in ('example', 'footnotes'):
                    result[('introduction', owner[1], field)] = step['id']
            elif kind in ('example', 'practice'):
                if not step.get('question_bank_revision'):
                    result[(kind, step['question_id'], 'html')] = step['id']
                if step.get('phase') == 'reading' or (kind == 'practice' and step.get('phase') == 'feedback'):
                    result[(kind, step['question_id'], 'explanation_html')] = step['id']
        return result

    def view(self, access, topic_id, snapshots, subject_for, root_ready=lambda _id: True):
        allowed = {topic['id'] for topic in access['topics']} & self.topics.keys()
        owners = {tid: self._visible(access, tid, snapshots) for tid in allowed}

        def location(tid, nid):
            if tid not in allowed:
                return None
            if nid is None:
                return {'topic_id': tid, 'node_id': None, 'subject_id': subject_for(tid)} if tid in snapshots or root_ready(tid) else None
            node = self.nodes[tid].get(nid)
            if not node:
                return None
            owner = node['owner']
            step = owners[tid].get((owner['kind'], owner['id'], owner['field']))
            return {'topic_id': tid, 'node_id': nid, 'step_id': step, 'field': owner['field'],
                    'subject_id': subject_for(tid)} if step else None

        outgoing, backlinks = [], []
        for source_id, ref in self.edges:
            source = location(source_id, ref['source_node_id'])
            if not source:
                continue
            matches = self.targets.get(ref['target_rem_id'], [])
            target = location(*matches[0]) if len(matches) == 1 else None
            if source_id == topic_id:
                outgoing.append({'source_node_id': ref['source_node_id'], 'target_rem_id': ref['target_rem_id'],
                                 'label': ref['label'], 'status': 'resolved' if target else 'unresolved',
                                 **({'target': target} if target else {})})
            if target and target['topic_id'] == topic_id and source_id != topic_id:
                backlinks.append({**source, 'title': self.topics[source_id]['title']})
        # Keep source positions while showing one backlink per authored source node.
        unique = {(entry['topic_id'], entry['node_id']): entry for entry in backlinks}
        return {'outgoing': outgoing, 'backlinks': list(unique.values()),
                'locations': [item for nid in self.nodes.get(topic_id, {}) if (item := location(topic_id, nid))]}
