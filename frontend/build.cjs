const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = __dirname;
const out = path.join(root, 'vendor');
fs.mkdirSync(out, {recursive:true});
execFileSync(process.execPath, [require.resolve('tailwindcss/lib/cli.js'), '-c', path.join(root,'tailwind.config.cjs'), '-i', path.join(root,'styles.css'), '-o', path.join(out,'dashboard.css'), '--minify'], {stdio:'inherit'});
for (const [source, target] of [
  ['lightweight-charts/dist/lightweight-charts.standalone.production.js', 'lightweight-charts.standalone.production.js'],
  ['lightweight-charts/LICENSE','LICENSE-lightweight-charts'],
  ['material-symbols/material-symbols-outlined.woff2', 'material-symbols-outlined.woff2'],
  ['material-symbols/LICENSE','LICENSE-material-symbols'],
  ['tailwindcss/LICENSE','LICENSE-tailwindcss']
]) fs.copyFileSync(path.join(root,'node_modules',source),path.join(out,target));
