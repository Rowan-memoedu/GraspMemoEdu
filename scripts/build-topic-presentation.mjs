// Build with the installed Blog dependency environment; runtime uses local assets.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {createRequire} from 'node:module';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const dependencyRoot = process.env.GRASPMEMOEDU_BLOG_DEPENDENCIES || 'D:/Projects/PersonalWebsite/node_modules';
const require = createRequire(path.join(dependencyRoot, '_topic-build.cjs'));
const {build} = require('esbuild');
const vendor = path.join(root, 'vendor/personalwebsite/sample');
const manifest = JSON.parse(fs.readFileSync(path.join(vendor, 'upstream/callout/manifest.json'), 'utf8'));
for (const item of manifest.files) {
  const content = fs.readFileSync(path.join(vendor, 'upstream/callout', item.name), 'utf8').replace(/\r\n/g, '\n');
  if (crypto.createHash('sha256').update(content).digest('hex') !== item.sha256) throw new Error('Pinned source hash differs: ' + item.name);
}
const source = path.join(vendor, 'render-mermaid.mjs').replaceAll('\\', '/');
const assets = {};
function saveBrowserAssets(result) {
  for (const file of result.outputFiles) {
    fs.mkdirSync(path.dirname(file.path), {recursive: true});
    fs.writeFileSync(file.path, file.contents);
    const relative = path.relative(path.join(root, 'web'), file.path).replaceAll('\\', '/');
    assets[relative] = crypto.createHash('sha256').update(file.contents).digest('hex');
  }
}
await build({stdin: {contents: `export {prepareMermaid} from ${JSON.stringify(source)};`, resolveDir: root},
  outfile: path.join(root, 'scripts/generated/prepare-mermaid.mjs'), bundle: true, format: 'esm', platform: 'node', minify: true,
  nodePaths: [dependencyRoot], legalComments: 'eof'});

saveBrowserAssets(await build({stdin: {contents: `export {mountMermaid} from ${JSON.stringify(source)};`, resolveDir: root},
  outdir: path.join(root, 'web/vendor/mermaid'), entryNames: 'index', chunkNames: '[name]-[hash]',
  bundle: true, splitting: true, format: 'esm', platform: 'browser', minify: true,
  nodePaths: [dependencyRoot], legalComments: 'eof', write: false}));

// Export the original Blog list renderer without injecting an inline script.
const backlinks = fs.readFileSync(path.join(vendor, 'document-backlinks.mjs'), 'utf8');
saveBrowserAssets(await build({stdin: {contents: backlinks + '\nexport {renderBacklinks};', resolveDir: vendor},
  outfile: path.join(root, 'web/vendor/document-backlinks.mjs'), bundle: true, format: 'esm', platform: 'browser', minify: false, write: false}));
fs.writeFileSync(path.join(root, 'scripts/topic-presentation-assets.json'), JSON.stringify({files: assets}, null, 2) + '\n');

const styleEntry = path.join(vendor, 'callout-styles.mjs');
const styleBundle = await build({entryPoints: [styleEntry], bundle: true, format: 'esm', platform: 'node', write: false,
  define: {'import.meta.url': JSON.stringify(pathToFileURL(styleEntry).href)}, nodePaths: [dependencyRoot],
  plugins: [{name: 'pinned-css-runtime', setup(builder) {
    builder.onResolve({filter: /^lightningcss$/}, args => ({path: pathToFileURL(require.resolve(args.path)).href, external: true}));
  }}]});
const {composeCalloutStyles} = await import('data:text/javascript;base64,' + Buffer.from(styleBundle.outputFiles[0].text).toString('base64'));
const config = JSON.parse(fs.readFileSync(path.join(root, 'vendor/personalwebsite/config/callouts.json'), 'utf8'));
const palettes = [
  ['hint', 'hint', '#ffffd6', '#876300', '#443600', '#604000'],
  ['aside', '题外话', '#f3e8ff', '#7e22ce', '#3b0764', '#6b21a8'],
  ['requirement', '要求', '#e5f1ff', '#285b94', '#183b63', '#174d8b'],
  ['source', '题源', '#f0f1f3', '#63666c', '#34363b', '#424f69'],
];
config.tags = palettes.map(([key, name, background, accent, text, link]) => ({key, name, background, accent, text, link}));
fs.writeFileSync(path.join(root, 'web/topic-callouts.css'), composeCalloutStyles(config));
console.log('Built pinned Blog Callout, Mermaid and backlinks assets.');
