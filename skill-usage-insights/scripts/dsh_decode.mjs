// Decode one dsh session log to the events the collector consumes.
//
// A dsh session log is a multi-frame zstd stream: one frame per append, so
// `zstdDecompressSync` on the whole buffer returns only the first frame. Frame
// magics are scanned and each frame decoded on its own. Node ships zstd and dsh
// ships Node, so this replaces a Python zstd dependency.
//
//   node dsh_decode.mjs <session.v3.jsonl.zstd>   ->  JSON array on stdout
import { readFileSync } from 'node:fs';
import { zstdDecompressSync } from 'node:zlib';

const MAGIC = [0x28, 0xb5, 0x2f, 0xfd];
const WANTED = new Set([
  'session', 'turn/start', 'turn/end', 'assistant/message', 'tool/call', 'tool/result',
  'user/message', 'subagent/catalog', 'goal/change', 'command/run',
]);

function frameOffsets(buffer) {
  const offsets = [];
  for (let i = 0; i + 3 < buffer.length; i += 1) {
    if (buffer[i] === MAGIC[0] && buffer[i + 1] === MAGIC[1] && buffer[i + 2] === MAGIC[2] && buffer[i + 3] === MAGIC[3]) {
      offsets.push(i);
    }
  }
  return offsets;
}

function decode(path) {
  const raw = readFileSync(path);
  const offsets = frameOffsets(raw);
  if (offsets.length === 0) throw new Error('no zstd frames found');
  const events = [];
  for (let i = 0; i < offsets.length; i += 1) {
    const end = i + 1 < offsets.length ? offsets[i + 1] : raw.length;
    const text = zstdDecompressSync(raw.subarray(offsets[i], end)).toString('utf8');
    for (const line of text.split('\n')) {
      if (!line.trim()) continue;
      let event;
      try {
        event = JSON.parse(line);
      } catch {
        continue; // a torn tail line is retried by the next collection run
      }
      if (WANTED.has(event.type)) events.push(event);
    }
  }
  return events;
}

const path = process.argv[2];
if (!path) {
  process.stderr.write('usage: dsh_decode.mjs <session.v3.jsonl.zstd>\n');
  process.exit(2);
}
try {
  process.stdout.write(JSON.stringify(decode(path)));
} catch (error) {
  process.stderr.write(`${error && error.message ? error.message : error}\n`);
  process.exit(1);
}
