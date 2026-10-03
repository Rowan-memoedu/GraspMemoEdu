# Reused Blog components

These files are copied from `D:/Projects/PersonalWebsite` and retain their original
source. Callouts include al-folio and Tufted originals, licenses and pinned hashes.
`scripts/build-topic-presentation.mjs` verifies those hashes and builds the shared
import helper plus local browser assets. The deployed site does not depend on the
Blog checkout or a CDN.

The Topic adapter binds four labels, content scope and site routes. The backlinks
build exports the original list renderer; the runtime supplies only references
visible to the current learner. Mermaid preparation and rendering use the Blog
implementation, including its source-preserving failure behavior.

Build with the installed Blog dependency environment using
`node scripts/build-topic-presentation.mjs`. `GRASPMEMOEDU_BLOG_DEPENDENCIES` may
point at an equivalent dependency directory. Generated assets are committed so
Python/server and ordinary author imports require no browser-library install.
