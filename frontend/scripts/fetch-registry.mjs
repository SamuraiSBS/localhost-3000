import { mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const entries = {
  faulty: 'https://reactbits.dev/r/FaultyTerminal-JS-CSS.json',
  blur: 'https://reactbits.dev/r/BlurText-JS-CSS.json',
  animated: 'https://reactbits.dev/r/AnimatedContent-JS-CSS.json',
  glass: 'https://ui.aceternity.com/registry/liquid-glass.json',
  button: 'https://magicui.design/r/interactive-hover-button.json',
  upload: 'https://ui.aceternity.com/registry/file-upload.json',
  login: 'https://www.scrollxui.dev/registry/login-sections/login-minimal.json',
  chart: 'https://reui.io/r/c-chart-11.json',
};
await mkdir(path.join(root, 'registry'), { recursive: true });
const manifest = [];
for (const [name, url] of Object.entries(entries)) {
  const response = await fetch(url, { signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  const raw = await response.text();
  const document = JSON.parse(raw);
  await writeFile(path.join(root, 'registry', `${name}.json`), raw);
  const dir = path.join(root, 'src', 'vendor', name);
  await mkdir(dir, { recursive: true });
  for (const file of document.files ?? []) {
    if (!file.content || !file.path) continue;
    const basename = path.basename(file.path.replaceAll('\\', '/'));
    await writeFile(path.join(dir, basename), file.content);
  }
  const record = { name, url, sha256: createHash('sha256').update(raw).digest('hex'), dependencies: document.dependencies ?? [], registryDependencies: document.registryDependencies ?? [], files: document.files?.map(f => f.path) };
  manifest.push(record);
  console.log(JSON.stringify(record));
  await writeFile(path.join(root, 'registry', 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
}
