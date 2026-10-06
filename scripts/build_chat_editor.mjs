/** Build public chat assets from the pinned lockfile; no private input. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import crypto from 'node:crypto';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = process.env.GRASPMEMOEDU_CHAT_RUNTIME || 'D:/Caches/GraspMemoEdu/chat-runtime';
const lock = JSON.parse(await fs.readFile(path.join(root, 'scripts/chat-dependencies/package-lock.json'), 'utf8'));
for (const [name, info] of Object.entries(lock.packages)) {
  if (!name || info.optional) continue;
  const installed = JSON.parse(await fs.readFile(path.join(runtime, name, 'package.json'), 'utf8'));
  if (installed.version !== info.version) throw new Error('Dependency drift: ' + name);
}
const require = createRequire(path.join(runtime, 'package.json'));
const {build} = require('esbuild');
const target = path.join(root, 'web/vendor/chat'); await fs.mkdir(target, {recursive: true});
await build({entryPoints: [path.join(root, 'scripts/chat_editor_source.mjs')], outfile: path.join(target, 'chat.js'),
  bundle: true, minify: true, format: 'esm', platform: 'browser', legalComments: 'linked',
  nodePaths: [path.join(runtime, 'node_modules')]});
const katex = path.dirname(require.resolve('katex/package.json'));
await fs.copyFile(path.join(katex, 'dist/katex.min.css'), path.join(target, 'katex.min.css'));
await fs.mkdir(path.join(target, 'fonts'), {recursive: true});
for (const name of await fs.readdir(path.join(katex, 'dist/fonts'))) {
  if (name.endsWith('.woff2')) await fs.copyFile(path.join(katex, 'dist/fonts', name), path.join(target, 'fonts', name));
}
// Keep only shipped woff2 fonts in the CSS, and prevent missing-font requests.
const css = await fs.readFile(path.join(target, 'katex.min.css'), 'utf8');
await fs.writeFile(path.join(target, 'katex.min.css'), css.replace(/src:url\(([^)]+\.woff2)\) format\("woff2"\)(?:,[^;}]+)*/g, 'src:url($1) format("woff2")'));
const licenses = [];
for (const name of Object.keys(lock.packages).filter(name => name && !lock.packages[name].optional)) {
  const dir = path.join(runtime, name);
  for (const file of await fs.readdir(dir)) if (/^license(?:\.\w+)?$/i.test(file)) {
    licenses.push(name + '\n' + await fs.readFile(path.join(dir, file), 'utf8'));
  }
}
await fs.writeFile(path.join(target, 'LICENSES.txt'), licenses.join('\n\n---\n\n'));
const files = {};
async function walk(dir) {for (const entry of await fs.readdir(dir, {withFileTypes: true})) {
  const file = path.join(dir, entry.name); if (entry.isDirectory()) await walk(file);
  else files[path.relative(path.join(root, 'web'), file).replaceAll('\\', '/')] = crypto.createHash('sha256').update(await fs.readFile(file)).digest('hex');
}}
await walk(target);
await fs.writeFile(path.join(root, 'scripts/chat-assets.json'), JSON.stringify({files}, null, 2) + '\n');
console.log(`PASS built ${Object.keys(files).length} pinned chat assets`);
