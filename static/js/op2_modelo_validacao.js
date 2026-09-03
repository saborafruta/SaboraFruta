// Regra compartilhada pelos editores de criação e edição da OP/orçamento.
function validarModeloOp2(draft, grupos) {
  const valor = String(draft.valor_unitario ?? '').trim();
  if (!/^(?:\d+|\d*\.\d{1,2})$/.test(valor) || Number(valor) < 0 || Number(valor) > 9999999999.99) {
    return 'Valor unitário: informe zero ou um valor positivo, com até duas casas decimais.';
  }
  const grupo = grupos[draft.estrutura_tipo];
  if (!grupo) return 'Selecione um tipo de peça válido.';
  for (const [campo, opcoes] of Object.entries(grupo.campos)) {
    const valorCampo = campo === 'tipo_impressao' ? draft.tipo_impressao : draft.estrutura?.[campo];
    const valores = op2ListaMultisselecao(valorCampo);
    const rotulo = campo.replaceAll('_', ' ');
    if (!valores.length) return `${rotulo}: preenchimento obrigatório. Se não se aplica, selecione N/A.`;
    if (!op2CampoMultisselecao(campo) && valores.length > 1) return `${rotulo}: selecione somente uma opção.`;
    if (valores.some(valor => !opcoes.includes(valor))) return `${rotulo}: selecione uma opção válida para ${grupo.label}.`;
    if (valores.length > 1 && valores.includes('N/A')) return `${rotulo}: N/A não pode ser combinado com outra opção.`;
    if (valores.includes('OUTRO') && !String(draft.estrutura_outros?.[campo] || '').trim()) {
      return `${rotulo}: descreva a opção Outro.`;
    }
  }
  return '';
}

function op2ListaMultisselecao(valores) {
  const brutos = Array.isArray(valores) ? valores : (valores ? [String(valores)] : []);
  return brutos.flatMap(valor => String(valor).replaceAll(' + ', ',').split(','))
    .map(valor => valor.trim()).filter(Boolean);
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
  return lista.join(', ');
}

function op2CampoMultisselecao(campo) {
  return campo === 'tipo_impressao' || String(campo || '').startsWith('acabamento');
}

function op2NormalizarUidsItens(itens, gerarUid) {
  const usados = new Set();
  return (Array.isArray(itens) ? itens : []).map((item, indice) => {
    let uid = item?.uid;
    let chave = String(uid ?? '').trim();
    while (!chave || usados.has(chave)) {
      uid = gerarUid ? gerarUid() : `op2-${Date.now()}-${indice}-${Math.random()}`;
      chave = String(uid ?? '').trim();
    }
    usados.add(chave);
    return { ...item, uid };
  });
}

function op2EstruturaPadraoNaoMultipla(grupos, tipo, estrutura) {
  const resultado = { ...(estrutura || {}) };
  const campos = grupos?.[tipo]?.campos || {};
  Object.entries(campos).forEach(([campo, opcoes]) => {
    if (!resultado[campo] && opcoes.includes('N/A')) {
      resultado[campo] = op2CampoMultisselecao(campo) ? ['N/A'] : 'N/A';
    }
  });
  return resultado;
}

function op2NovoComponenteConjunto(grupos, tipo) {
  return {
    estrutura: op2EstruturaPadraoNaoMultipla(grupos, tipo, {}),
    cor_personalizada: '', outros: {}, observacoes_campos: {},
    grades: [], gradePorGrade: {}, observacoes: '',
  };
}

function op2NovaConfiguracaoConjunto(grupos) {
  return {
    camisa: op2NovoComponenteConjunto(grupos, 'camisa'),
    calcao: op2NovoComponenteConjunto(grupos, 'calcao'),
  };
}

