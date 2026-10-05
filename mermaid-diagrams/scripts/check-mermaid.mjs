#!/usr/bin/env node
/**
 * Parse-check mermaid diagrams with the real mermaid parser.
 *
 *   node scripts/check-mermaid.mjs diagram.md [more.md ...]
 *   node scripts/check-mermaid.mjs diagram.mmd
 *   cat diagram.mmd | node scripts/check-mermaid.mjs -
 *
 * Markdown files: every ```mermaid / ~~~mermaid fenced block is checked.
 * Other files: the whole file is checked as one diagram (unless it contains
 * mermaid fences, in which case the fences are used).
 *
 * Exit codes: 0 = every block parsed, 1 = a parse error or no block found, 2 = bad usage
 * or mermaid could not be installed.
 *
 * mermaid + jsdom are installed on first run into a cache directory: an
 * existing install is reused, otherwise ~/.cache/mermaid-check, otherwise
 * $TMPDIR/mermaid-check (for sandboxes where $HOME is read-only). Override
 * with MERMAID_CHECK_HOME=/path.
 */
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { accessSync, constants, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// Pinned so every machine parses against the same grammar.
const MERMAID_VERSION = '12';
const JSDOM_VERSION = '29';

const candidates = [
  process.env.MERMAID_CHECK_HOME,
  join(homedir(), '.cache', 'mermaid-check'),
  join(tmpdir(), 'mermaid-check'),
].filter(Boolean);

const installed = (dir) => existsSync(join(dir, 'node_modules', 'mermaid'));
const writable = (dir) => {
  try {
    mkdirSync(dir, { recursive: true });
    accessSync(dir, constants.W_OK);
    return true;
  } catch {
    return false;
  }
};

const home = candidates.find(installed) ?? candidates.find(writable) ?? candidates[candidates.length - 1];
const bridge = join(home, 'mermaid-bridge.mjs');

function setup() {
  if (!installed(home)) {
    process.stderr.write(`[check-mermaid] installing mermaid@${MERMAID_VERSION} + jsdom@${JSDOM_VERSION} into ${home}\n`);
    if (!writable(home)) {
      process.stderr.write('[check-mermaid] cache directory is not writable; set MERMAID_CHECK_HOME to a writable path.\n');
      process.exit(2);
    }
    const npm = spawnSync(
      'npm',
      ['install', '--prefix', home, '--cache', join(home, '.npm'), '--no-audit', '--no-fund', '--loglevel', 'error',
       `mermaid@${MERMAID_VERSION}`, `jsdom@${JSDOM_VERSION}`],
      { stdio: 'inherit', shell: process.platform === 'win32' },
    );
    if (npm.status !== 0) {
      process.stderr.write(`[check-mermaid] install failed; install mermaid + jsdom into ${home} manually, or set MERMAID_CHECK_HOME.\n`);
      process.exit(2);
    }
  }
  // Bare "mermaid" must resolve from inside `home`, hence a bridge living there.
  return createRequire(join(home, 'noop.cjs')).resolve('jsdom');
}

function extractBlocks(name, text) {
  const fence = /^[ \t]*(`{3,}|~{3,})[ \t]*mermaid[ \t]*$/i;
  const lines = text.split('\n');
  const blocks = [];
  for (let i = 0; i < lines.length; i++) {
    const open = lines[i].match(fence);
    if (!open) continue;
    const close = new RegExp(`^[ \\t]*${open[1][0]}{${open[1].length},}[ \\t]*$`);
    let end = i + 1;
    while (end < lines.length && !close.test(lines[end])) end++;
    blocks.push({ label: `${name}:${i + 2}`, code: lines.slice(i + 1, end).join('\n') });
    i = end;
  }
  if (blocks.length) return blocks;
  if (/\.(md|markdown|mdx)$/i.test(name)) {
    process.stderr.write(`[check-mermaid] no mermaid fence found in ${name}\n`);
    return [];
  }
  const raw = text.replace(/^\uFEFF/, '').trim();
  return raw ? [{ label: name, code: raw }] : [];
}

function condense(message) {
  const lines = String(message).split('\n');
  const caret = lines.findIndex((l) => /^-+\^/.test(l.trim()));
  const kept = caret >= 0 ? lines.slice(0, caret + 1) : lines.slice(0, 2);
  return kept.map((l) => l.trimEnd()).join('\n');
}

const argv = process.argv.slice(2);
if (!argv.length || argv.includes('-h') || argv.includes('--help')) {
  const usage = readFileSync(new URL(import.meta.url), 'utf8')
    .split('*/')[0]
    .replace(/^#!.*\n/, '')
    .replace(/^\/\*\*\n/, '')
    .replace(/^ \* ?/gm, '');
  process.stdout.write(usage);
  process.exit(argv.length ? 0 : 2);
}

const { JSDOM } = await import(pathToFileURL(setup()).href);
const dom = new JSDOM('<!doctype html><html><body></body></html>', { pretendToBeVisual: true });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, 'navigator', { value: dom.window.navigator, configurable: true });

if (!existsSync(bridge)) writeFileSync(bridge, "export { default } from 'mermaid';\n");
const mermaid = (await import(pathToFileURL(bridge).href)).default;
mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });

let checked = 0;
let failed = 0;
for (const arg of argv) {
  const name = arg === '-' ? '<stdin>' : resolve(arg);
  let text;
  try {
    text = arg === '-' ? readFileSync(0, 'utf8') : readFileSync(name, 'utf8');
  } catch (err) {
    process.stderr.write(`[check-mermaid] cannot read ${name}: ${err.code ?? err.message}\n`);
    process.exit(2);
  }
  for (const { label, code } of extractBlocks(arg === '-' ? name : arg, text)) {
    checked++;
    let type = '?';
    try { type = mermaid.detectType(code); } catch { /* reported by parse below */ }
    try {
      await mermaid.parse(code);
      process.stdout.write(`OK    ${label}  [${type}]\n`);
    } catch (err) {
      failed++;
      const src = code.split('\n');
      const errLine = Number((String(err.message).match(/line (\d+)/) || [])[1]);
      const context = errLine && src[errLine - 1] ? `\n      source: ${src[errLine - 1].trim()}` : '';
      process.stdout.write(`FAIL  ${label}  [${type}]${context}\n${condense(err.message).replace(/^/gm, '      ')}\n`);
    }
  }
}

process.stdout.write(`\n${checked - failed}/${checked} mermaid block(s) parsed\n`);
if (!checked) {
  process.stderr.write('[check-mermaid] no mermaid blocks found — pass a file containing ```mermaid fences, a .mmd file, or stdin\n');
  process.exit(1);
}
process.exit(failed ? 1 : 0);
