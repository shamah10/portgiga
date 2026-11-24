// Carteira Portgiga 2.0 (app para celular). A chave secreta nunca sai do aparelho: fica trancada pelo PIN
// (PBKDF2 + AES-GCM, as mesmas contas do site) e só é aberta na memória enquanto o app está desbloqueado.
'use strict';
const $ = id => document.getElementById(id);
const UNIDADE = 1e8;
const PREFIXO = 'ptgapp:';
const ATUALIZAR_MS = 15000;
const ABAS = ['inicio', 'atividade', 'rede', 'ajustes'];

// ------------------------------------------------------------------ guardar no aparelho
const guardar = {
  ler(k) { try { return JSON.parse(localStorage.getItem(PREFIXO + k)); } catch (e) { return null; } },
  gravar(k, v) { try { v === null ? localStorage.removeItem(PREFIXO + k) : localStorage.setItem(PREFIXO + k, JSON.stringify(v)); } catch (e) {} },
};
const preferencias = Object.assign({ trancarMin: 5, ocultarAoAbrir: false }, guardar.ler('preferencias') || {});
const salvarPreferencias = () => guardar.gravar('preferencias', preferencias);

// ------------------------------------------------------------------ criptografia (igual ao site)
const hex = b => [...new Uint8Array(b)].map(x => x.toString(16).padStart(2, '0')).join('');
const deHex = h => new Uint8Array(h.match(/../g).map(x => parseInt(x, 16)));
const sha = async t => hex(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(t)));
const b64u = s => { s = s.replace(/-/g, '+').replace(/_/g, '/'); return Uint8Array.from(atob(s + '==='.slice((s.length + 3) % 4)), c => c.charCodeAt(0)); };
async function parDe(chave) {
  const pk8 = new Uint8Array([0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20, ...deHex(await sha('ptg-ed25519:' + chave))]);
  const priv = await crypto.subtle.importKey('pkcs8', pk8, { name: 'Ed25519' }, true, ['sign']);
  return { priv, pub: hex(b64u((await crypto.subtle.exportKey('jwk', priv)).x)) };
}
async function assinar(chave, msg) {
  const p = await parDe(chave);
  return { pub: p.pub, assinatura: hex(await crypto.subtle.sign('Ed25519', p.priv, new TextEncoder().encode(msg))) };
}
const enderecoNovo = async pub => 'ptg1' + (await sha('ptg-pub:' + pub)).slice(0, 38);
const enderecoAntigo = async chave => 'ptg1' + (await sha('ptg:' + chave)).slice(0, 38);
async function enderecoDaChave(chave) {
  const antigo = await enderecoAntigo(chave);
  const d = await api('/api/carteira?endereco=' + antigo);
  return d.tipo === 'antigo' ? antigo : enderecoNovo((await parDe(chave)).pub);
}
async function pbkdf2(senha, sal) {
  const k = await crypto.subtle.importKey('raw', new TextEncoder().encode(senha), 'PBKDF2', false, ['deriveBits']);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'PBKDF2', hash: 'SHA-256', salt: typeof sal === 'string' ? new TextEncoder().encode(sal) : sal, iterations: 210000 }, k, 256));
}
const senhaLogin = async (usuario, senha) => hex(await pbkdf2(senha, 'portgiga-login:' + usuario.toLowerCase()));
async function trancar(chave, segredo) {
  const sal = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const k = await crypto.subtle.importKey('raw', await pbkdf2(segredo, sal), 'AES-GCM', false, ['encrypt']);
  return JSON.stringify({ v: 1, sal: hex(sal), iv: hex(iv), dados: hex(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, k, new TextEncoder().encode(chave))) });
}
async function destrancar(cifrada, segredo) {
  const c = JSON.parse(cifrada);
  const k = await crypto.subtle.importKey('raw', await pbkdf2(segredo, deHex(c.sal)), 'AES-GCM', false, ['decrypt']);
  return new TextDecoder().decode(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: deHex(c.iv) }, k, deHex(c.dados)));
}
async function criptoFunciona() {
  try {
    if (!window.isSecureContext || !crypto.subtle) return false;
    await parDe('00'.repeat(32));
    return true;
  } catch (e) { return false; }
}

// ------------------------------------------------------------------ servidor
// No app instalado (Android), as telas vêm de dentro do app e o servidor é outro endereço: o link público do túnel
// (muda quando o túnel reinicia) ou o PC na rede de casa. O app guarda os endereços e descobre o novo sozinho.
const EMBUTIDO = location.hostname === 'appassets.androidplatform.net';
// Endereço fixo grátis (Cloudflare Pages): as telas ficam lá e o servidor é o túnel do dia, lido de /link.json.
const HOSPEDADO = /\.pages\.dev$/.test(location.hostname);
const REMOTO = EMBUTIDO || HOSPEDADO;   // o servidor é outro endereço (não a mesma origem das telas)
const LINK_FIXO = HOSPEDADO ? '/link.json' : (window.PTG_LINK_FIXO || '');
const NATIVO = window.PortgigaNativo || null;
let servidores = REMOTO ? (guardar.ler('servidores') || window.PTG_SERVIDORES || []) : [''];
let servidor = REMOTO ? (guardar.ler('servidor') || servidores[0] || '') : '';
const normalizarServidor = u => String(u || '').trim().replace(/\/+(app\/?)?$/, '');
function lembrarServidor(u) {
  u = normalizarServidor(u); if (!u) return;
  servidores = [u, ...servidores.filter(x => x !== u)].slice(0, 6);
  guardar.gravar('servidores', servidores);
}
function buscarComPrazo(url, opcoes = {}, ms = 6000) {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), ms);
  return fetch(url, { ...opcoes, signal: c.signal }).finally(() => clearTimeout(t));
}
let descobrindo = null;
async function descobrirServidor() {
  if (descobrindo) return descobrindo;
  descobrindo = (async () => {
    let doDia = '';   // o link público de agora, publicado no endereço fixo
    if (LINK_FIXO) try { const r = await buscarComPrazo(LINK_FIXO + (LINK_FIXO.includes('?') ? '&' : '?') + 't=' + Date.now(), { cache: 'no-store' }, 5000); if (r.ok) doDia = normalizarServidor((await r.json()).portgiga); } catch (e) {}
    const candidatos = [...new Set([servidor, doDia, ...servidores, ...(window.PTG_SERVIDORES || [])].filter(Boolean))];
    for (const c of candidatos) {
      try {
        const r = await buscarComPrazo(c + '/api/servidor-publico', { cache: 'no-store' }, 5000);
        if (!r.ok) continue;
        const d = await r.json();
        servidor = c; guardar.gravar('servidor', c);
        if (d.url) lembrarServidor(d.url);   // o PC conta qual é o link público de agora
        lembrarServidor(c);
        // em casa (endereço local) continua no PC; fora de casa vale o link público
        return true;
      } catch (e) {}
    }
    return false;
  })().finally(() => { setTimeout(() => { descobrindo = null; }, 0); });
  return descobrindo;
}
async function api(caminho, corpo, tentouDescobrir) {
  const opcoes = corpo
    ? { method: 'POST', headers: { 'Content-Type': REMOTO ? 'text/plain' : 'application/json' }, body: JSON.stringify(corpo), credentials: 'same-origin' }
    : { credentials: 'same-origin', cache: 'no-store' };
  try {
    const r = await (REMOTO ? buscarComPrazo(servidor + caminho, opcoes, 12000) : fetch(caminho, opcoes));
    if (REMOTO && r.status >= 500) throw new Error('servidor fora');   // túnel velho responde 502/530
    return await r.json();
  } catch (e) {
    if (REMOTO && !tentouDescobrir && await descobrirServidor()) return api(caminho, corpo, true);
    throw e;
  }
}

