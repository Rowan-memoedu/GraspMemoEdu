import test from 'node:test';
import assert from 'node:assert/strict';
import {catalogPath, courseWorkspaceId, createWorkspaceLocations, courseDisplayEntries} from '../web/catalog-workspace.js';

const parse = hash => {
  const [path, search] = hash.replace(/^#\/subjects\//, '').split('?');
  const [subjectId, ...parts] = path.split('/');
  return {subjectId, path: '/' + parts.join('/'), params: new URLSearchParams(search)};
};

test('course and bank scopes encode IDs without changing ownership', () => {
  for (const kind of ['courses', 'banks']) {
    assert.equal(catalogPath(kind), '/' + kind);
    assert.equal(catalogPath(kind, 'one/two', 'a & b'), `/${kind}/one%2Ftwo?root=a%20%26%20b`);
  }
  assert.equal(courseWorkspaceId(parse('#/subjects/physics/courses/one%2Ftwo?root=unit')), 'one/two');
  assert.equal(courseWorkspaceId(parse('#/subjects/physics/courses/one/progress')), null);
});

test('each subject and workspace restores its own location, including replaced reader URLs', () => {
  const locations = createWorkspaceLocations(parse);
  const course = '#/subjects/math/courses/one?root=unit';
  const reader = '#/subjects/math/topic/topic-one?course=one&root=unit';
  const bank = '#/subjects/math/banks/bank/group?question=second';
  locations.remember(course); locations.remember(reader, 120); locations.remember(bank, 230);
  locations.remember('#/subjects/physics/courses/two');
  assert.equal(locations.href('math', 'courses'), reader);
  assert.equal(locations.href('math', 'banks'), bank);
  assert.equal(locations.href('physics', 'courses'), '#/subjects/physics/courses/two');
  assert.equal(locations.sidebarScroll(reader), 120);
  assert.equal(locations.sidebarScroll(bank), 230);
  locations.remember('#/subjects/math/banks/bank/group?question=review&card=1');
  locations.remember('#/subjects/math/banks/bank/group?question=review&review=1');
  assert.equal(locations.href('math', 'banks'), bank);
  locations.remember('#/subjects/math/courses');
  assert.equal(locations.href('math', 'courses'), '#/subjects/math/courses');
  locations.clear();
  assert.equal(locations.href('math', 'courses', '#/subjects/math/courses'), '#/subjects/math/courses');
  assert.equal(locations.sidebarScroll(reader), 0);
});

test('drafts retain source positions and draft-only branches without changing learning tasks', () => {
  const outline = [{id: 'chapter', title: 'Chapter', parent_id: null, topic_ids: []},
    {id: 'mixed', title: 'Mixed', parent_id: 'chapter', topic_ids: ['draft-one', 'ready']},
    {id: 'draft-only', title: 'Drafts', parent_id: 'chapter', topic_ids: ['draft-two']}];
  const lesson = {id: 'lesson:ready', type: 'Lesson', topic_id: 'ready', dependency_ready: false};
  const review = {id: 'review:ready', type: 'Review', topic_id: 'ready'};
  const data = {course: {id: 'course'}, tasks: [review, lesson], pending_hierarchy: [],
    topics: [{id: 'draft-one', title: 'First', status: 'read_only'},
      {id: 'ready', title: 'Ready', status: 'not_started'}, {id: 'draft-two', title: 'Last', status: 'read_only'}]};
  const before = structuredClone(data);
  const result = courseDisplayEntries(data, outline);
  assert.deepEqual(data, before);
  assert.deepEqual(result.entries.map(entry => entry.id), ['review:ready', 'draft:draft-one', 'lesson:ready', 'draft:draft-two']);
  assert.equal(result.entries[2], lesson);
  assert.equal(result.entries[2].dependency_ready, false);
  assert.deepEqual(result.hierarchy, outline);
  assert.ok(result.drafts.every(entry => entry.presentation === 'draft' && !('type' in entry)));
});

test('a selected outline root does not reintroduce ancestors or out-of-scope topics', () => {
  const outline = [{id: 'chapter', title: 'Chapter', parent_id: null, topic_ids: []},
    {id: 'selected', title: 'Selected', parent_id: 'chapter', topic_ids: []},
    {id: 'inside', title: 'Inside', parent_id: 'selected', topic_ids: ['draft']},
    {id: 'outside', title: 'Outside', parent_id: 'chapter', topic_ids: ['hidden']}];
  const data = {course: {id: 'course', root_id: 'selected'}, tasks: [], pending_hierarchy: [],
    topics: [{id: 'draft', title: 'Draft', status: 'read_only'}]};
  const result = courseDisplayEntries(data, outline);
  assert.equal(result.hierarchy.some(unit => unit.id === 'selected'), false);
  assert.equal(result.hierarchy.find(unit => unit.id === 'inside').parent_id, null);
  assert.deepEqual(result.hierarchy.find(unit => unit.id === 'outside').topic_ids, []);
  assert.deepEqual(result.entries.map(entry => entry.topic_id), ['draft']);
});

test('courses without drafts retain their current task and hierarchy views', () => {
  const data = {course: {id: 'course'}, tasks: [{id: 'one', type: 'Lesson'}], topics: [], pending_hierarchy: []};
  const result = courseDisplayEntries(data, []);
  assert.equal(result.entries, data.tasks);
  assert.equal(result.hierarchy, data.pending_hierarchy);
  assert.deepEqual(result.drafts, []);
});
