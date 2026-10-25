import { readFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const manifest = JSON.parse(await readFile(path.join(root, 'registry/manifest.json'), 'utf8'));
let files = 0;
for (const record of manifest) {
  const raw = await readFile(path.join(root, 'registry', record.name + '.json'));
  if (createHash('sha256').update(raw).digest('hex') !== record.sha256) throw new Error(`Registry changed: ${record.name}`);
  for (const file of JSON.parse(raw).files ?? []) {
    if (!file.content) continue;
    const local = await readFile(path.join(root, 'src/vendor', record.name, path.basename(file.path.replaceAll('\\', '/'))), 'utf8');
    if (local !== file.content) throw new Error(`Vendor changed: ${file.path}`);
    files++;
  }
}
const chart = JSON.parse(await readFile(path.join(root, 'registry/chart-21st.json'), 'utf8'));
if (await readFile(path.join(root, 'src/vendor/chart/Chart21st.tsx'), 'utf8') !== chart.componentCode) throw new Error('21st chart source changed');
for (const [file, source] of Object.entries(chart.registryDependencies.filesWithRegistry)) {
  if (file.includes('badge')) continue; // Explicitly documented external adapter.
  if (await readFile(path.join(root, 'src', file), 'utf8') !== source.code) throw new Error(`21st primitive changed: ${file}`);
  files++;
}
console.log(`Verified ${manifest.length} registry snapshots, the 21st chart snapshot and ${files + 1} unchanged source files.`);
