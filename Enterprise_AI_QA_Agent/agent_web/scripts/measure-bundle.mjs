import { writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { build } from "vite";

// Measure the real production graph without overwriting dist or changing limits.
const output = await build({ logLevel: "warn", build: { write: false } });
const chunks = output.output.filter((item) => item.type === "chunk");
const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]));
const sizes = (code) => ({ bytes: Buffer.byteLength(code), gzipBytes: gzipSync(code).length });
const entries = chunks.filter((chunk) => chunk.isEntry).map((entry) => {
  const visited = new Set();
  function visit(name) {
    if (visited.has(name)) return;
    visited.add(name);
    for (const dependency of byName.get(name)?.imports ?? []) visit(dependency);
  }
  visit(entry.fileName);
  const files = [...visited].filter((name) => byName.has(name));
  const total = files.reduce((sum, name) => {
    const size = sizes(byName.get(name).code);
    return { bytes: sum.bytes + size.bytes, gzipBytes: sum.gzipBytes + size.gzipBytes };
  }, { bytes: 0, gzipBytes: 0 });
  return { file: entry.fileName, staticFiles: files, ...total };
});
const report = {
  generatedAt: new Date().toISOString(),
  note: "JavaScript UTF-8 bytes and sum of individually gzipped files; excludes CSS/fonts, dynamic imports and runtime latency.",
  entries,
  chunks: chunks.map((chunk) => ({
    file: chunk.fileName, ...sizes(chunk.code), imports: chunk.imports,
    dynamicImports: chunk.dynamicImports,
  })).sort((a, b) => b.bytes - a.bytes),
};
const serialized = `${JSON.stringify(report, null, 2)}\n`;
if (process.argv[2]) writeFileSync(process.argv[2], serialized);
else process.stdout.write(serialized);
