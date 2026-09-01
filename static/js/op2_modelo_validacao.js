// Regra compartilhada pelos editores de criação e edição da OP/orçamento.
function validarModeloOp2(draft, grupos) {
  const valor = String(draft.valor_unitario ?? '').trim();
  if (!/^(?:\d+|\d*\.\d{1,2})$/.test(valor) || Number(valor) <= 0 || Number(valor) > 9999999999.99) {
    return 'Valor unitário: informe um valor maior que zero, com até duas casas decimais.';
  }
  const grupo = grupos[draft.estrutura_tipo];
  if (!grupo) return 'Selecione um tipo de peça válido.';
  for (const [campo, opcoes] of Object.entries(grupo.campos)) {
    const valorCampo = campo === 'tipo_impressao' ? draft.tipo_impressao : draft.estrutura?.[campo];
    const valores = Array.isArray(valorCampo) ? valorCampo.filter(Boolean) : (valorCampo ? [valorCampo] : []);
    const rotulo = campo.replaceAll('_', ' ');
    if (!valores.length) return `${rotulo}: preenchimento obrigatório. Se não se aplica, selecione N/A.`;
    if (valores.some(valor => !opcoes.includes(valor))) return `${rotulo}: selecione uma opção válida para ${grupo.label}.`;
    if (valores.length > 1 && valores.includes('N/A')) return `${rotulo}: N/A não pode ser combinado com outra opção.`;
  }
  return '';
}

function op2ListaMultisselecao(valores) {
  return Array.isArray(valores) ? valores.filter(Boolean) : (valores ? [String(valores)] : []);
}

function op2MultisselecaoContem(valores, opcao) {
  return op2ListaMultisselecao(valores).includes(opcao);
}

function op2AlternarMultisselecao(valores, opcao, marcada) {
  let lista = op2ListaMultisselecao(valores).filter(valor => valor !== opcao);
  if (marcada) {
    lista = opcao === 'N/A' ? ['N/A'] : [...lista.filter(valor => valor !== 'N/A'), opcao];
  }
  return lista;
}

function op2ResumoMultisselecao(valores) {
  const lista = op2ListaMultisselecao(valores);
  if (!lista.length) return 'Selecione...';
  if (lista.length === 1) return lista[0];
  return `${lista.length} opções selecionadas`;
}

if (typeof module !== 'undefined') module.exports = {
  validarModeloOp2, op2AlternarMultisselecao, op2ListaMultisselecao,
  op2MultisselecaoContem, op2ResumoMultisselecao,
};
