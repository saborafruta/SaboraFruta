/**
 * Peso e volume do item do pedido de expedição.
 *
 * A conta é Alpine puro, então a suíte Django não a alcança — o que ela cobre
 * é o endpoint que alimenta a tela (peso unitário, quantidade por embalagem e
 * preço). Este roteiro cobre o resto: dada a quantidade digitada, o peso é o
 * peso unitário multiplicado por ela, e o volume é a quantidade dividida pelo
 * que cabe na caixa, ARREDONDADO PARA CIMA — caixa pela metade ocupa uma
 * caixa no caminhão.
 *
 * SEM CADASTRO NÃO SE INVENTA: produto sem peso ou sem embalagem deixa o
 * campo como está, para quem lança preencher à mão. Número inventado aqui
 * vira divergência na balança da doca.
 *
 * Como rodar:
 *
 *     node apps/logistica/tests/item_expedicao_calculado.test.mjs
 */
import assert from 'node:assert';
import fs from 'node:fs';
import path from 'node:path';

const TEMPLATE = path.join(
  'apps', 'logistica', 'templates', 'logistica', 'pedido_expedicao', 'detail.html',
);

// ── O componente, extraído do template ─────────────────────────────────
const html = fs.readFileSync(TEMPLATE, 'utf8');
const inicio = html.indexOf('function buscaProdutoItem()');
assert.ok(inicio > 0, 'componente buscaProdutoItem não encontrado no template');
const fim = html.indexOf('\n  }', inicio);
const fonte = html
  .slice(inicio, fim + 4)
  // As tags do Django viram ids fixos: o que se testa é a conta, não o render.
  .replace(/\{\{\s*item_form\.(\w+)\.id_for_label\s*\}\}/g, 'id_$1')
  .replace(/\{%[^%]*%\}/g, '');

const componente = new Function(`${fonte}; return buscaProdutoItem();`)();

// ── DOM mínimo: só os campos que a conta toca ──────────────────────────
function montarCampos(quantidade) {
  // `preencher` avisa o resto da tela com um evento -- aqui basta engolir.
  const campo = (valor) => ({ value: valor, dispatchEvent() {} });
  const campos = {
    id_quantidade: campo(String(quantidade)),
    id_peso_kg: campo(''),
    id_volumes: campo(''),
    id_valor_unitario: campo(''),
  };
  globalThis.document = { getElementById: (id) => campos[id] || null };
  globalThis.Event = class { constructor() {} };
  return campos;
}

function recalcular(produto, quantidade) {
  const campos = montarCampos(quantidade);
  componente.escolhido = produto;
  componente.recalcular();
  return campos;
}

// ── O peso é a soma, e não o unitário ──────────────────────────────────
{
  const campos = recalcular({ peso_bruto: 1.2, quantidade_por_embalagem: 12 }, 10);
  assert.strictEqual(campos.id_peso_kg.value, 12, 'peso = 1,2 kg × 10');
}

// ── O volume arredonda para cima ───────────────────────────────────────
{
  const campos = recalcular({ peso_bruto: 1, quantidade_por_embalagem: 12 }, 13);
  assert.strictEqual(campos.id_volumes.value, 2, '13 unidades em caixas de 12 = 2 caixas');
}

{
  const campos = recalcular({ peso_bruto: 1, quantidade_por_embalagem: 12 }, 24);
  assert.strictEqual(campos.id_volumes.value, 2, 'caixa cheia não vira três');
}

// ── Vírgula é o separador que a pessoa digita ──────────────────────────
{
  const campos = recalcular({ peso_bruto: 2, quantidade_por_embalagem: 0 }, '2,5');
  assert.strictEqual(campos.id_peso_kg.value, 5, '2,5 × 2 kg = 5 kg');
}

// ── Sem cadastro, o campo fica como estava ─────────────────────────────
{
  const campos = recalcular({ peso_bruto: null, quantidade_por_embalagem: 0 }, 10);
  assert.strictEqual(campos.id_peso_kg.value, '', 'sem peso cadastrado, não inventa');
  assert.strictEqual(campos.id_volumes.value, '', 'sem embalagem cadastrada, não inventa');
}

// ── Quantidade zerada não zera o que já estava ─────────────────────────
{
  const campos = recalcular({ peso_bruto: 1.2, quantidade_por_embalagem: 12 }, 0);
  assert.strictEqual(campos.id_peso_kg.value, '', 'sem quantidade, nada a somar');
}

console.log('ok — peso e volume do item de expedição');