function op2PrepararConfiguracaoConjunto(grupos, configuracao) {
  const resultado = {};
  for (const componente of ['camisa', 'calcao']) {
    const padrao = op2NovoComponenteConjunto(grupos, componente);
    const origem = configuracao?.[componente] || {};
    resultado[componente] = {
      ...padrao, ...origem,
      estrutura: op2EstruturaPadraoNaoMultipla(
        grupos, componente, { ...padrao.estrutura, ...(origem.estrutura || {}) },
      ),
      outros: { ...(origem.outros || {}) },
      observacoes_campos: { ...(origem.observacoes_campos || {}) },
      grades: [...(origem.grades || [])].map(String),
      gradePorGrade: JSON.parse(JSON.stringify(origem.gradePorGrade || {})),
    };
  }
  return resultado;
}

function op2EstruturaTemInformacao(estrutura) {
  return Object.values(estrutura || {}).some(valor => op2ListaMultisselecao(valor)
    .some(opcao => opcao && opcao !== 'N/A'));
}

function op2MesclarEstruturaPreferindoDestino(destino, origem) {
  const resultado = { ...(origem || {}), ...(destino || {}) };
  for (const [campo, valor] of Object.entries(origem || {})) {
    if (!op2ListaMultisselecao(resultado[campo]).some(opcao => opcao && opcao !== 'N/A')
        && op2ListaMultisselecao(valor).some(opcao => opcao && opcao !== 'N/A')) {
      resultado[campo] = JSON.parse(JSON.stringify(valor));
    }
  }
  return resultado;
}

function op2PreservarEstruturaAoTrocarTipo(grupos, draft, tipoAnterior, tipoNovo, componenteAtivo = 'camisa') {
  if (!draft || tipoAnterior === tipoNovo) return draft;
  const conjuntoAnterior = tipoAnterior === 'conjunto';
  const conjuntoNovo = tipoNovo === 'conjunto';
  const configuracao = op2PrepararConfiguracaoConjunto(grupos, draft.configuracao_conjunto);

  if (conjuntoNovo && !conjuntoAnterior) {
    const conjuntoJaPreenchido = ['camisa', 'calcao'].some(componente =>
      op2EstruturaTemInformacao(configuracao[componente].estrutura)
      || Object.keys(configuracao[componente].outros || {}).length
      || Object.keys(configuracao[componente].observacoes_campos || {}).length,
    );
    if (!conjuntoJaPreenchido) {
      for (const componente of ['camisa', 'calcao']) {
        configuracao[componente].estrutura = op2MesclarEstruturaPreferindoDestino(
          configuracao[componente].estrutura, draft.estrutura,
        );
        configuracao[componente].cor_personalizada = draft.cor_personalizada || '';
        configuracao[componente].outros = { ...(draft.estrutura_outros || {}) };
        configuracao[componente].observacoes_campos = { ...(draft.estrutura_observacoes || {}) };
      }
    }
    draft.configuracao_conjunto = configuracao;
    return draft;
  }

  if (conjuntoAnterior && !conjuntoNovo) {
    const origem = configuracao[componenteAtivo] || configuracao.camisa;
    draft.estrutura = op2MesclarEstruturaPreferindoDestino(draft.estrutura, origem.estrutura);
    draft.cor_personalizada = draft.cor_personalizada || origem.cor_personalizada || '';
    draft.estrutura_outros = { ...(origem.outros || {}), ...(draft.estrutura_outros || {}) };
    draft.estrutura_observacoes = {
      ...(origem.observacoes_campos || {}), ...(draft.estrutura_observacoes || {}),
    };
  }
  return draft;
}

