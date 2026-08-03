/**
 * Autocomplete de municipio do MDF-e.
 *
 * O componente e Alpine puro, entao nao ha como cobri-lo pela suite Django.
 * Este roteiro extrai a funcao do template, monta um DOM minimo e exercita o
 * comportamento que quebrou em producao: ao EDITAR um MDF-e a UF ja vem
 * preenchida do banco, nenhum evento `input` acontece, e a lista de municipios
 * nunca era carregada -- o campo aceitava digitacao mas nunca sugeria nada, e
 * o codigo IBGE (oculto) ficava vazio, derrubando a emissao.
 *
 * Como rodar: extraia o <script> do template para um .js (trocando as tags
 * do Django por literais) e passe o caminho como argumento:
 *
 *     node apps/logistica/tests/autocomplete_municipio.test.mjs /tmp/mdfe.js
 */
import fs from 'fs';

const js = fs.readFileSync(process.argv[2], 'utf8');

// DOM minimo: so os campos que o componente toca.
function montarAmbiente(ufValor) {
  const campos = {
    uf_carregamento: { value: ufValor },
    municipio_carregamento: { value: '' },
    codigo_municipio_carregamento: { value: '' },
  };
  globalThis.document = {
    querySelector: (sel) => campos[sel.replace(/\[name="|"\]/g, '')] || null,
  };
  globalThis.fetch = async () => ({
    json: async () => ([
      { id: 2408102, nome: 'Natal' },
      { id: 2403251, nome: 'Parnamirim' },
      { id: 2404200, nome: 'São Gonçalo do Amarante' },
    ]),
  });
  return campos;
}

globalThis.window = {};
const criar = new Function(js + '; return municipioAutocomplete;')();
const espera = (ms) => new Promise(r => setTimeout(r, ms));
let falhas = 0;
function checar(nome, condicao) {
  console.log((condicao ? '  OK   ' : '  FALHA') + '  ' + nome);
  if (!condicao) falhas++;
}

// --- Cenario da tela: editar MDF-e com a UF ja preenchida, sem digitar nela.
{
  const campos = montarAmbiente('RN');
  const c = criar('municipio_carregamento');
  if (typeof c.init === 'function') c.init();  // codigo antigo nao tem
  await espera(10);

  checar('lista carrega so de abrir a tela (UF ja preenchida)', c._lista.length === 3);
  c.onFocus('');
  checar('focar no campo ja mostra as sugestoes', c.sugestoes.length === 3);
  c.onMunicipio('parna');
  checar('digitar filtra ignorando acento/caixa', c.sugestoes.length === 1
         && c.sugestoes[0].nome === 'Parnamirim');
  c.onMunicipio('sao goncalo');
  checar('"sao goncalo" encontra "São Gonçalo"', c.sugestoes.length === 1);

  c.selecionar(c.sugestoes[0]);
  checar('escolher preenche o codigo IBGE', campos.codigo_municipio_carregamento.value === '2404200');

  // Digitar o nome inteiro, sem clicar na sugestao.
  c.onMunicipio('Natal');
  checar('digitar o nome exato preenche o codigo sozinho',
         campos.codigo_municipio_carregamento.value === '2408102');

  // Trocar o texto depois de escolher nao pode deixar o codigo antigo.
  c.onMunicipio('Nata');
  checar('texto que nao casa limpa o codigo (evita nome x codigo divergentes)',
         campos.codigo_municipio_carregamento.value === '');
}

// --- UF vazia: nao deve buscar nada nem quebrar.
{
  montarAmbiente('');
  const c = criar('municipio_carregamento');
  if (typeof c.init === 'function') c.init();  // codigo antigo nao tem
  await espera(10);
  checar('UF vazia nao busca lista', c._lista.length === 0);
}

console.log(falhas ? `\n${falhas} FALHA(S)` : '\nTODAS AS VERIFICACOES PASSARAM');
process.exit(falhas ? 1 : 0);
