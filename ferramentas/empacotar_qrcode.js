// Junta a biblioteca "qrcode" (MIT, node_modules) num único script para o navegador: window.QRCode.
// Uso: node ferramentas/empacotar_qrcode.js <pasta node_modules> <saida.js>
const fs = require('fs'), path = require('path');
const [nm, saida] = process.argv.slice(2);
const mods = [], ids = new Map();
function resolver(de, req) {
  if (req.startsWith('.')) {
    let p = path.resolve(path.dirname(de), req);
    for (const c of [p, p + '.js', path.join(p, 'index.js')]) if (fs.existsSync(c) && fs.statSync(c).isFile()) return c;
    throw new Error('não achei ' + req + ' a partir de ' + de);
  }
  const dir = path.join(nm, req), pkg = JSON.parse(fs.readFileSync(path.join(dir, 'package.json')));
  return path.join(dir, pkg.main || 'index.js');
}
function adicionar(arq) {
  if (ids.has(arq)) return ids.get(arq);
  const id = mods.length; ids.set(arq, id); mods.push(null);
  let src = fs.readFileSync(arq, 'utf8');
  const deps = {};
  src = src.replace(/require\((['"])([^'"]+)\1\)/g, (m, q, r) => { const alvo = adicionar(resolver(arq, r)); deps[r] = alvo; return `__req(${alvo})`; });
  mods[id] = `function(module,exports,__req){${src}\n}`;
  return id;
}
const entrada = adicionar(path.join(nm, 'qrcode', 'lib', 'browser.js'));
const out = `/*! qrcode (MIT, Ryan Day e colaboradores) + dijkstrajs (MIT), empacotados para o navegador */\n(function(){var M=[${mods.join(',\n')}],C={};function __req(i){if(C[i])return C[i].exports;var m=C[i]={exports:{}};M[i](m,m.exports,__req);return m.exports}window.QRCode=__req(${entrada});})();\n`;
fs.writeFileSync(saida, out);
console.log('módulos:', mods.length, 'bytes:', out.length);