function op2CopiarCamisaParaCalcao(grupos, configuracao) {
  const origem = configuracao?.camisa || {};
  const destino = op2NovoComponenteConjunto(grupos, 'calcao');
  const camposCalcao = grupos?.calcao?.campos || {};
  Object.keys(camposCalcao).forEach(campo => {
    if (origem.estrutura?.[campo]) {
      const valores = op2ListaMultisselecao(origem.estrutura[campo]);
      const validos = valores.every(valor => (camposCalcao[campo] || []).includes(valor));
      if (validos) {
        destino.estrutura[campo] = JSON.parse(JSON.stringify(origem.estrutura[campo]));
      } else {
        // Uma opção criada só para a camisa continua visível no calção como
        // "Outro", em vez de virar um select aparentemente em branco.
        destino.estrutura[campo] = op2CampoMultisselecao(campo) ? ['OUTRO'] : 'OUTRO';
        destino.outros[campo] = valores.join(', ');
      }
    }
  });
  destino.cor_personalizada = origem.cor_personalizada || '';
  destino.outros = { ...destino.outros, ...JSON.parse(JSON.stringify(origem.outros || {})) };
  destino.observacoes_campos = JSON.parse(JSON.stringify(origem.observacoes_campos || {}));
  destino.grades = [...(origem.grades || [])];
  destino.gradePorGrade = JSON.parse(JSON.stringify(origem.gradePorGrade || {}));
  destino.observacoes = origem.observacoes || '';
  return { ...(configuracao || {}), calcao: destino };
}

function op2TotalComponenteConjunto(configuracao, componente) {
  const dados = configuracao?.[componente] || {};
  return Object.values(dados.gradePorGrade || {}).reduce(
    (total, mapa) => total + Object.values(mapa || {}).reduce(
      (soma, quantidade) => soma + Number(quantidade || 0), 0,
    ), 0,
  );
}

function validarConjuntoOp2(configuracao, grupos) {
  for (const [componente, label] of [['camisa', 'Camisa'], ['calcao', 'Calção']]) {
    const dados = configuracao?.[componente] || {};
    const campos = grupos?.[componente]?.campos || {};
    for (const [campo, opcoes] of Object.entries(campos)) {
      const valores = op2ListaMultisselecao(dados.estrutura?.[campo]);
      if (!valores.length) return `${label} · ${campo.replaceAll('_', ' ')}: selecione uma opção ou N/A.`;
      if (valores.some(valor => !opcoes.includes(valor))) return `${label}: existe uma opção inválida em ${campo}.`;
      if (valores.includes('OUTRO') && !String(dados.outros?.[campo] || '').trim()) {
        return `${label} · ${campo.replaceAll('_', ' ')}: descreva a opção Outro.`;
      }
    }
    if (dados.estrutura?.cor === 'COR PERSONALIZADA' && !String(dados.cor_personalizada || '').trim()) {
      return `${label}: informe a cor personalizada.`;
    }
  }
  const camisas = op2TotalComponenteConjunto(configuracao, 'camisa');
  const calcoes = op2TotalComponenteConjunto(configuracao, 'calcao');
  const possuiAlgumaGrade = ['camisa', 'calcao'].some(
    componente => (configuracao?.[componente]?.grades || []).length,
  );
  if (possuiAlgumaGrade && (
    !(configuracao?.camisa?.grades || []).length
    || !(configuracao?.calcao?.grades || []).length
    || camisas < 1
    || calcoes < 1
  )) return 'Selecione uma grade e informe as quantidades para camisa e calção.';
  if (camisas !== calcoes) return `Camisa e calção precisam ter o mesmo total (${camisas} e ${calcoes}).`;
  return '';
}

if (typeof module !== 'undefined') module.exports = {
  validarModeloOp2, op2AlternarMultisselecao, op2ListaMultisselecao,
  op2MultisselecaoContem, op2ResumoMultisselecao,
  op2CampoMultisselecao, op2NormalizarUidsItens,
  op2EstruturaPadraoNaoMultipla,
  op2NovoComponenteConjunto, op2NovaConfiguracaoConjunto,
  op2PrepararConfiguracaoConjunto,
  op2PreservarEstruturaAoTrocarTipo,
  op2CopiarCamisaParaCalcao, op2TotalComponenteConjunto, validarConjuntoOp2,
};
