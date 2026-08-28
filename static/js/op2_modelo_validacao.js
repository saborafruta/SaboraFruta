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
    const rotulo = campo.replaceAll('_', ' ');
    if (!valorCampo) return `${rotulo}: preenchimento obrigatório. Se não se aplica, selecione N/A.`;
    if (!opcoes.includes(valorCampo)) return `${rotulo}: selecione uma opção válida para ${grupo.label}.`;
  }
  return '';
}

if (typeof module !== 'undefined') module.exports = { validarModeloOp2 };
