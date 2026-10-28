import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// Developer and preview pages expose the same native action UI as the
// packaged server. Loopback binding alone cannot stop iframe clickjacking.
const frameHeaders = {
  'X-Frame-Options': 'DENY',
  'Content-Security-Policy': "frame-ancestors 'none'",
};

function dependencyNotices(): Plugin {
  return {
    name: 'bundled-dependency-notices', apply: 'build',
    generateBundle(_options, bundle) {
      const roots = new Set<string>();
      for (const item of Object.values(bundle)) {
        if (item.type !== 'chunk') continue;
        for (const [id, module] of Object.entries(item.modules)) {
          if (!module.renderedLength) continue;
          const path = id.replace(/^\0/, '').split('?')[0];
          const offset = path.lastIndexOf('/node_modules/');
          if (offset < 0) continue;
          const parts = path.slice(offset + 14).split('/');
          roots.add(path.slice(0, offset + 14) + parts.slice(0, parts[0].startsWith('@') ? 2 : 1).join('/'));
        }
      }
      const notices = [...roots].map(root => {
        const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
        const licenses = readdirSync(root).filter(name => /^(licen[cs]e|copying)(\.|$)/i.test(name));
        if (!licenses.length) throw new Error(`Missing bundled license for ${manifest.name}`);
        return `${manifest.name} ${manifest.version}\n${licenses.map(name => readFileSync(join(root, name), 'utf8').trim()).join('\n\n')}`;
      }).sort();
      this.emitFile({ type: 'asset', fileName: 'THIRD_PARTY_NOTICES.txt', source: 'Third-party software included in the Per Pulse browser client.\n\n' + notices.join('\n\n----------------------------------------\n\n') + '\n' });
    },
  };
}

export default defineConfig({
  plugins: [react(), dependencyNotices()],
  server: {
    host: '127.0.0.1',
    headers: frameHeaders,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  preview: { host: '127.0.0.1', headers: frameHeaders },
  build: { sourcemap: false, emptyOutDir: true },
});
