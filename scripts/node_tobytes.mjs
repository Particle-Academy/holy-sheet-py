// Cross-runtime parity helper: emit xlsx bytes from the Node holy-sheet for a
// given JSON input, so the Python port can be diffed against its other twin.
//
//   node node_tobytes.mjs <input.json> <out.xlsx>
//
// `HOLY_SHEET_JS_DIST` (the built `dist/index.js`) is checked before the
// sibling checkout, for the same reason php_tobytes.php checks
// HOLY_SHEET_PHP_SRC first: a sibling path alone resolves only inside the .agi
// envelope.

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const dist =
  process.env.HOLY_SHEET_JS_DIST || resolve(here, "..", "..", "holy-sheet-js", "dist", "index.js");

if (process.argv.length < 4) {
  process.stderr.write("usage: node node_tobytes.mjs <input.json> <out.xlsx>\n");
  process.exit(2);
}
if (!existsSync(dist)) {
  process.stderr.write(`holy-sheet-js dist not found at ${dist}. Set HOLY_SHEET_JS_DIST.\n`);
  process.exit(3);
}

const { Agent } = await import(pathToFileURL(dist).href);
const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
writeFileSync(process.argv[3], Agent.toBytes(input));