// ------------------------------------------------------------------ formatação
const moeda = v => Number(v || 0).toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 8 });
const moeda2 = v => { const n = Number(v || 0); return n.toLocaleString('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: n !== 0 && Math.abs(n) < 1 ? 8 : 2 }); };
const compacto = v => { const n = Number(v || 0); return Math.abs(n) >= 1e6 ? (n / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + ' mi' : moeda2(n); };
const curto = e => e ? e.slice(0, 8) + '…' + e.slice(-6) : '';
const OCULTO = '••••••';
function unidadesDe(txt) {
  const m = String(txt).trim().replace(/\s/g, '').replace(/\./g, (x, i, s) => (s.includes(',') ? '' : '.')).replace(',', '.').match(/^(\d+)(?:\.(\d{0,8}))?$/);
  return m ? Number(m[1]) * UNIDADE + Number((m[2] || '').padEnd(8, '0')) : null;
}
const valorCampo = unidades => moeda(unidades / UNIDADE).replace(/\./g, '');
const hora = seg => new Date(seg * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
function diaTxt(seg) {
  const d = new Date(seg * 1000), hoje = new Date(), ontem = new Date(Date.now() - 864e5);
  if (d.toDateString() === hoje.toDateString()) return 'Hoje';
  if (d.toDateString() === ontem.toDateString()) return 'Ontem';
  return d.toLocaleDateString('pt-BR', { weekday: 'long', day: '2-digit', month: 'long' });
}
function quandoTxt(seg) {
  const dia = diaTxt(seg);
  return (dia === 'Hoje' || dia === 'Ontem' ? dia.toLowerCase() : new Date(seg * 1000).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short' })) + ', ' + hora(seg);
}
const dataCompleta = seg => new Date(seg * 1000).toLocaleString('pt-BR', { day: '2-digit', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
function haQuanto(seg) {
  const s = Math.max(0, Math.round(Date.now() / 1000 - seg));
  if (s < 60) return `há ${s} s`;
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  return `há ${Math.floor(s / 3600)} h`;
}
function velocidade(hps) {
  const n = Number(hps || 0);
  if (n >= 1e9) return (n / 1e9).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + ' GH/s';
  if (n >= 1e6) return (n / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 2 }) + ' MH/s';
  if (n >= 1e3) return (n / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' kH/s';
  return n + ' H/s';
}
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const vibrar = p => { try { navigator.vibrate && navigator.vibrate(p); } catch (e) {} };
const ic = id => `<svg><use href="#${id}"/></svg>`;

// avatar único para cada endereço (desenho simétrico 5×5 tirado do próprio endereço)
function avatar(endereco) {
  const h = (endereco || '').replace(/^ptg1/, '') || '0'.repeat(38);
  const matiz = parseInt(h.slice(0, 3), 16) % 360;
  const cor = `hsl(${matiz} 68% 58%)`, fundo = `hsl(${matiz} 40% 15%)`, cor2 = `hsl(${(matiz + 40) % 360} 75% 66%)`;
  let r = '';
  for (let y = 0; y < 5; y++) for (let x = 0; x < 3; x++) {
    const v = parseInt(h[3 + y * 3 + x], 16);
    if (v % 2) { const c = v > 11 ? cor2 : cor; r += `<rect x="${x}" y="${y}" width="1" height="1" fill="${c}"/>` + (x < 2 ? `<rect x="${4 - x}" y="${y}" width="1" height="1" fill="${c}"/>` : ''); }
  }
  return `<svg viewBox="-1 -1 7 7" shape-rendering="crispEdges"><rect x="-1" y="-1" width="7" height="7" fill="${fundo}"/>${r}</svg>`;
}

function aviso(texto, tipo = 'ok') {
  const t = document.createElement('div');
  t.className = 'toast ' + tipo;
  t.innerHTML = `${ic(tipo === 'erro' ? 'i-info' : 'i-check')}<span>${esc(texto)}</span>`;
  $('toasts').prepend(t);
  while ($('toasts').children.length > 3) $('toasts').lastChild.remove();
  setTimeout(() => t.remove(), 3400);
}
function copiar(texto, msg) {
  const feito = () => { aviso(msg || 'Copiado'); vibrar(15); };
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(texto).then(feito, feito); return; }
  const a = document.createElement('textarea'); a.value = texto; document.body.appendChild(a); a.select();
  try { document.execCommand('copy'); } catch (e) {} a.remove(); feito();
}

// ------------------------------------------------------------------ contatos (só neste celular)
let contatos = guardar.ler('contatos') || [];
const salvarContatos = () => guardar.gravar('contatos', contatos);
const contatoDe = e => contatos.find(c => c.endereco === e);
const nomeDe = e => (contatoDe(e) || {}).nome || curto(e);

// ------------------------------------------------------------------ navegação (com o voltar do Android)
let telaAtual = null;
const pilha = [];
function mostrarTela(tela) {
  document.querySelectorAll('.tela').forEach(t => t.classList.toggle('ativa', t.dataset.tela === tela));
  telaAtual = tela;
  const ehAba = ABAS.includes(tela);
  $('abas').classList.toggle('visivel', ehAba);
  if (ehAba) document.querySelectorAll('#abas button').forEach(b => b.classList.toggle('ativa', b.dataset.aba === tela));
}
function irPara(tela, { trocar = false } = {}) {
  if (telaAtual && !trocar) pilha.push(telaAtual);
  mostrarTela(tela);
  history.pushState({ tela }, '');
}
function voltar() {
  if (folhaAberta) { fecharFolha(); return; }
  const anterior = pilha.pop();
  if (!anterior) {
    if (ABAS.includes(telaAtual) && telaAtual !== 'inicio') irAba('inicio');
    else if (NATIVO && NATIVO.sair && (telaAtual === 'inicio' || telaAtual === 'boas-vindas' || telaAtual === 'pin')) NATIVO.sair();   // voltar no início sai do app
    return;
  }
  if (anterior === 'pin' && pinModo === 'abrir') return;   // nunca volta para uma tela de PIN já usada
  mostrarTela(anterior);
  aoMostrarAba(anterior);
}
window.addEventListener('popstate', () => { voltar(); if (telaAtual) history.pushState({ tela: telaAtual }, ''); });
document.querySelectorAll('[data-voltar]').forEach(b => b.onclick = () => voltar());

function irAba(aba) {
  pilha.length = 0;
  irPara(aba, { trocar: true });
  aoMostrarAba(aba);
  const rol = document.querySelector(`[data-tela="${aba}"] .rolavel`);
  if (rol && aba !== 'inicio') rol.scrollTop = 0;
}
function aoMostrarAba(aba) {
  if (aba === 'atividade') desenharAtividade();
  if (aba === 'rede') desenharRede();
  if (aba === 'ajustes') prepararAjustes();
}
document.querySelectorAll('#abas button').forEach(b => b.onclick = () => { vibrar(6); irAba(b.dataset.aba); });

// folhas que sobem de baixo
let folhaAberta = null;
function abrirFolha(id) {
  fecharFolha();
  folhaAberta = $(id);
  folhaAberta.classList.add('ativa');
  folhaAberta.scrollTop = 0;
  $('veu').classList.add('ativo');
}
function fecharFolha() {
  if (!folhaAberta) return;
  if (folhaAberta.id === 'f-escanear') pararCamera();
  folhaAberta.classList.remove('ativa');
  $('veu').classList.remove('ativo');
  folhaAberta = null;
}
$('veu').onclick = fecharFolha;
document.querySelectorAll('[data-fechar]').forEach(b => b.onclick = fecharFolha);
// arrastar a folha para baixo fecha
document.querySelectorAll('.folha').forEach(f => {
  let y0 = null, dy = 0;
  f.addEventListener('touchstart', ev => { if (f.scrollTop <= 0 && ev.target.closest('.alca, .folha-topo')) { y0 = ev.touches[0].clientY; dy = 0; f.style.transition = 'none'; } }, { passive: true });
  f.addEventListener('touchmove', ev => { if (y0 === null) return; dy = Math.max(0, ev.touches[0].clientY - y0); f.style.transform = `translateY(${dy}px)`; }, { passive: true });
  f.addEventListener('touchend', () => { if (y0 === null) return; f.style.transition = ''; f.style.transform = ''; if (dy > 90) fecharFolha(); y0 = null; });
});

// ------------------------------------------------------------------ estado da carteira
let chave = null;          // só na memória, enquanto desbloqueada
let cofre = guardar.ler('cofre');   // { v, endereco, cifrada }
let chavePendente = null;  // chave escolhida no cadastro, antes de ter PIN
let enderecoPendente = null;
let dados = null;          // última resposta de /api/carteira
let rede = null;           // última resposta de /api/estado
let proximoBlocoEm = 0;    // relógio local do próximo bloco
let ocultar = false;

// ------------------------------------------------------------------ PIN
let pinModo = 'criar', pinDigitado = '', pinPrimeiro = '', pinAoTerminar = null, pinOcupado = false;
function abrirPin(modo, aoTerminar) {
  pinModo = modo; pinDigitado = ''; pinAoTerminar = aoTerminar; pinOcupado = false;
  const textos = {
    criar: ['Crie um PIN', '6 números para abrir a carteira e assinar envios.', ''],
    confirmar: ['Repita o PIN', 'Digite os mesmos 6 números.', ''],
    abrir: ['Bem-vindo de volta', 'Digite o seu PIN para abrir a carteira.', ''],
    envio: ['Assinar envio', 'Digite o seu PIN para assinar.', ''],
    'ver-chave': ['Ver chave secreta', 'Digite o seu PIN.', ''],
    'trocar-atual': ['PIN atual', 'Digite o PIN que você usa hoje.', ''],
  };
  const [t, s, topo] = textos[modo];
  $('pin-titulo').textContent = t; $('pin-sub').textContent = s; $('pin-erro').textContent = '';
  $('pin-titulo-topo').textContent = topo;
  $('pin-voltar').style.visibility = modo === 'abrir' ? 'hidden' : 'visible';
  desenharPontos();
  irPara('pin', { trocar: modo === 'abrir' || telaAtual === 'pin' });
  conferirBloqueio();
}
function desenharPontos() {
  [...$('pin-pontos').children].forEach((p, i) => p.classList.toggle('cheio', i < pinDigitado.length));
}
function errouPin(msg) {
  $('pin-erro').textContent = msg;
  $('pin-pontos').classList.remove('tremer'); void $('pin-pontos').offsetWidth; $('pin-pontos').classList.add('tremer');
  vibrar([60, 40, 60]);
  pinDigitado = ''; desenharPontos();
}
function conferirBloqueio() {
  const t = guardar.ler('tentativas') || { n: 0, ate: 0 };
  const falta = Math.ceil((t.ate - Date.now()) / 1000);
  if (falta > 0) { $('pin-erro').textContent = `Muitas tentativas. Espere ${falta} s.`; setTimeout(conferirBloqueio, 1000); return true; }
  if ($('pin-erro').textContent.startsWith('Muitas tentativas')) $('pin-erro').textContent = '';
  return false;
}
function registrarErroPin() {
  const t = guardar.ler('tentativas') || { n: 0, ate: 0 };
  t.n += 1;
  if (t.n >= 5) t.ate = Date.now() + 30000 * Math.pow(2, t.n - 5);   // 30 s, 1 min, 2 min…
  guardar.gravar('tentativas', t);
}
async function pinCompleto() {
  const pin = pinDigitado;
  if (pinModo === 'criar') { pinPrimeiro = pin; abrirPin('confirmar', pinAoTerminar); return; }
  if (pinModo === 'confirmar') {
    if (pin !== pinPrimeiro) { abrirPin('criar', pinAoTerminar); $('pin-erro').textContent = 'Os PINs não eram iguais. Crie de novo.'; return; }
    pinOcupado = true; $('pin-sub').textContent = 'Trancando a carteira…';
    await pinAoTerminar(pin);
    return;
  }
  // os outros modos conferem o PIN abrindo o cofre
  if (conferirBloqueio()) { pinDigitado = ''; desenharPontos(); return; }
  pinOcupado = true; $('pin-sub').textContent = pinModo === 'envio' ? 'Assinando…' : 'Conferindo…';
  let aberta;
  try { aberta = await destrancar(cofre.cifrada, pin); } catch (e) {
    pinOcupado = false; registrarErroPin(); errouPin('PIN errado.'); conferirBloqueio();
    const textos = { abrir: 'Digite o seu PIN para abrir a carteira.', envio: 'Digite o seu PIN para assinar.' };
    $('pin-sub').textContent = textos[pinModo] || 'Digite o seu PIN.';
    return;
  }
  guardar.gravar('tentativas', null);
  await pinAoTerminar(aberta, pin);
}
$('teclado').addEventListener('click', ev => {
  const b = ev.target.closest('button');
  if (!b || pinOcupado) return;
  vibrar(8);
  if (b.hasAttribute('data-apagar')) { pinDigitado = pinDigitado.slice(0, -1); desenharPontos(); return; }
  if (pinDigitado.length >= 6) return;
  pinDigitado += b.dataset.n; desenharPontos();
  if (pinDigitado.length === 6) setTimeout(pinCompleto, 120);
});
document.addEventListener('keydown', ev => {   // teclado físico (computador) também funciona
  if (telaAtual !== 'pin' || pinOcupado) return;
  if (/^[0-9]$/.test(ev.key) && pinDigitado.length < 6) { pinDigitado += ev.key; desenharPontos(); if (pinDigitado.length === 6) setTimeout(pinCompleto, 120); }
  if (ev.key === 'Backspace') { pinDigitado = pinDigitado.slice(0, -1); desenharPontos(); }
});

// grava o cofre com a chave e o PIN e entra na carteira
async function salvarCofreEEntrar(novaChave, endereco, pin) {
  cofre = { v: 1, endereco, cifrada: await trancar(novaChave, pin), criado: Date.now() };
  guardar.gravar('cofre', cofre);
  chave = novaChave; chavePendente = null; enderecoPendente = null;
  pilha.length = 0;
  entrarNaCarteira();
  aviso('Carteira pronta!');
}

// ------------------------------------------------------------------ chave em 16 grupos
function desenharGrade(el, ch) {
  el.dataset.oculta = '1';
  el.innerHTML = ch.match(/.{4}/g).map((g, i) => `<div class="g"><small>${i + 1}</small><b>${g}</b></div>`).join('') +
    `<button class="revelar" type="button">${ic('i-olho')}<span>Toque para mostrar</span><small class="mini">confira se ninguém está olhando</small></button>`;
  el.querySelector('.revelar').onclick = () => { el.dataset.oculta = '0'; vibrar(10); };
}

// ------------------------------------------------------------------ boas-vindas, cadastro e importação
let conferirIdx = [0, 15];
$('bv-criar').onclick = () => {
  chavePendente = hex(crypto.getRandomValues(new Uint8Array(32)));
  desenharGrade($('bk-chave'), chavePendente);
  $('bk-c1').checked = $('bk-c2').checked = false; $('bk-anotei').disabled = true;
  irPara('backup');
};
const podeSeguirBackup = () => { $('bk-anotei').disabled = !($('bk-c1').checked && $('bk-c2').checked); };
$('bk-c1').onchange = podeSeguirBackup; $('bk-c2').onchange = podeSeguirBackup;
$('bk-copiar').onclick = () => copiar(chavePendente, 'Chave copiada. Guarde num lugar seguro e apague da área de transferência.');
$('bk-baixar').onclick = () => {
  const grupos = chavePendente.match(/.{4}/g).map((g, i) => `${String(i + 1).padStart(2, ' ')}. ${g}`).join('\n');
  const texto = `CARTEIRA PORTGIGA - CHAVE SECRETA\n\n${chavePendente}\n\nEm grupos:\n${grupos}\n\nGuarde este arquivo num lugar seguro e não mande para ninguém.\nCom esta chave você recupera a carteira em qualquer aparelho.\n`;
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([texto], { type: 'text/plain' }));
  a.download = 'chave-carteira-portgiga.txt'; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
};
$('bk-anotei').onclick = () => {
  const a = Math.floor(Math.random() * 8), b = 8 + Math.floor(Math.random() * 8);
  conferirIdx = [a, b];
  $('cf-rot-a').textContent = `Grupo ${a + 1}`; $('cf-rot-b').textContent = `Grupo ${b + 1}`;
  $('cf-rot-a').dataset.i = a; $('cf-rot-b').dataset.i = b;
  $('cf-a').value = $('cf-b').value = ''; $('cf-erro').textContent = '';
  irPara('conferir'); setTimeout(() => $('cf-a').focus(), 300);
};
$('cf-a').oninput = () => { if ($('cf-a').value.length === 4) $('cf-b').focus(); };
$('cf-ok').onclick = async () => {
  const grupos = chavePendente.match(/.{4}/g);
  const ok = $('cf-a').value.trim().toLowerCase() === grupos[conferirIdx[0]] && $('cf-b').value.trim().toLowerCase() === grupos[conferirIdx[1]];
  if (!ok) { $('cf-erro').textContent = 'Não confere. Volte e veja a chave de novo.'; vibrar([60, 40, 60]); return; }
  enderecoPendente = await enderecoNovo((await parDe(chavePendente)).pub);
  abrirPin('criar', pin => salvarCofreEEntrar(chavePendente, enderecoPendente, pin));
};
$('bv-importar').onclick = () => { $('im-chave').value = ''; $('im-erro').textContent = ''; contarImportar(); irPara('importar'); };
function contarImportar() {
  const n = $('im-chave').value.replace(/\s/g, '').length;
  $('im-contador').textContent = `${n} / 64`;
  $('im-contador').classList.toggle('ok', n === 64 && /^[0-9a-f]{64}$/i.test($('im-chave').value.replace(/\s/g, '')));
}
$('im-chave').oninput = contarImportar;
// abrir o arquivo da chave (o .txt salvo pelo app ou o "Minha carteira Portgiga.txt"): pega a primeira sequência de 64 hex
$('im-arquivo').onclick = () => $('im-arquivo-input').click();
$('im-arquivo-input').onchange = async () => {
  const f = $('im-arquivo-input').files[0]; $('im-arquivo-input').value = '';
  if (!f) return;
  const txt = f.size < 100000 ? await f.text() : '';
  const m = txt.match(/(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])/i);
  if (!m) { $('im-erro').textContent = 'Não achei uma chave de 64 caracteres nesse arquivo.'; return; }
  $('im-chave').value = m[0].toLowerCase(); $('im-erro').textContent = ''; contarImportar(); vibrar(10);
};
$('im-ok').onclick = async () => {
  const ch = $('im-chave').value.trim().toLowerCase().replace(/\s/g, '');
  if (!/^[0-9a-f]{64}$/.test(ch)) { $('im-erro').textContent = 'A chave tem 64 caracteres, só de 0 a 9 e de a até f.'; return; }
  $('im-ok').disabled = true; $('im-erro').textContent = '';
  try { enderecoPendente = await enderecoDaChave(ch); } catch (e) { $('im-erro').textContent = 'Sem conexão com o servidor da Portgiga.'; $('im-ok').disabled = false; return; }
  $('im-ok').disabled = false; chavePendente = ch;
  abrirPin('criar', pin => salvarCofreEEntrar(ch, enderecoPendente, pin));
};
$('bv-conta').onclick = () => { $('ct-erro').textContent = ''; irPara('conta'); };
$('ct-ok').onclick = async () => {
  const usuario = $('ct-usuario').value.trim(), senha = $('ct-senha').value;
  if (!usuario || !senha) { $('ct-erro').textContent = 'Preencha usuário e senha.'; return; }
  $('ct-ok').disabled = true; $('ct-erro').textContent = ''; $('ct-ok').textContent = 'Entrando…';
  let r;
  try { r = await api('/api/entrar', { usuario, senha: await senhaLogin(usuario, senha) }); }
  catch (e) { r = { ok: false, erro: 'Sem conexão com o servidor da Portgiga.' }; }
  let ch = null;
  if (r.ok) { try { ch = await destrancar(r.chave_cifrada, senha); } catch (e) { r = { ok: false, erro: 'Não foi possível abrir a carteira com esta senha.' }; } }
  $('ct-ok').disabled = false; $('ct-ok').textContent = 'Entrar';
  if (!r.ok) { $('ct-erro').textContent = r.erro || 'Não foi possível entrar.'; return; }
  fetch('/api/sair', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {});   // o app não precisa da sessão
  $('ct-senha').value = '';
  chavePendente = ch; enderecoPendente = r.carteira;
  abrirPin('criar', pin => salvarCofreEEntrar(ch, r.carteira, pin));
};

// ------------------------------------------------------------------ início
let temporizador = null, relogio = null, maiorBlocoVisto = null, avisouAtivacao = false;
function entrarNaCarteira() {
  ocultar = !!preferencias.ocultarAoAbrir;
  $('in-end-txt').textContent = curto(cofre.endereco);
  $('in-end-curto').textContent = curto(cofre.endereco);
  $('in-movs').innerHTML = '<li class="carregando"></li><li class="carregando"></li><li class="carregando"></li>';
  aplicarOcultar();
  irAba('inicio');
  atualizar();
  clearInterval(temporizador);
  temporizador = setInterval(() => { if (!document.hidden && ABAS.includes(telaAtual) && chave) atualizar(); }, ATUALIZAR_MS);
  clearInterval(relogio);
  relogio = setInterval(tique, 1000);
}
function pendentes() {
  return (guardar.ler('pendentes') || []).filter(x => Date.now() - x.quando < 10 * 60 * 1000);
}
function definirStatus(on, txt) {
  ['in-status', 'rd-status'].forEach(id => {
    $(id).classList.toggle('on', on); $(id).classList.toggle('off', !on);
    $(id).querySelector('span').textContent = txt;
  });
}
let atualizando = false;
async function atualizar(manual) {
  if (!cofre || atualizando) return;
  atualizando = true;
  if (manual) $('in-atualizar').classList.add('girando');
  let d, e;
  try { [d, e] = await Promise.all([api('/api/carteira?endereco=' + cofre.endereco), api('/api/estado')]); }
  catch (err) {
    atualizando = false; $('in-atualizar').classList.remove('girando');
    if (manual) aviso('Sem conexão com o servidor', 'erro');
    definirStatus(false, 'offline'); $('in-manutencao').hidden = false; return;
  }
  atualizando = false;
  setTimeout(() => $('in-atualizar').classList.remove('girando'), 400);
  if (d.erro) { aviso(d.erro, 'erro'); return; }
  dados = d; rede = e;
  definirStatus(true, 'online'); $('in-manutencao').hidden = true;
  registrarChegada();
  if (e && e.proximo_bloco_s !== undefined) proximoBlocoEm = Date.now() + e.proximo_bloco_s * 1000;
  // pendentes que já entraram num bloco saem da lista
  let pend = pendentes();
  const enviados = d.movimentos.filter(m => m.tipo === 'enviado');
  pend = pend.filter(p => !enviados.some(m => m.outro === p.para && Math.abs(m.valor - p.valor) < 1e-9 && m.quando * 1000 >= p.quando - 5000));
  guardar.gravar('pendentes', pend);
  desenharInicio();
  if (telaAtual === 'atividade') desenharAtividade();
  if (telaAtual === 'rede') desenharRede();
  // aviso de PTG recebido enquanto o app está aberto
  const blocos = d.movimentos.map(m => m.bloco);
  const maior = blocos.length ? Math.max(...blocos) : 0;
  if (maiorBlocoVisto !== null && maior > maiorBlocoVisto) {
    d.movimentos.filter(m => m.bloco > maiorBlocoVisto && m.tipo === 'recebido').forEach(m => { aviso(`Você recebeu ${moeda(m.valor)} PTG de ${nomeDe(m.outro)}`); vibrar([30, 60, 30]); if (window.Giga) Giga.comemora(`Você recebeu ${moeda(m.valor)} PTG! 🎉`); });
  }
  maiorBlocoVisto = maior;
  if (d.ativada === false && !avisouAtivacao) { avisouAtivacao = true; aviso('Carteira antiga ainda não ativada: abra uma vez pelo painel do PC.', 'erro'); }
}
function saldoHtml(v) {
  const [int, dec] = moeda2(v).split(',');
  return `${int}<span class="dec">,${dec}</span>`;
}
// saldo no fim de cada hora das últimas 24 h (do mais antigo ao atual)
function serieSaldo() {
  const r = dados && dados.resumo_24h;
  const s = [Number(dados ? dados.saldo : 0)];
  if (!r || !r.variacao_por_hora) return null;
  for (let i = 23; i >= 0; i--) s.unshift(Math.max(0, s[0] - r.variacao_por_hora[i]));
  return s;
}
function desenharGrafico(serie) {
  const g = $('in-grafico');
  if (!serie) { g.innerHTML = ''; return; }
  const min = Math.min(...serie), max = Math.max(...serie), faixa = max - min;
  const pts = serie.map((v, i) => [i * 290 / (serie.length - 1), faixa < 1e-9 ? 40 : 62 - (v - min) / faixa * 52]);
  // curva suave
  let d = `M${pts[0][0]},${pts[0][1]}`;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], cx = (x0 + x1) / 2;
    d += ` C${cx},${y0} ${cx},${y1} ${x1},${y1}`;
  }
  const [ux, uy] = pts[pts.length - 1];
  g.innerHTML = `<path class="area" d="${d} L290,70 L0,70 Z"/><path class="linha-g" d="${d}"/><circle class="ponto" cx="${ux}" cy="${uy}" r="4"/>`;
}
function desenharInicio() {
  if (!dados) return;
  const d = dados, r = d.resumo_24h || {};
  $('in-saldo').innerHTML = ocultar ? OCULTO : saldoHtml(d.saldo);
  const bloqueado = Number(d.saldo) - Number(d.saldo_livre);
  $('in-livre').textContent = bloqueado > 1e-9 && !ocultar ? `${moeda(d.saldo_livre)} disponível · ${moeda(bloqueado)} saindo no próximo bloco` : '';
  const serie = serieSaldo();
  desenharGrafico(serie);
  const pil = $('in-var');
  if (serie) {
    const delta = serie[serie.length - 1] - serie[0], pct = serie[0] > 0 ? delta / serie[0] * 100 : null;
    pil.className = 'pilula' + (Math.abs(delta) < 1e-9 ? ' zero' : delta < 0 ? ' neg' : '');
    pil.textContent = ocultar ? '••••' : `${delta >= 0 ? '+' : '−'}${compacto(Math.abs(delta))} PTG` + (pct !== null && Math.abs(delta) > 1e-9 ? ` · ${delta >= 0 ? '+' : '−'}${Math.abs(pct).toLocaleString('pt-BR', { maximumFractionDigits: pct > 100 ? 0 : 1 })}%` : '');
  }
  const v = x => ocultar ? '••••' : compacto(x || 0);
  $('st-minerado').textContent = v(r.minerado);
  $('at-saldo').textContent = ocultar ? OCULTO : moeda2(d.saldo) + ' PTG';
  $('at-minerado').textContent = ocultar ? '' : `+${compacto(r.minerado || 0)} hoje`;
  $('st-blocos').textContent = r.blocos_minerados !== undefined ? r.blocos_minerados.toLocaleString('pt-BR') : '—';
  $('st-recebido').textContent = v(r.recebido);
  $('st-enviado').textContent = v(r.enviado);
  $('in-respostas').textContent = Number(d.respostas_no_bloco_atual || 0).toLocaleString('pt-BR', { maximumFractionDigits: 2 });
  if (rede) $('in-cm-txt').textContent = `Bloco #${(rede.altura + 1).toLocaleString('pt-BR')} · ${rede.mineradores_online} mineradores`;
  const itens = agrupar(d.movimentos, pendentes(), 'todos').slice(0, 5);
  $('in-movs').innerHTML = itens.map(itemHtml).join('') ||
    `<li class="vazio">${ic('i-moedas')}Nenhuma movimentação ainda.<br>Toque em <b>Receber</b> para mostrar o seu QR code, ou minere pelo site.</li>`;
  tique();
}
function aplicarOcultar() {
  $('in-olho').innerHTML = ic(ocultar ? 'i-olho-off' : 'i-olho');
  $('in-olho').setAttribute('aria-label', ocultar ? 'Mostrar saldo' : 'Esconder saldo');
  $('in-cartao').classList.toggle('oculto', ocultar);
}
$('in-olho').onclick = () => { ocultar = !ocultar; vibrar(8); aplicarOcultar(); desenharInicio(); };
$('in-atualizar').onclick = () => { vibrar(10); atualizar(true); };
$('in-endereco').onclick = () => copiar(cofre.endereco, 'Endereço copiado');
$('in-ver-tudo').onclick = () => irAba('atividade');
$('in-ativo').onclick = () => irAba('atividade');
$('in-mineracao').onclick = abrirMineracao;
$('in-contatos').onclick = () => abrirContatos();

// relógio de 1 s: contagem do próximo bloco (início e rede)
const CIRC_MINI = 2 * Math.PI * 19, CIRC_GRANDE = 2 * Math.PI * 52;
let pediuNovoBloco = false;
function tique() {
  if (!rede) return;
  const total = rede.bloco_s || 60;
  const falta = Math.max(0, Math.ceil((proximoBlocoEm - Date.now()) / 1000));
  const frac = 1 - Math.min(1, falta / total);
  $('in-anel-s').textContent = falta + 's';
  $('in-anel-prog').style.strokeDasharray = CIRC_MINI; $('in-anel-prog').style.strokeDashoffset = CIRC_MINI * (1 - frac);
  $('rd-seg').textContent = falta;
  $('rd-anel-prog').style.strokeDasharray = CIRC_GRANDE; $('rd-anel-prog').style.strokeDashoffset = CIRC_GRANDE * (1 - frac);
  if (falta === 0 && !pediuNovoBloco && chave && !document.hidden) {
    pediuNovoBloco = true;
    setTimeout(() => { pediuNovoBloco = false; atualizar(); }, 2500);
  }
}

// ------------------------------------------------------------------ lista de movimentações
// recompensas de mineração seguidas (no mesmo dia) viram um item só, para não esconder as transferências
function agrupar(movs, pend, filtro) {
  const itens = [];
  if (filtro === 'todos' || filtro === 'enviado') pend.forEach(p => itens.push({ tipo: 'pendente', ...p }));
  for (const m of movs) {
    if (filtro !== 'todos' && m.tipo !== filtro) continue;
    const ult = itens[itens.length - 1];
    if (m.tipo === 'mineracao' && ult && ult.tipo === 'grupo' && diaTxt(ult.ate) === diaTxt(m.quando)) {
      ult.blocos.push(m); ult.total += m.valor; ult.de = m.quando;
    } else if (m.tipo === 'mineracao') {
      itens.push({ tipo: 'grupo', blocos: [m], total: m.valor, ate: m.quando, de: m.quando, quando: m.quando });
    } else itens.push(m);
  }
  return itens;
}
let itensMostrados = new Map(), proxId = 0;
function itemHtml(it) {
  const id = ++proxId; itensMostrados.set(String(id), it);
  const val = x => ocultar ? '••••' : moeda2(x);
  if (it.tipo === 'pendente')
    return `<li class="mov pendente" data-item="${id}"><span class="ic">${ic('i-relogio')}</span><div class="meio"><div class="t">Enviando para ${esc(nomeDe(it.para))}</div><div class="q">aguardando o próximo bloco</div></div><div class="v">−${val(it.valor)}<small>PTG</small></div></li>`;
  if (it.tipo === 'grupo') {
    const n = it.blocos.length;
    return `<li class="mov mineracao" data-item="${id}"><span class="ic">${ic('i-picareta')}</span><div class="meio"><div class="t">Mineração</div><div class="q">${n === 1 ? 'bloco #' + it.blocos[0].bloco : n + ' blocos'} · ${n === 1 ? quandoTxt(it.ate) : hora(it.de) + '–' + hora(it.ate)}</div></div><div class="v">+${val(it.total)}<small>PTG</small></div></li>`;
  }
  const rec = it.tipo === 'recebido';
  return `<li class="mov ${rec ? 'recebido' : 'enviado'}" data-item="${id}"><span class="ic">${ic(rec ? 'i-receber' : 'i-enviar')}</span><div class="meio"><div class="t">${rec ? 'Recebido de' : 'Enviado para'} ${esc(nomeDe(it.outro))}</div><div class="q">${quandoTxt(it.quando)} · bloco #${it.bloco}</div></div><div class="v">${rec ? '+' : '−'}${val(it.valor)}<small>PTG</small></div></li>`;
}
function aoTocarItem(ev) {
  const li = ev.target.closest('[data-item]');
  if (!li) return;
  const it = itensMostrados.get(li.dataset.item);
  if (it) abrirDetalhe(it);
}
$('in-movs').onclick = aoTocarItem;
$('at-lista').onclick = aoTocarItem;

let filtroAtividade = 'todos';
$('at-filtros').onclick = ev => {
  const b = ev.target.closest('[data-f]'); if (!b) return;
  filtroAtividade = b.dataset.f; vibrar(6);
  document.querySelectorAll('#at-filtros .chip').forEach(c => c.classList.toggle('ativo', c === b));
  desenharAtividade();
};
function desenharAtividade() {
  if (!dados) { $('at-lista').innerHTML = '<ul class="movs"><li class="carregando"></li><li class="carregando"></li></ul>'; return; }
  const itens = agrupar(dados.movimentos, pendentes(), filtroAtividade);
  if (!itens.length) {
    const txt = { todos: 'Nenhuma movimentação ainda.', recebido: 'Você ainda não recebeu PTG de ninguém.', enviado: 'Você ainda não enviou PTG.', mineracao: 'Nenhuma recompensa de mineração recente.' }[filtroAtividade];
    $('at-lista').innerHTML = `<ul class="movs"><li class="vazio">${ic('i-lista')}${txt}</li></ul>`; return;
  }
  let html = '', diaAtual = null, aberto = false;
  for (const it of itens) {
    const dia = it.tipo === 'pendente' ? 'Agora' : diaTxt(it.quando);
    if (dia !== diaAtual) { if (aberto) html += '</ul>'; html += `<p class="dia">${esc(dia)}</p><ul class="movs">`; diaAtual = dia; aberto = true; }
    html += itemHtml(it);
  }
  $('at-lista').innerHTML = html + (aberto ? '</ul>' : '') + '<p class="mini" style="margin-top:14px">Mostrando as transferências e as recompensas mais recentes.</p>';
}

// ------------------------------------------------------------------ detalhes (transação, mineração, bloco)
const linha = (rot, valor) => `<div class="resumo-linha"><span>${rot}</span><b>${valor}</b></div>`;
const copiavel = (txt, mostrar) => `<span class="copiavel mono" data-copiar="${esc(txt)}">${esc(mostrar || txt)}${ic('i-copiar')}</span>`;
const confirmacoes = bloco => rede ? Math.max(1, rede.altura - bloco + 1) : null;
$('dt-corpo').onclick = ev => {
  const c = ev.target.closest('[data-copiar]'); if (c) { copiar(c.dataset.copiar, 'Copiado'); return; }
  const b = ev.target.closest('[data-bloco]'); if (b) { abrirBloco(Number(b.dataset.bloco)); return; }
};
function abrirDetalhe(it) {
  let html = '';
  if (it.tipo === 'pendente') {
    $('dt-titulo').textContent = 'Envio pendente';
    html = `<div class="det-cab"><span class="ic enviado">${ic('i-relogio')}</span><div class="v">−${moeda(it.valor)}<small>PTG</small></div><div class="s">para ${esc(nomeDe(it.para))}</div></div>
      <div class="resumo">${linha('Status', '<span class="estado-pend">aguardando o bloco</span>')}${linha('Assinado', quandoTxt(it.quando / 1000))}${linha('Para', copiavel(it.para))}</div>`;
  } else if (it.tipo === 'grupo') {
    const n = it.blocos.length;
    $('dt-titulo').textContent = 'Mineração';
    html = `<div class="det-cab"><span class="ic mineracao">${ic('i-picareta')}</span><div class="v">+${moeda(it.total)}<small>PTG</small></div><div class="s">${n} ${n === 1 ? 'bloco minerado' : 'blocos minerados'}</div></div>
      <div class="resumo">${linha('Período', n === 1 ? dataCompleta(it.ate) : `${hora(it.de)} até ${hora(it.ate)} · ${diaTxt(it.ate).toLowerCase()}`)}${linha('Média por bloco', moeda(it.total / n) + ' PTG')}${linha('Status', '<span class="estado-ok">confirmado</span>')}</div>
      <ul class="det-lista">${it.blocos.map(b => `<li data-bloco="${b.bloco}"><span>Bloco <b class="mono">#${b.bloco}</b> · ${hora(b.quando)}</span><b class="verde-txt">+${moeda(b.valor)}</b></li>`).join('')}</ul>`;
  } else {
    const rec = it.tipo === 'recebido', conf = confirmacoes(it.bloco), ctt = contatoDe(it.outro);
    $('dt-titulo').textContent = rec ? 'PTG recebido' : 'PTG enviado';
    html = `<div class="det-cab"><span class="ic ${it.tipo}">${ic(rec ? 'i-receber' : 'i-enviar')}</span><div class="v">${rec ? '+' : '−'}${moeda(it.valor)}<small>PTG</small></div><div class="s">${rec ? 'de' : 'para'} ${esc(nomeDe(it.outro))}</div></div>
      <div class="resumo">
        ${linha('Status', `<span class="estado-ok">confirmada${conf ? ` · ${conf.toLocaleString('pt-BR')} ${conf === 1 ? 'confirmação' : 'confirmações'}` : ''}</span>`)}
        ${linha('Data', dataCompleta(it.quando))}
        ${linha(rec ? 'De' : 'Para', copiavel(it.outro))}
        ${linha('Bloco', `<span class="ligacao mono" data-bloco="${it.bloco}">#${it.bloco} ›</span>`)}
        ${linha('Taxa', '<span class="verde-txt">grátis</span>')}
        ${linha('Assinatura', '<span class="mono" id="dt-assin">carregando…</span>')}
      </div>
      <div class="det-acoes">${ctt ? '' : `<button class="btn sec peq" id="dt-salvar">${ic('i-mais')}Salvar contato</button>`}<button class="btn peq" id="dt-enviar">${ic('i-enviar')}${rec ? 'Enviar para' : 'Enviar de novo'}</button></div>`;
    // a assinatura Ed25519 fica gravada no bloco
    api(`/api/cadeia?desde=${it.bloco}&ate=${it.bloco}`).then(c => {
      const t = ((c.blocos[0] || {}).dados || {}).transferencias || [];
      const achada = t.find(x => (rec ? x.de === it.outro && x.para === cofre.endereco : x.de === cofre.endereco && x.para === it.outro) && Math.abs(x.valor - it.valor) < 1e-9);
      const el = $('dt-assin'); if (!el) return;
      el.outerHTML = achada ? copiavel(achada.assinatura, achada.assinatura.slice(0, 10) + '…' + achada.assinatura.slice(-8)) : '<span>—</span>';
    }).catch(() => { const el = $('dt-assin'); if (el) el.textContent = '—'; });
  }
  $('dt-corpo').innerHTML = html;
  if ($('dt-salvar')) $('dt-salvar').onclick = () => editarContato(null, it.outro);
  if ($('dt-enviar')) $('dt-enviar').onclick = () => abrirEnvio(it.outro, rec2(it) ? null : it.valor);
  abrirFolha('f-detalhe');
}
const rec2 = it => it.tipo === 'recebido';
async function abrirBloco(altura) {
  $('dt-titulo').textContent = `Bloco #${altura.toLocaleString('pt-BR')}`;
  $('dt-corpo').innerHTML = '<div class="det-cab"><span class="ic bloco">' + ic('i-cubo') + '</span><div class="s">carregando…</div></div>';
  abrirFolha('f-detalhe');
  let c;
  try { c = await api(`/api/cadeia?desde=${altura}&ate=${altura}`); } catch (e) { $('dt-corpo').innerHTML = '<p class="erro">Sem conexão com o servidor.</p>'; return; }
  const b = c.blocos[0]; if (!b) return;
  const rec = b.dados.recompensas || {}, tr = b.dados.transferencias || [];
  const emissao = Object.values(rec).reduce((a, v) => a + v, 0);
  const conf = Math.max(1, c.altura_atual - altura + 1);
  const meu = e => e === cofre.endereco ? '<b class="verde-txt">você</b>' : esc(nomeDe(e));
  $('dt-corpo').innerHTML = `<div class="det-cab"><span class="ic bloco">${ic('i-cubo')}</span><div class="v mono">#${altura.toLocaleString('pt-BR')}</div><div class="s">${dataCompleta(b.quando)}</div></div>
    <div class="resumo">
      ${linha('Confirmações', `<span class="estado-ok">${conf.toLocaleString('pt-BR')}</span>`)}
      ${linha('Hash', copiavel(b.hash, b.hash.slice(0, 12) + '…' + b.hash.slice(-8)))}
      ${linha('Anterior', copiavel(b.anterior, b.anterior.slice(0, 12) + '…' + b.anterior.slice(-8)))}
      ${linha('Recompensa', moeda(emissao) + ' PTG')}
      ${linha('Mineradores', Object.keys(rec).length)}
      ${linha('Transferências', tr.length)}
    </div>
    ${tr.length ? `<p class="grupo-titulo" style="margin:16px 6px 0">Transferências</p><ul class="det-lista">${tr.map(t => `<li><span>${meu(t.de)} → ${meu(t.para)}</span><b>${moeda(t.valor)}</b></li>`).join('')}</ul>` : ''}
    <p class="grupo-titulo" style="margin:16px 6px 0">Recompensas</p>
    <ul class="det-lista">${Object.entries(rec).sort((a, b) => b[1] - a[1]).map(([e, v]) => `<li><span class="mono">${meu(e)}</span><b>${moeda(v)}</b></li>`).join('')}</ul>`;
}

// ------------------------------------------------------------------ rede
let ultimoBlocoDesenhado = null;
function desenharRede() {
  if (!rede) return;
  const e = rede;
  $('rd-altura').textContent = '#' + e.altura.toLocaleString('pt-BR');
  $('rd-intervalo').textContent = (e.bloco_s || 60) + ' s';
  $('rd-online').textContent = e.mineradores_online;
  $('rd-hps').textContent = velocidade(e.hps_total);
  $('rd-emissao').textContent = moeda2(e.emissao_por_bloco) + ' PTG';
  $('rd-respostas').textContent = Number(e.respostas_no_bloco_atual || 0).toLocaleString('pt-BR', { maximumFractionDigits: 1 });
  const pct = e.oferta_max ? e.emitido / e.oferta_max * 100 : 0;
  $('rd-barra').style.width = Math.max(0.6, pct) + '%';
  $('rd-emitido-pct').textContent = pct.toLocaleString('pt-BR', { maximumFractionDigits: 3 }) + '% emitido';
  $('rd-emitido').textContent = compacto(e.emitido) + ' PTG emitidos';
  $('rd-max').textContent = 'máx. ' + compacto(e.oferta_max);
  const blocos = (e.blocos || []).slice(0, 12);
  $('rd-blocos').innerHTML = blocos.map(b => `<li data-bloco="${b.altura}" class="${ultimoBlocoDesenhado !== null && b.altura > ultimoBlocoDesenhado ? 'novo-bloco' : ''}"><span class="bl-ic">${ic('i-cubo')}</span><div class="meio"><div class="t mono">#${b.altura.toLocaleString('pt-BR')}</div><div class="q mono">${b.hash.slice(0, 16)}…</div></div><div class="d">${b.recompensas} ${b.recompensas === 1 ? 'minerador' : 'mineradores'}${b.transferencias ? ` · ${b.transferencias} tx` : ''}<br><span class="peq" style="color:var(--txt-3)">${haQuanto(b.quando)}</span></div></li>`).join('');
  if (blocos.length) ultimoBlocoDesenhado = blocos[0].altura;
  tique();
}
$('rd-blocos').onclick = ev => { const li = ev.target.closest('[data-bloco]'); if (li) abrirBloco(Number(li.dataset.bloco)); };

// ------------------------------------------------------------------ receber
function textoQr() {
  const u = unidadesDe($('rc-valor').value);
  return 'portgiga:' + cofre.endereco + (u ? '?valor=' + (u / UNIDADE) : '');
}
function desenharQr() {
  const u = unidadesDe($('rc-valor').value);
  $('rc-valor-txt').hidden = !u;
  if (u) $('rc-valor-txt').innerHTML = `${moeda(u / UNIDADE)}<small>PTG</small>`;
  window.QRCode.toDataURL(textoQr(), { errorCorrectionLevel: 'H', margin: 1, width: 480, color: { dark: '#062b18', light: '#ffffff' } }, (err, url) => {
    if (!err) $('rc-qr').innerHTML = `<img alt="QR code do endereço" src="${url}">`;
  });
}
$('in-receber').onclick = () => {
  const e = cofre.endereco;
  $('rc-end').textContent = e;
  $('rc-valor').value = '';
  desenharQr(); abrirFolha('f-receber');
};
$('rc-valor').oninput = desenharQr;
$('rc-copiar').onclick = () => copiar(cofre.endereco, 'Endereço copiado');
$('rc-compartilhar').onclick = async () => {
  const u = unidadesDe($('rc-valor').value);
  const texto = `Meu endereço Portgiga (PTG): ${cofre.endereco}` + (u ? `\nValor: ${moeda(u / UNIDADE)} PTG` : '');
  if (NATIVO && NATIVO.compartilhar) NATIVO.compartilhar(texto);
  else if (navigator.share) { try { await navigator.share({ title: 'Carteira Portgiga', text: texto }); } catch (e) {} }
  else copiar(texto, 'Endereço copiado para compartilhar');
};

// ------------------------------------------------------------------ enviar
let envio = null;
function mostrarPasso(n) { [1, 2, 3].forEach(i => { $('ev-passo' + i).hidden = i !== n; }); $('ev-titulo').textContent = n === 1 ? 'Enviar PTG' : n === 2 ? 'Revisar envio' : ''; }
function recentes() {
  const lista = [];
  const add = (endereco, nome) => { if (endereco && endereco !== cofre.endereco && !lista.some(x => x.endereco === endereco)) lista.push({ endereco, nome }); };
  contatos.forEach(c => add(c.endereco, c.nome));
  (dados ? dados.movimentos : []).filter(m => m.outro).forEach(m => add(m.outro, nomeDe(m.outro)));
  return lista.slice(0, 10);
}
function mostrarNomeDestino() {
  const p = $('ev-para').value.trim().toLowerCase(), c = contatoDe(p);
  $('ev-para-nome').textContent = c ? '✓ ' + c.nome : '';
}
function abrirEnvio(para, valor) {
  mostrarPasso(1);
  $('ev-para').value = para || ''; $('ev-valor').value = valor ? String(valor).replace('.', ',') : ''; $('ev-erro').textContent = '';
  $('ev-disp').textContent = dados ? moeda(dados.saldo_livre) + ' PTG' : '—';
  $('ev-recentes').innerHTML = recentes().map(r => `<button class="recente" data-end="${esc(r.endereco)}"><span class="avatar">${avatar(r.endereco)}</span><span>${esc(r.nome)}</span></button>`).join('');
  mostrarNomeDestino();
  abrirFolha('f-enviar');
}
$('ev-recentes').onclick = ev => { const b = ev.target.closest('[data-end]'); if (b) { $('ev-para').value = b.dataset.end; mostrarNomeDestino(); vibrar(6); } };
$('ev-para').oninput = () => {
  const lido = lerQr($('ev-para').value);   // colar "portgiga:ptg1…?valor=" também funciona
  if (lido && $('ev-para').value.includes(':')) { $('ev-para').value = lido.para; if (lido.valor) $('ev-valor').value = String(lido.valor).replace('.', ','); }
  mostrarNomeDestino();
};
$('in-enviar').onclick = () => abrirEnvio();
document.querySelectorAll('.porcentagens [data-pct]').forEach(b => b.onclick = () => {
  if (!dados) return;
  const livre = Math.round(Number(dados.saldo_livre) * UNIDADE);
  const u = Math.floor(livre * Number(b.dataset.pct) / 100);
  $('ev-valor').value = u ? valorCampo(u) : ''; vibrar(6);
});
$('ev-continuar').onclick = () => {
  const para = $('ev-para').value.trim().toLowerCase(), unidades = unidadesDe($('ev-valor').value);
  let erro = '';
  if (!/^ptg1[0-9a-f]{38}$/.test(para)) erro = 'Endereço inválido. Ele começa com ptg1 e tem 42 caracteres.';
  else if (para === cofre.endereco) erro = 'Esse é o seu próprio endereço.';
  else if (!unidades) erro = 'Valor inválido (até 8 casas depois da vírgula).';
  else if (dados && unidades > Math.round(Number(dados.saldo_livre) * UNIDADE)) erro = 'Saldo insuficiente.';
  if (erro) { $('ev-erro').textContent = erro; vibrar([60, 40, 60]); return; }
  envio = { para, unidades };
  $('ev-r-valor').textContent = moeda(unidades / UNIDADE);
  $('ev-r-para').textContent = para;
  $('ev-r-nome').textContent = nomeDe(para);
  $('ev-r-av-para').innerHTML = avatar(para);
  $('ev-r-depois').textContent = dados ? moeda((Math.round(Number(dados.saldo_livre) * UNIDADE) - unidades) / UNIDADE) + ' PTG' : '—';
  mostrarPasso(2);
};
$('ev-voltar').onclick = () => mostrarPasso(1);
$('ev-confirmar').onclick = () => {
  fecharFolha();
  abrirPin('envio', async aberta => {
    let r;
    try {
      const de = cofre.endereco;
      const nonce = (await api('/api/carteira?endereco=' + de)).proximo_nonce;
      const msg = 'portgiga-tx:' + JSON.stringify([de, envio.para, envio.unidades, nonce]);
      r = await api('/api/enviar', { de, para: envio.para, unidades: envio.unidades, nonce, ...(await assinar(aberta, msg)) });
    } catch (e) { r = { ok: false, erro: 'Sem conexão com o servidor da Portgiga.' }; }
    voltar();
    if (!r.ok) { abrirEnvio(envio.para, envio.unidades / UNIDADE); $('ev-erro').textContent = r.erro; vibrar([60, 40, 60]); return; }
    const pend = pendentes(); pend.unshift({ para: envio.para, valor: envio.unidades / UNIDADE, quando: Date.now() }); guardar.gravar('pendentes', pend);
    vibrar([20, 50, 40]);
    abrirFolha('f-enviar'); mostrarPasso(3);
    $('ev-ok-sub').textContent = `${moeda(envio.unidades / UNIDADE)} PTG para ${nomeDe(envio.para)}. Entra no próximo bloco, em até 1 minuto.`;
    $('ev-ok-contato').hidden = !!contatoDe(envio.para);
    const destino = envio.para;
    $('ev-ok-contato').onclick = () => editarContato(null, destino);
    atualizar();
  });
};

// ------------------------------------------------------------------ escanear QR (onde o celular deixa)
let fluxo = null, laco = null;
const podeEscanear = 'BarcodeDetector' in window && !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
function lerQr(texto) {
  const m = String(texto).trim().match(/(?:portgiga:)?(ptg1[0-9a-f]{38})(?:\?valor=([0-9.]+))?/i);
  return m ? { para: m[1].toLowerCase(), valor: m[2] ? Number(m[2]) : null } : null;
}
async function abrirCamera() {
  if (!podeEscanear) { aviso('Este celular não lê QR pelo app. Use a câmera do celular e cole o endereço.', 'erro'); return; }
  abrirFolha('f-escanear'); $('sc-msg').textContent = 'Aponte para o QR code de uma carteira Portgiga.';
  try {
    fluxo = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
    const v = $('sc-video'); v.srcObject = fluxo; await v.play();
    const detector = new window.BarcodeDetector({ formats: ['qr_code'] });
    laco = setInterval(async () => {
      try {
        const achados = await detector.detect(v);
        const lido = achados.length && lerQr(achados[0].rawValue);
        if (lido) { vibrar(30); fecharFolha(); abrirEnvio(lido.para, lido.valor); }
        else if (achados.length) $('sc-msg').textContent = 'Esse QR não é de uma carteira Portgiga.';
      } catch (e) {}
    }, 300);
  } catch (e) { $('sc-msg').textContent = 'Não foi possível abrir a câmera. Permita o acesso nas configurações.'; }
}
function pararCamera() {
  clearInterval(laco); laco = null;
  if (fluxo) { fluxo.getTracks().forEach(t => t.stop()); fluxo = null; }
}
$('in-escanear').onclick = abrirCamera;
$('ev-escanear').onclick = () => { fecharFolha(); abrirCamera(); };
if (!podeEscanear) {
  // sem leitor de QR no navegador (ex.: Safari do iPhone): o terceiro botão copia o endereço
  $('ev-escanear').hidden = true;
  $('in-escanear').innerHTML = `<span>${ic('i-copiar')}</span>Copiar`;
  $('in-escanear').onclick = () => copiar(cofre.endereco, 'Endereço copiado');
}

// ------------------------------------------------------------------ contatos
function abrirContatos() {
  desenharContatos();
  if (telaAtual !== 'contatos') irPara('contatos');
}
function desenharContatos() {
  const lista = [...contatos].sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'));
  $('ctt-lista').innerHTML = lista.map(c => `<li data-end="${esc(c.endereco)}"><span class="avatar">${avatar(c.endereco)}</span><div class="meio"><b>${esc(c.nome)}</b><small class="mono">${curto(c.endereco)}</small></div><button class="icone" data-enviar="${esc(c.endereco)}" aria-label="Enviar">${ic('i-enviar')}</button></li>`).join('');
  const vazio = document.querySelector('[data-tela="contatos"] .vazio-ctt');
  if (!lista.length && !vazio) $('ctt-lista').insertAdjacentHTML('afterend', `<div class="vazio vazio-ctt">${ic('i-pessoas')}Nenhum contato ainda.<br>Salve os endereços que você usa para enviar mais rápido.<br><br><button class="btn peq" id="ctt-novo2">${ic('i-mais')}Adicionar contato</button></div>`);
  if (lista.length && vazio) vazio.remove();
  if ($('ctt-novo2')) $('ctt-novo2').onclick = () => editarContato();
}
$('ctt-lista').onclick = ev => {
  const env = ev.target.closest('[data-enviar]'); if (env) { abrirEnvio(env.dataset.enviar); return; }
  const li = ev.target.closest('[data-end]'); if (li) editarContato(contatoDe(li.dataset.end));
};
$('ctt-novo').onclick = () => editarContato();
let contatoEditando = null;
function editarContato(c, endereco) {
  contatoEditando = c || null;
  $('cn-titulo').textContent = c ? 'Editar contato' : 'Novo contato';
  $('cn-nome').value = c ? c.nome : ''; $('cn-end').value = c ? c.endereco : (endereco || '');
  $('cn-erro').textContent = ''; $('cn-apagar').hidden = !c;
  abrirFolha('f-contato');
  setTimeout(() => $('cn-nome').focus(), 350);
}
$('cn-salvar').onclick = () => {
  const nome = $('cn-nome').value.trim(), e = $('cn-end').value.trim().toLowerCase();
  if (!nome) { $('cn-erro').textContent = 'Dê um nome ao contato.'; return; }
  if (!/^ptg1[0-9a-f]{38}$/.test(e)) { $('cn-erro').textContent = 'Endereço inválido. Ele começa com ptg1 e tem 42 caracteres.'; return; }
  if (e === cofre.endereco) { $('cn-erro').textContent = 'Esse é o seu próprio endereço.'; return; }
  contatos = contatos.filter(x => x.endereco !== e && x !== contatoEditando);
  contatos.push({ nome, endereco: e });
  salvarContatos(); fecharFolha(); aviso('Contato salvo');
  if (telaAtual === 'contatos') desenharContatos();
  desenharInicio(); if (telaAtual === 'atividade') desenharAtividade();
};
// trocar o endereço do servidor à mão (app instalado): cole o link https://….trycloudflare.com
document.addEventListener('click', ev => {
  if (!REMOTO || !ev.target.closest('#aj-servidor-item')) return;
  const novo = prompt('Endereço do servidor da Portgiga (o link https que aparece no PC):', servidor);
  if (novo === null) return;
  const u = normalizarServidor(novo);
  if (!/^https?:\/\/[^\s/]+$/.test(u)) { aviso('Endereço inválido', 'erro'); return; }
  servidor = u; guardar.gravar('servidor', u); lembrarServidor(u);
  prepararAjustes(); atualizar(true);
});
$('cn-apagar').onclick = () => {
  if (!contatoEditando || !confirm(`Apagar o contato "${contatoEditando.nome}"?`)) return;
  contatos = contatos.filter(x => x !== contatoEditando); salvarContatos(); fecharFolha(); desenharContatos(); desenharInicio();
};

// ------------------------------------------------------------------ ajustes
const OPCOES_TRANCA = [[1, '1 minuto'], [5, '5 minutos'], [15, '15 minutos'], [60, '1 hora']];
function prepararAjustes() {
  if (!cofre) return;
  $('aj-end').textContent = cofre.endereco;
  $('aj-servidor').textContent = REMOTO ? (servidor.replace(/^https?:\/\//, '') || 'procurando…') : location.host;
  $('aj-tranca-txt').textContent = 'depois de ' + (OPCOES_TRANCA.find(o => o[0] === preferencias.trancarMin) || [0, preferencias.trancarMin + ' min'])[1] + ' fora do app';
  $('aj-contatos-txt').textContent = contatos.length ? `${contatos.length} ${contatos.length === 1 ? 'salvo' : 'salvos'}` : 'nenhum ainda';
  $('aj-ocultar').checked = !!preferencias.ocultarAoAbrir;
}
$('in-ajustes').onclick = () => irAba('ajustes');
$('aj-copiar-end').onclick = () => copiar(cofre.endereco, 'Endereço copiado');
$('aj-contatos').onclick = () => abrirContatos();
$('aj-ocultar').onchange = () => { preferencias.ocultarAoAbrir = $('aj-ocultar').checked; salvarPreferencias(); };
$('aj-tranca-auto').onclick = () => {
  $('op-titulo').textContent = 'Trancar sozinho';
  $('op-lista').innerHTML = OPCOES_TRANCA.map(([m, t]) => `<button class="item ${m === preferencias.trancarMin ? 'marcado' : ''}" data-min="${m}"><div><b>${t}</b><span class="peq">fora do app</span></div></button>`).join('');
  abrirFolha('f-opcoes');
};
$('op-lista').onclick = ev => {
  const b = ev.target.closest('[data-min]'); if (!b) return;
  preferencias.trancarMin = Number(b.dataset.min); salvarPreferencias(); fecharFolha(); prepararAjustes(); vibrar(8);
};
$('aj-ver-chave').onclick = () => abrirPin('ver-chave', aberta => {
  desenharGrade($('vc-chave'), aberta);
  irPara('ver-chave', { trocar: true });
  $('vc-copiar').onclick = () => copiar(aberta, 'Chave copiada. Apague da área de transferência depois de guardar.');
});
$('aj-pin').onclick = () => abrirPin('trocar-atual', aberta => {
  abrirPin('criar', async pin => {
    cofre.cifrada = await trancar(aberta, pin); guardar.gravar('cofre', cofre);
    irAba('ajustes'); aviso('PIN trocado');
  });
  $('pin-titulo-topo').textContent = '';
});
$('aj-trancar').onclick = () => trancarApp();
$('aj-apagar').onclick = () => {
  if (!confirm('Apagar a carteira deste celular? O saldo continua na rede, mas para voltar você vai precisar da chave secreta anotada.')) return;
  if (!confirm('Tem certeza? Sem a chave anotada, o saldo fica perdido para sempre.')) return;
  guardar.gravar('cofre', null); guardar.gravar('pendentes', null); guardar.gravar('tentativas', null);
  cofre = null; chave = null; dados = null; rede = null; pilha.length = 0;
  clearInterval(temporizador); clearInterval(relogio);
  irPara('boas-vindas', { trocar: true }); aviso('Carteira apagada deste celular');
};

// ------------------------------------------------------------------ trancar
let escondidoDesde = null;
function trancarApp() {
  chave = null; fecharFolha(); clearInterval(temporizador); clearInterval(relogio); pilha.length = 0;
  abrirPin('abrir', aberta => { chave = aberta; entrarNaCarteira(); });
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) { escondidoDesde = Date.now(); return; }
  if (chave && escondidoDesde && Date.now() - escondidoDesde > preferencias.trancarMin * 60000) trancarApp();
  else if (chave && ABAS.includes(telaAtual)) atualizar();
  escondidoDesde = null;
});

// ------------------------------------------------------------------ instalar na tela inicial
let pedidoInstalar = null;
const instalado = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
window.addEventListener('beforeinstallprompt', ev => {   // Android (Chrome): botão "Instalar"
  ev.preventDefault(); pedidoInstalar = ev;
  $('bv-instalar').hidden = false; $('aj-instalar').hidden = false;
});
async function instalar() {
  if (!pedidoInstalar) return;
  pedidoInstalar.prompt();
  const r = await pedidoInstalar.userChoice.catch(() => null);
  if (r && r.outcome === 'accepted') { aviso('App instalado!'); $('bv-instalar').hidden = true; $('aj-instalar').hidden = true; }
  pedidoInstalar = null;
}
$('bv-instalar').onclick = instalar;
$('aj-instalar').onclick = instalar;
if (!instalado() && /iphone|ipad|ipod/i.test(navigator.userAgent)) $('bv-ios').hidden = false;   // iPhone: instrução
if (!EMBUTIDO && /android/i.test(navigator.userAgent)) { $('bv-apk').hidden = false; $('aj-apk').hidden = false; }   // Android: o app de verdade

// ------------------------------------------------------------------ começo

// ------------------------------------------------------------------ presente de boas-vindas e convites
// O link pode trazer a origem (?o=tiktok) e o convite de um amigo (?convite=ptg1...). Guardamos o primeiro que chegou.
const LINK_PUBLICO = HOSPEDADO ? location.origin : 'https://portgiga.pages.dev';
(function () {
  let q; try { q = new URLSearchParams(location.search); } catch (e) { return; }
  const o = (q.get('o') || '').toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 20);
  const convite = /^ptg1[0-9a-f]{38}$/.test(q.get('convite') || '') ? q.get('convite') : null;
  let salvo = null; try { salvo = JSON.parse(localStorage.getItem('ptg-chegada')); } catch (e) {}
  if (!salvo && (o || convite)) try { localStorage.setItem('ptg-chegada', JSON.stringify({ o: o || (convite ? 'convite' : ''), convite })); } catch (e) {}
})();
let presente = null, chegadaFeita = false;
async function registrarChegada() {
  if (chegadaFeita || !cofre) return;
  chegadaFeita = true;
  let c = {}; try { c = JSON.parse(localStorage.getItem('ptg-chegada')) || {}; } catch (e) {}
  try {
    const r = await api('/api/chegada', { endereco: cofre.endereco, origem: c.o || (EMBUTIDO ? 'apk' : 'direto'), convite: c.convite || null });
    if (r && r.ok) { presente = r; desenharPresente(); } else chegadaFeita = false;
  } catch (e) { chegadaFeita = false; }
}
async function atualizarPresente() {
  if (!cofre) return;
  try { const r = await api('/api/boas-vindas?endereco=' + cofre.endereco); if (r && r.ok) { const ganhouAgora = presente && !presente.ganhou && r.ganhou; presente = r; desenharPresente(); if (ganhouAgora && window.Giga) Giga.comemora(`Você ganhou ${r.bonus_ptg} PTG de boas-vindas! 🎁`); } } catch (e) {}
}
function desenharPresente() {
  const b = $('in-presente');
  if (!presente) { b.hidden = true; return; }
  b.hidden = false;
  if (presente.pode_ganhar && presente.ativo) {
    $('in-presente-ic').textContent = '🎁';
    $('in-presente-t').textContent = `Ganhe ${presente.bonus_ptg} PTG de boas-vindas`;
    $('in-presente-q').textContent = 'Toque aqui e minere 1 minuto neste celular';
    b.onclick = abrirMineracao;
  } else {
    $('in-presente-ic').textContent = '🤝';
    $('in-presente-t').textContent = presente.ativo ? `Convide amigos: +${presente.bonus_convite_ptg} PTG por amigo` : 'Convide amigos para a Portgiga';
    $('in-presente-q').textContent = presente.convites_pagos ? `${presente.convites_pagos} ${presente.convites_pagos === 1 ? 'amigo já chegou' : 'amigos já chegaram'}` : 'Mande seu link no WhatsApp';
    b.onclick = abrirConvite;
  }
  const mp = $('mn-presente');
  if (presente.pode_ganhar && presente.ativo) { mp.hidden = false; mp.textContent = `🎁 Na sua primeira resposta aceita você ganha ${presente.bonus_ptg} PTG de presente (chega no próximo bloco).`; }
  else if (presente.ganhou) { mp.hidden = false; mp.textContent = '🎁 Presente de boas-vindas recebido! Agora é só minerar e convidar amigos.'; }
  else mp.hidden = true;
}
function linkConvite() { return `${LINK_PUBLICO}/?convite=${cofre.endereco}`; }
function abrirConvite() {
  const p = presente || {};
  $('cv-txt').textContent = p.ativo
    ? `Cada amigo que criar a carteira e minerar pela primeira vez ganha ${p.bonus_ptg} PTG, e você ganha ${p.bonus_convite_ptg} PTG.`
    : 'Mande seu link para os amigos conhecerem a Portgiga.';
  $('cv-amigos').textContent = p.convites_pagos || 0;
  $('cv-ganho').textContent = p.ganho_convites || 0;
  $('cv-link').textContent = linkConvite();
  abrirFolha('f-convite');
}
$('cv-copiar').onclick = () => copiar(linkConvite(), 'Link de convite copiado');
$('cv-compartilhar').onclick = async () => {
  const texto = `Conhece a Portgiga? É uma moeda digital brasileira que você minera no celular, de graça. Crie sua carteira por este link e ganhe PTG de boas-vindas (não é investimento, é pra brincar e trocar): ${linkConvite()}`;
  if (NATIVO && NATIVO.compartilhar) NATIVO.compartilhar(texto);
  else if (navigator.share) { try { await navigator.share({ title: 'Portgiga', text: texto }); } catch (e) {} }
  else window.open('https://wa.me/?text=' + encodeURIComponent(texto), '_blank', 'noopener');
};

// ------------------------------------------------------------------ minerar neste aparelho
let mnWorker = null, mnLigado = false, mnAceitas = 0;
const mnId = guardar.ler('id_aparelho') || ('app-' + Math.random().toString(36).slice(2, 8));
guardar.gravar('id_aparelho', mnId);
function abrirMineracao() { abrirFolha('f-minerar'); desenharPresente(); atualizarPresente(); }
function mnSit(t, erro) { $('mn-sit').textContent = t; $('mn-sit').classList.toggle('erro', !!erro); }
function mnProgresso(f) { $('mn-prog').style.strokeDashoffset = (326.7 * (1 - f)).toFixed(1); }
async function mnCiclo() {
  while (mnLigado) {
    let tr;
    try { tr = await api(`/api/trabalho?carteira=${cofre.endereco}&aparelho=celular&id=${mnId}&nome=${encodeURIComponent('Carteira ' + mnId.slice(-4))}`); }
    catch (e) { mnSit('Sem conexão com a rede. Tentando de novo…', true); await new Promise(r => setTimeout(r, 5000)); continue; }
    if (!mnLigado) return;
    if (tr.erro) { mnSit(tr.erro, true); mnParar(); return; }
    mnSit('Procurando o número certo…'); mnProgresso(0);
    const res = await new Promise(ok => {
      mnWorker.onmessage = ev => { if (ev.data.fim) ok(ev.data); else { $('mn-vel').textContent = velocidade(Math.round(ev.data.hps)); mnProgresso(ev.data.n / tr.max); } };
      mnWorker.postMessage(tr);
    });
    if (!mnLigado) return;
    if (res.nonce < 0) continue;
    mnProgresso(1);
    const r = await api(`/api/resultado?trabalho=${tr.trabalho}&nonce=${res.nonce}`).catch(() => ({}));
    if (r.aceito) {
      mnAceitas++; $('mn-ok').textContent = mnAceitas; vibrar(20);
      $('mn-vel').textContent = velocidade(r.hps);
      $('mn-bloco').textContent = r.proximo_bloco_s + ' s';
      mnSit('Resposta aceita! Sua parte entra no próximo bloco.');
      if (window.Giga) Giga.ganho('aceita ✓');
      if (mnAceitas === 1) setTimeout(atualizarPresente, 1500);
    } else mnSit('Resposta recusada: ' + (r.erro || 'tente de novo'), true);
  }
}
function mnParar() {
  mnLigado = false;
  if (mnWorker) { mnWorker.terminate(); mnWorker = null; }
  if (window.Giga) Giga.minera(false);
  $('mn-botao').classList.remove('parar'); $('mn-botao').querySelector('span').textContent = 'Começar a minerar';
  $('mn-painel').classList.remove('minerando'); $('mn-vel').textContent = '—'; mnProgresso(0);
  if (mnAceitas) atualizar();
}
$('mn-botao').onclick = () => {
  if (mnLigado) { mnParar(); mnSit('Parado. Toque em Começar para minerar de novo.'); return; }
  if (!cofre) return;
  try { mnWorker = new Worker('/app/minerador.js'); } catch (e) { mnSit('Este navegador não consegue minerar. Tente pelo Chrome.', true); return; }
  mnLigado = true;
  if (window.Giga) Giga.minera(true);
  $('mn-botao').classList.add('parar'); $('mn-botao').querySelector('span').textContent = 'Parar';
  $('mn-painel').classList.add('minerando');
  mnCiclo();
};

(async function () {
  if (EMBUTIDO) $('bk-baixar').hidden = true;   // o WebView não salva arquivos; o botão Copiar continua
  if (REMOTO) {
    $('aj-servidor').closest('.item').classList.add('seta');
    descobrirServidor();
  }
  if (!EMBUTIDO && 'serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(() => {});
  if (!(await criptoFunciona())) {
    irPara('boas-vindas', { trocar: true });
    ['bv-criar', 'bv-conta', 'bv-importar'].forEach(id => { $(id).disabled = true; });
    const p = document.createElement('p'); p.className = 'erro'; p.style.textAlign = 'center';
    p.textContent = 'Este navegador não consegue proteger a carteira. Abra pelo link https da Portgiga num navegador atualizado (Chrome ou Safari).';
    document.querySelector('[data-tela="boas-vindas"] .rodape').prepend(p);
    return;
  }
  if (cofre) abrirPin('abrir', aberta => { chave = aberta; entrarNaCarteira(); });
  else irPara('boas-vindas', { trocar: true });
})();
/* GIGA-INICIO (gerado por divulgacao/mascote/widget.py) */
(function () {
  if (window.Giga) return;
  const CSS = ".gg-w{position:fixed;right:14px;bottom:14px;width:116px;z-index:9000;cursor:pointer;user-select:none;-webkit-tap-highlight-color:transparent;filter:drop-shadow(0 8px 14px rgba(0,0,0,.35))}.gg-w svg{display:block;width:100%;height:auto;overflow:visible}.gg-w.gg-oculto{display:none}.gg-x{position:absolute;right:-4px;top:4px;width:24px;height:24px;border-radius:50%;border:0;background:rgba(0,0,0,.55);color:#fff;font:700 14px/24px sans-serif;cursor:pointer;opacity:0;transition:opacity .2s}.gg-w:hover .gg-x{opacity:1}.gg-volta{position:fixed;right:14px;bottom:14px;width:46px;height:46px;border-radius:50%;border:0;z-index:9000;cursor:pointer;display:none;background:radial-gradient(circle at 35% 30%,#fff1b8,#f6c94e 45%,#c98a12 80%);box-shadow:0 6px 14px rgba(0,0,0,.4);font:900 13px sans-serif;color:#7a4c06}.gg-volta.gg-on{display:block}.gg-fala{position:absolute;right:8px;bottom:100%;margin-bottom:6px;max-width:230px;width:max-content;background:#fff;color:#1b1b1b;font:800 14px/1.3 Outfit,system-ui,sans-serif;padding:9px 13px;border-radius:14px;box-shadow:0 8px 20px rgba(0,0,0,.35);opacity:0;transform:translateY(8px) scale(.9);transform-origin:90% 100%;transition:opacity .2s,transform .25s cubic-bezier(.3,1.6,.5,1);pointer-events:none}.gg-fala::after{content:'';position:absolute;right:30px;bottom:-9px;border:9px solid transparent;border-top-color:#fff;border-bottom:0}.gg-fala.gg-on{opacity:1;transform:none}.gg-w #gg-giga{transform-box:view-box;transform-origin:200px 490px;animation:gg-respira 1.6s ease-in-out infinite}.gg-w #gg-sombra{transform-box:view-box;transform-origin:200px 492px;animation:gg-sombra 1.6s ease-in-out infinite}.gg-w #gg-olhoE,.gg-w #gg-olhoD{transform-box:fill-box;transform-origin:center;animation:gg-pisca 4.2s infinite}.gg-w #gg-folhaE{transform-box:view-box;transform-origin:200px 128px;animation:gg-folhaE 2.4s ease-in-out infinite}.gg-w #gg-folhaD{transform-box:view-box;transform-origin:202px 112px;animation:gg-folhaD 2.4s ease-in-out infinite}.gg-w #gg-bracoD,.gg-w #gg-bracoE{transform-box:view-box}.gg-w #gg-bracoD{transform-origin:320px 300px}.gg-w #gg-bracoE{transform-origin:80px 300px}.gg-w #gg-corpo,.gg-w #gg-rosto,.gg-w #gg-broto{transform-box:view-box;transform-origin:200px 300px}.gg-w #gg-copa{transform-box:view-box;transform-origin:200px 150px;transition:opacity .6s,transform .8s cubic-bezier(.3,1.6,.5,1);transform:scale(.2)}.gg-w.gg-arvore #gg-copa{opacity:1!important;transform:translateY(-40px) scale(1.35)}.gg-w.gg-arvore #gg-folhaE,.gg-w.gg-arvore #gg-folhaD{opacity:0;transition:opacity .4s}.gg-w .gg-pupila{transition:transform .3s}.gg-w.gg-pulando #gg-giga{animation:gg-pulo .7s cubic-bezier(.3,.7,.4,1)}.gg-w.gg-festa #gg-giga{animation:gg-festa 1.3s ease-in-out}.gg-w.gg-festa #gg-bracoE{animation:gg-maosE 1.3s ease-in-out}.gg-w.gg-festa #gg-bracoD{animation:gg-maosD 1.3s ease-in-out}.gg-w.gg-festa #gg-boca,.gg-w.gg-minerando #gg-boca{transform-box:view-box;transform-origin:200px 330px}.gg-w.gg-festa #gg-boca{transform:scaleY(1.25)}.gg-w.gg-minerando #gg-picareta,.gg-w.gg-minerando #gg-pedra{opacity:1!important}.gg-w.gg-minerando #gg-bracoD{animation:gg-bate .75s cubic-bezier(.5,0,.8,.4) infinite}.gg-w.gg-minerando #gg-boca{transform:scaleY(.6)}.gg-w.gg-gira #gg-corpo,.gg-w.gg-gira #gg-rosto,.gg-w.gg-gira #gg-broto{animation:gg-gira .9s ease-in-out}.gg-conf{position:fixed;width:9px;height:13px;border-radius:2px;z-index:9001;pointer-events:none;animation:gg-conf 1.5s cubic-bezier(.2,.6,.4,1) forwards}.gg-ganho{position:fixed;z-index:9001;pointer-events:none;font:900 18px Outfit,system-ui,sans-serif;color:#fbbf24;text-shadow:0 2px 0 #7a4c06;animation:gg-sobe 1.2s ease-out forwards;white-space:nowrap}@keyframes gg-respira{0%,100%{transform:translateY(0) scale(1.03,.97)}50%{transform:translateY(-14px) scale(.98,1.02)}}@keyframes gg-sombra{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(.84);opacity:.7}}@keyframes gg-pisca{0%,93%,100%{transform:scaleY(1)}96%{transform:scaleY(.1)}}@keyframes gg-folhaE{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(7deg)}}@keyframes gg-folhaD{0%,100%{transform:rotate(5deg)}50%{transform:rotate(-6deg)}}@keyframes gg-pulo{0%{transform:scale(1.1,.85)}35%{transform:translateY(-120px) scale(.92,1.1)}70%{transform:translateY(0) scale(1.12,.86)}100%{transform:scale(1)}}@keyframes gg-festa{0%{transform:scale(1.12,.84)}25%{transform:translateY(-150px) rotate(0)}55%{transform:translateY(-150px) rotate(360deg)}80%{transform:translateY(0) rotate(360deg) scale(1.12,.86)}100%{transform:rotate(360deg)}}@keyframes gg-maosE{0%,100%{transform:rotate(0)}20%,80%{transform:rotate(150deg)}}@keyframes gg-maosD{0%,100%{transform:rotate(0)}20%,80%{transform:rotate(-150deg)}}@keyframes gg-bate{0%{transform:rotate(-70deg)}70%{transform:rotate(28deg)}100%{transform:rotate(-70deg)}}@keyframes gg-gira{0%,100%{transform:scaleX(1)}15%,45%,75%{transform:scaleX(.06)}30%,60%,90%{transform:scaleX(1)}}@keyframes gg-conf{to{transform:translate(var(--dx),var(--dy)) rotate(var(--r));opacity:0}}@keyframes gg-sobe{0%{transform:translateY(0) scale(.6);opacity:1}100%{transform:translateY(-70px) scale(1.1);opacity:0}}@media (max-width:560px){.gg-w{width:84px;right:8px;bottom:8px}.gg-fala{font-size:13px;max-width:190px}}@media (prefers-reduced-motion:reduce){.gg-w *{animation:none!important}}", SVG = "<defs> <radialGradient id=\"gg-gOuro\" cx=\"38%\" cy=\"32%\" r=\"75%\"> <stop offset=\"0\" stop-color=\"#fff4c2\"/><stop offset=\".42\" stop-color=\"#f7cd52\"/><stop offset=\".78\" stop-color=\"#d4951a\"/><stop offset=\"1\" stop-color=\"#f3d175\"/> </radialGradient> <linearGradient id=\"gg-gBorda\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\"><stop offset=\"0\" stop-color=\"#b9780f\"/><stop offset=\"1\" stop-color=\"#7a4c06\"/></linearGradient> <linearGradient id=\"gg-gFolha\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\"><stop offset=\"0\" stop-color=\"#4fbf72\"/><stop offset=\"1\" stop-color=\"#1f7a45\"/></linearGradient> <radialGradient id=\"gg-gSombra\"><stop offset=\"0\" stop-color=\"#000\" stop-opacity=\".35\"/><stop offset=\"1\" stop-color=\"#000\" stop-opacity=\"0\"/></radialGradient> </defs> <ellipse id=\"gg-sombra\" cx=\"200\" cy=\"492\" rx=\"120\" ry=\"16\" fill=\"url(#gg-gSombra)\"/> <g id=\"gg-giga\"> <g id=\"gg-pedra\" opacity=\"0\"> <path d=\"M318 490 L332 452 L362 440 L392 456 L398 490 Z\" fill=\"#6b7280\"/><path d=\"M332 452 L362 440 L372 462 L345 470 Z\" fill=\"#9ca3af\"/> <g fill=\"#f7cd52\"><circle cx=\"352\" cy=\"478\" r=\"5\"/><circle cx=\"378\" cy=\"470\" r=\"4\"/><circle cx=\"366\" cy=\"484\" r=\"3\"/></g> </g> <g id=\"gg-pernas\"> <path d=\"M168 412 Q164 446 158 470\" stroke=\"#8a5a08\" stroke-width=\"11\" fill=\"none\" stroke-linecap=\"round\"/> <path d=\"M232 412 Q236 446 242 470\" stroke=\"#8a5a08\" stroke-width=\"11\" fill=\"none\" stroke-linecap=\"round\"/> <g id=\"gg-peE\"><ellipse cx=\"150\" cy=\"478\" rx=\"30\" ry=\"15\" fill=\"#16a34a\"/><rect x=\"120\" y=\"482\" width=\"60\" height=\"7\" rx=\"3.5\" fill=\"#f8fafc\"/><path d=\"M140 470 L156 474 M144 465 L159 469\" stroke=\"#facc15\" stroke-width=\"3\" stroke-linecap=\"round\"/></g> <g id=\"gg-peD\"><ellipse cx=\"250\" cy=\"478\" rx=\"30\" ry=\"15\" fill=\"#16a34a\"/><rect x=\"220\" y=\"482\" width=\"60\" height=\"7\" rx=\"3.5\" fill=\"#f8fafc\"/><path d=\"M244 474 L260 470 M241 469 L256 465\" stroke=\"#facc15\" stroke-width=\"3\" stroke-linecap=\"round\"/></g> </g> <g id=\"gg-bracoE\"> <path d=\"M80 300 Q52 320 44 356\" stroke=\"#8a5a08\" stroke-width=\"11\" fill=\"none\" stroke-linecap=\"round\"/> <circle cx=\"42\" cy=\"362\" r=\"17\" fill=\"#fff\"/><path d=\"M30 356 Q42 350 54 356\" stroke=\"#e5e7eb\" stroke-width=\"3\" fill=\"none\"/> </g> <g id=\"gg-bracoD\"> <g id=\"gg-picareta\" opacity=\"0\"> <path d=\"M358 362 L420 250\" stroke=\"#7c4a1e\" stroke-width=\"10\" stroke-linecap=\"round\"/> <path d=\"M372 236 Q420 228 456 262 Q424 244 394 250 Z\" fill=\"#9ca3af\" stroke=\"#4b5563\" stroke-width=\"3\"/> <path d=\"M372 236 Q392 238 404 262\" stroke=\"#4b5563\" stroke-width=\"3\" fill=\"none\"/> </g> <path d=\"M320 300 Q348 320 356 356\" stroke=\"#8a5a08\" stroke-width=\"11\" fill=\"none\" stroke-linecap=\"round\"/> <circle cx=\"358\" cy=\"362\" r=\"17\" fill=\"#fff\"/><path d=\"M346 356 Q358 350 370 356\" stroke=\"#e5e7eb\" stroke-width=\"3\" fill=\"none\"/> </g> <g id=\"gg-corpo\"> <ellipse cx=\"200\" cy=\"312\" rx=\"132\" ry=\"132\" fill=\"url(#gg-gBorda)\"/> <circle cx=\"200\" cy=\"300\" r=\"132\" fill=\"url(#gg-gOuro)\"/> <circle cx=\"200\" cy=\"300\" r=\"118\" fill=\"none\" stroke=\"#c98a12\" stroke-width=\"5\"/> <circle cx=\"200\" cy=\"300\" r=\"111\" fill=\"none\" stroke=\"#fff3bf\" stroke-width=\"2\" stroke-dasharray=\"3 7\" opacity=\".9\"/> <path id=\"gg-brilho\" d=\"M112 236 Q140 186 196 176\" stroke=\"#fffbe6\" stroke-width=\"12\" fill=\"none\" stroke-linecap=\"round\" opacity=\".75\"/> <text x=\"200\" y=\"398\" text-anchor=\"middle\" font-family=\"Outfit, Arial Black, sans-serif\" font-weight=\"900\" font-size=\"24\" letter-spacing=\"4\" fill=\"#a16207\" opacity=\".75\">PTG</text> </g> <g id=\"gg-rosto\"> <g id=\"gg-sobrancelhas\" fill=\"none\" stroke=\"#5b3a06\" stroke-width=\"7\" stroke-linecap=\"round\"> <path id=\"gg-sbE\" d=\"M136 244 Q158 232 180 242\"/><path id=\"gg-sbD\" d=\"M220 242 Q242 232 264 244\"/> </g> <g id=\"gg-olhoE\"><ellipse cx=\"160\" cy=\"284\" rx=\"25\" ry=\"31\" fill=\"#fff\"/><g class=\"gg-pupila\"><circle cx=\"164\" cy=\"290\" r=\"14\" fill=\"#1f2937\"/><circle cx=\"169\" cy=\"284\" r=\"5\" fill=\"#fff\"/></g></g> <g id=\"gg-olhoD\"><ellipse cx=\"240\" cy=\"284\" rx=\"25\" ry=\"31\" fill=\"#fff\"/><g class=\"gg-pupila\"><circle cx=\"244\" cy=\"290\" r=\"14\" fill=\"#1f2937\"/><circle cx=\"249\" cy=\"284\" r=\"5\" fill=\"#fff\"/></g></g> <ellipse cx=\"126\" cy=\"330\" rx=\"18\" ry=\"11\" fill=\"#f97316\" opacity=\".35\"/><ellipse cx=\"274\" cy=\"330\" rx=\"18\" ry=\"11\" fill=\"#f97316\" opacity=\".35\"/> <g id=\"gg-boca\"> <path id=\"gg-bocaAberta\" d=\"M168 330 Q200 376 232 330 Q200 344 168 330 Z\" fill=\"#7c2d12\"/> <path id=\"gg-lingua\" d=\"M184 350 Q200 362 216 350 Q200 344 184 350 Z\" fill=\"#ef4444\"/> </g> </g> <g id=\"gg-broto\"> <g id=\"gg-copa\" opacity=\"0\"> <circle cx=\"200\" cy=\"96\" r=\"44\" fill=\"url(#gg-gFolha)\"/><circle cx=\"160\" cy=\"118\" r=\"32\" fill=\"url(#gg-gFolha)\"/><circle cx=\"240\" cy=\"118\" r=\"32\" fill=\"url(#gg-gFolha)\"/> <g id=\"gg-flores\" fill=\"#f5c518\"><circle cx=\"186\" cy=\"80\" r=\"7\"/><circle cx=\"216\" cy=\"74\" r=\"7\"/><circle cx=\"230\" cy=\"108\" r=\"7\"/><circle cx=\"168\" cy=\"112\" r=\"7\"/><circle cx=\"202\" cy=\"104\" r=\"7\"/></g> </g> <path id=\"gg-caule\" d=\"M200 172 Q194 140 202 104\" stroke=\"#b3261e\" stroke-width=\"10\" fill=\"none\" stroke-linecap=\"round\"/> <g id=\"gg-folhaE\"><path d=\"M200 128 Q168 96 140 112 Q164 140 200 128 Z\" fill=\"url(#gg-gFolha)\"/><path d=\"M196 126 Q170 116 150 114\" stroke=\"#a7f3d0\" stroke-width=\"2.5\" fill=\"none\"/></g> <g id=\"gg-folhaD\"><path d=\"M202 112 Q228 70 262 84 Q244 118 202 112 Z\" fill=\"url(#gg-gFolha)\"/><path d=\"M206 110 Q232 96 252 88\" stroke=\"#a7f3d0\" stroke-width=\"2.5\" fill=\"none\"/></g> </g> </g>", DICAS = ["Oi! Eu sou o Giga, a moeda-broto da Portgiga! 🌱", "Cada bloco sai a cada 1 minuto. Bora minerar? ⛏️", "Vão existir só pouco mais de 21 milhões de PTG. Nem uma a mais. 🪙", "Guarde sua chave secreta num lugar seguro. Ela é a sua carteira! 🔑", "Nunca mande sua chave ou PIN pra ninguém. Nem pra mim! 🙅", "PTG é troca de pessoa pra pessoa. Não é investimento. 🤝", "Meu broto cresce quando a rede cresce. 🌳", "Tem uma plaquinha ESP32? Ela também minera! 🔌"];
  const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
  const w = document.createElement('div'); w.className = 'gg-w'; w.setAttribute('role', 'button'); w.setAttribute('aria-label', 'Giga, mascote da Portgiga');
  w.innerHTML = '<div class="gg-fala"></div><button class="gg-x" aria-label="Esconder o Giga">×</button><svg viewBox="0 0 400 520" aria-hidden="true">' + SVG + '</svg>';
  const volta = document.createElement('button'); volta.className = 'gg-volta'; volta.textContent = 'PTG'; volta.setAttribute('aria-label', 'Mostrar o Giga');
  const ler = k => { try { return localStorage.getItem(k); } catch (e) { return null; } };
  const gravar = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
  function pronto() {
    document.body.appendChild(w); document.body.appendChild(volta);
    if (ler('giga-oculto') === '1') { w.classList.add('gg-oculto'); volta.classList.add('gg-on'); }
  }
  if (document.body) pronto(); else document.addEventListener('DOMContentLoaded', pronto);
  const fala = w.querySelector('.gg-fala');
  let tFala = null, iDica = 0, tMina = null;
  function aula(cls, ms) { w.classList.remove(cls); void w.offsetWidth; w.classList.add(cls); setTimeout(() => w.classList.remove(cls), ms); }
  const G = {
    fala(txt, ms = 3200) { fala.textContent = txt; fala.classList.add('gg-on'); clearTimeout(tFala); tFala = setTimeout(() => fala.classList.remove('gg-on'), ms); },
    pula() { aula('gg-pulando', 720); },
    gira() { aula('gg-gira', 920); },
    comemora(txt) {
      aula('gg-festa', 1320); if (txt) G.fala(txt, 3800);
      if (w.classList.contains('gg-oculto')) return;
      const r = w.getBoundingClientRect(), cores = ['#fbbf24', '#22c55e', '#ef4444', '#38bdf8', '#fff'];
      for (let i = 0; i < 34; i++) {
        const c = document.createElement('i'); c.className = 'gg-conf'; c.style.background = cores[i % 5];
        c.style.left = (r.left + r.width / 2) + 'px'; c.style.top = (r.top + r.height * .3) + 'px';
        c.style.setProperty('--dx', (Math.random() * 360 - 300) + 'px'); c.style.setProperty('--dy', (Math.random() * 300 - 260) + 'px'); c.style.setProperty('--r', (Math.random() * 900 - 450) + 'deg');
        document.body.appendChild(c); setTimeout(() => c.remove(), 1600);
      }
    },
    ganho(txt) {
      if (w.classList.contains('gg-oculto')) return;
      const r = w.getBoundingClientRect(), g = document.createElement('div'); g.className = 'gg-ganho'; g.textContent = txt;
      g.style.left = (r.left + r.width * .55) + 'px'; g.style.top = (r.top + r.height * .55) + 'px'; document.body.appendChild(g); setTimeout(() => g.remove(), 1250);
    },
    minera(liga) {
      w.classList.toggle('gg-minerando', !!liga); clearInterval(tMina);
      if (liga) G.fala('Bora minerar! ⛏️', 2500);
    },
    arvore(liga) { w.classList.toggle('gg-arvore', !!liga); },
    dica() { G.fala(DICAS[iDica++ % DICAS.length], 4200); G.pula(); },
  };
  w.addEventListener('click', e => { if (e.target.closest('.gg-x')) return; G.dica(); });
  w.querySelector('.gg-x').addEventListener('click', e => { e.stopPropagation(); w.classList.add('gg-oculto'); volta.classList.add('gg-on'); gravar('giga-oculto', '1'); });
  volta.addEventListener('click', () => { w.classList.remove('gg-oculto'); volta.classList.remove('gg-on'); gravar('giga-oculto', '0'); G.comemora('Voltei! 🌱'); });
  // olhar para os lados de vez em quando
  setInterval(() => { const x = (Math.random() * 12 - 6).toFixed(1), y = (Math.random() * 10 - 5).toFixed(1); w.querySelectorAll('.gg-pupila').forEach(p => p.style.transform = `translate(${x}px,${y}px)`); }, 2600);
  try { if (!sessionStorage.getItem('giga-oi')) { sessionStorage.setItem('giga-oi', '1'); setTimeout(() => { G.pula(); G.fala(DICAS[0], 4200); iDica = 1; }, 1800); } } catch (e) {}
  window.Giga = G;
})();
/* GIGA-FIM */
