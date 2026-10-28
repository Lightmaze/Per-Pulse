/** Include dependency licenses in the independently distributable web build. */
import { readFile, readdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const lock = JSON.parse(await readFile(join(root, 'package-lock.json'), 'utf8'));
const sections = ['Per Pulse web distribution — third-party notices\n\nThe application retains its Apache-2.0 license. These dependencies retain their own licenses.'];
for (const [location, entry] of Object.entries(lock.packages).sort(([a], [b]) => a.localeCompare(b))) {
  if (!location.startsWith('node_modules/') || entry.dev) continue;
  const directory = join(root, location);
  const metadata = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
  const files = (await readdir(directory)).filter(name => /^(licen[cs]e|copying)(\.|$)/i.test(name)).sort();
  if (!files.length) throw new Error(`Missing license text for ${metadata.name}@${metadata.version}`);
  const notices = await Promise.all(files.map(name => readFile(join(directory, name), 'utf8')));
  sections.push(`${metadata.name}@${metadata.version}\n${'='.repeat(60)}\n${notices.join('\n\n')}`);
}
await writeFile(join(root, 'dist/third-party-notices.txt'), sections.join('\n\n') + '\n');
console.log(`Included license notices for ${sections.length - 1} production dependencies.`);
