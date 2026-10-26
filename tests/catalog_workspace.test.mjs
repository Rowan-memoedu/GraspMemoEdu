import test from 'node:test';
import assert from 'node:assert/strict';
import {catalogPath, courseWorkspaceId, createWorkspaceLocations} from '../web/catalog-workspace.js';

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
