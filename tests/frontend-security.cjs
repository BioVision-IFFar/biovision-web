// Executa só as funções de apresentação, sem navegador, rede ou dependências novas.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const output = {
  hidden: true, innerHTML: '', classList: { add() {} },
  querySelector() { return null; }, querySelectorAll() { return []; },
  appendChild() {}, scrollIntoView() {}
};
const body = { classList: { contains() { return false; } }, appendChild() {}, style: {} };
const document = {
  documentElement: { dataset: {} }, body,
  querySelector(selector) { return selector === '#resultado' ? output : null; },
  getElementById(id) { return ['resultado', 'airResultado'].includes(id) ? output : null; },
  addEventListener() {}, querySelectorAll() { return []; },
  createElement() { return { appendChild() {}, addEventListener() {}, setAttribute() {} }; }
};
const context = {
  document, window: { addEventListener() {} }, localStorage: { removeItem() {} },
  setTimeout() {}, setInterval() {}, clearInterval() {}, console
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/static_biovision/js/app.js', 'utf8'), context);

const text = '<img src=x onerror="alert(1)"> & \'texto\'';
const taxon = Object.fromEntries([
  'especie', 'nomes', 'reino', 'filo', 'classe', 'ordem', 'familia', 'genero',
  'habitat', 'morfologia', 'reproducao', 'conservacao',
  'carac_reino', 'carac_filo', 'carac_classe', 'carac_ordem'
].map(key => [key, text]));
const payload = { keyword: text, dados_taxon: taxon, confidence: 99 };

context.exibirResultado(payload);
assert(!output.innerHTML.includes('<img src=x'));
assert(output.innerHTML.includes('&lt;img src=x onerror=&quot;alert(1)&quot;&gt;'));
const detail = context.renderTaxonDetailHtml(payload);
assert(!detail.includes('<img src=x'));
assert(detail.includes('&lt;img src=x'));
assert(context.hasImage('/static_biovision/species/homo_sapiens.jpg'));
assert(!context.hasImage('javascript:alert(1)'));
assert(!context.hasImage('data:text/html,example'));

for (const target of ['boi', 'humano', 'ave']) {
  context.renderAirResult({ alvo_contagem: target, contagens: { [target]: 4, gato: 100 },
    identificados: [{ nome: text, dados_taxon: taxon }] });
  assert(output.innerHTML.includes('<strong>4</strong>'));
  assert(!output.innerHTML.includes('100'));
  assert(!/Taxonomia|Ficha|confiança|Quadros|Habitat|airTaxonDetail|onerror/.test(output.innerHTML));
}
context.renderAirResult({ alvo_contagem: 'humano', contagens: { humano: 0 } });
assert(output.innerHTML.includes('Humanos'));
assert(output.innerHTML.includes('<strong>0</strong>'));
console.log('OK: textos escapados em foto/vídeo/áudio; URLs de imagem restritas; Air exibe somente o animal selecionado e a contagem.');
