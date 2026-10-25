import { cp, copyFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root = fileURLToPath(new URL('../', import.meta.url));
const target = path.resolve(root, '../web/assets/interface');
if (target !== path.resolve(root, '../web/assets/interface')) throw new Error('Invalid output path');
await cp(path.join(root, 'licenses'), path.join(target, 'licenses'), { recursive: true });
await copyFile(path.join(root, 'THIRD_PARTY.md'), path.join(target, 'THIRD_PARTY.md'));
console.log('Copied original component and dependency notices into product assets.');
