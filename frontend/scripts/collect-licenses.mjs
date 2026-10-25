import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const output = path.join(root, 'licenses');
await mkdir(output, { recursive: true });
for (const name of ['react','react-dom','motion','gsap','lucide-react','@tabler/icons-react','react-dropzone','recharts','clsx','tailwind-merge','@fontsource-variable/geist','@fontsource-variable/geist-mono']) {
  let copied = false;
  for (const file of ['LICENSE','LICENSE.md','LICENSE.txt','LICENSE.TXT','license','license.md']) {
    try {
      const text = await readFile(path.join(root, 'node_modules', name, file));
      await writeFile(path.join(output, name.replaceAll('/','-').replace('@','') + '.txt'), text);
      copied = true; break;
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  if (!copied) console.log(`No package-root license file: ${name}; see package.json license field.`);
}
for (const [name,url] of Object.entries({
  'react-bits':'https://raw.githubusercontent.com/DavidHDev/react-bits/main/LICENSE.md',
  'aceternity':'https://raw.githubusercontent.com/aceternity/ui/main/LICENSE',
  'magicui':'https://raw.githubusercontent.com/magicuidesign/magicui/main/LICENSE.md',
  'reui':'https://raw.githubusercontent.com/keenthemes/reui/main/LICENSE.md',
})) {
  const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (response.ok) { await writeFile(path.join(output, name + '.txt'), await response.text()); console.log(`Saved ${name} license`); }
  else console.log(`${name}: HTTP ${response.status}; retain official source link in THIRD_PARTY.md`);
}
