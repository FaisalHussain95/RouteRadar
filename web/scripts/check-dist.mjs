// Two properties of the build that are easy to break and invisible until the site is live.
//
// 1. Every file except index.html carries a content hash. Pages sets its own cache headers
//    and they are short but non-zero, so anything served under a stable name can be held
//    stale by a reader; a hashed name makes a changed file a new URL, and index.html is the
//    only thing that ever has to be re-fetched to see a new deploy.
// 2. index.html points at those files under the configured base. The deploy is a Pages
//    *project* site, served from /<repo>/ — deploy-site.yml sets VITE_BASE from the
//    repository name — so a build that silently fell back to `/`, or to the local default
//    below, would produce a page whose every asset 404s, and it would look perfectly fine
//    locally either way.
//
// Run from `pnpm build`, so CI enforces both rather than someone remembering to look.
import { readdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = resolve(fileURLToPath(new URL("..", import.meta.url)), "dist");
const HASHED = /-[A-Za-z0-9_-]{8,}\.[a-z0-9]+$/;
const base = process.env.VITE_BASE ?? "/flight-detective/"; // the dev default, as in vite.config.ts

const files = (await readdir(DIST, { recursive: true, withFileTypes: true })).filter((e) =>
  e.isFile(),
);
const unhashed = files.map((e) => e.name).filter((n) => n !== "index.html" && !HASHED.test(n));
if (unhashed.length > 0) {
  console.error(`unhashed assets in web/dist: ${unhashed.join(", ")}`);
  process.exit(1);
}

const html = await readFile(resolve(DIST, "index.html"), "utf8");
const refs = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1]);
const offBase = refs.filter((r) => !r.startsWith(base));
if (refs.length === 0 || offBase.length > 0) {
  console.error(`index.html references outside base ${base}: ${offBase.join(", ") || "(none at all)"}`);
  process.exit(1);
}

console.log(`dist: ${files.length} files, all hashed but index.html, all under ${base}`);
