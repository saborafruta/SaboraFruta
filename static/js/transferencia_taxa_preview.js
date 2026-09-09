(function () {
  function numero(valor) {
    return Number(String(valor || "0").replace(",", ".")) || 0;
  }

  function moeda(valor) {
    return valor.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
  }

  function arredondar(valor) {
    return Math.round((valor + Number.EPSILON) * 100) / 100;
  }

  function taxaPercentual(opcao, parcelas, bandeira) {
    let taxas = {};
    try { taxas = JSON.parse(opcao.dataset.taxasParcelamento || "{}"); } catch (_) { taxas = {}; }
    const exata = `${parcelas}|${bandeira}`;
    const generica = `${parcelas}|`;
    if (Object.prototype.hasOwnProperty.call(taxas, exata)) return numero(taxas[exata]);
    if (Object.prototype.hasOwnProperty.call(taxas, generica)) return numero(taxas[generica]);
    return numero(opcao.dataset.taxaPercentual);
  }

  function preparar(form) {
    if (form.dataset.transferenciaTaxaPreparada) return;
    const tipo = form.querySelector('[name="tipo"]');
    const forma = form.querySelector('[name="forma_pagamento"]');
    const valor = form.querySelector('[name="valor"]');
    const painel = form.querySelector("[data-transferencia-taxa-preview]");
    const texto = painel && painel.querySelector("[data-transferencia-taxa-text]");
    const titulo = painel && painel.querySelector("[data-transferencia-taxa-titulo]");
    const campoTaxa = form.querySelector('[name="valor_taxa"]');
    const campoLiquido = form.querySelector('[name="valor_liquido"]');
    if (!tipo || !forma || !valor || !painel || !texto || !campoTaxa || !campoLiquido) return;
    form.dataset.transferenciaTaxaPreparada = "1";

    function escrever(campo, valorCalculado) {
      campo.value = arredondar(Math.max(valorCalculado, 0)).toFixed(2);
    }

    function mostrarResumo(taxa, liquido) {
      titulo.textContent = tipo.value === "transferencia" ? "Taxa da transferência" : "Taxa da entrada manual";
      texto.textContent = taxa > 0
        ? `${moeda(taxa)} será descontado. A conta de destino receberá ${moeda(liquido)}.`
        : "Esta forma não possui taxa informada. A conta de destino receberá o valor integral.";
    }

    function atualizarAutomatico() {
      const opcao = forma.selectedOptions[0];
      const bandeira = form.querySelector('[name="bandeira"]');
      const parcelas = form.querySelector('[name="numero_parcelas"]');
      const recebeValor = tipo.value === "credito" || tipo.value === "transferencia";
      painel.hidden = !recebeValor || !forma.value;
      if (painel.hidden || !opcao) {
        campoTaxa.value = "";
        campoLiquido.value = "";
        return;
      }
      const bruto = numero(valor.value);
      const qtdParcelas = numero(parcelas && parcelas.value) || 1;
      const percentual = taxaPercentual(opcao, qtdParcelas, (bandeira && bandeira.value) || "");
      const fixa = numero(opcao.dataset.taxaFixa);
      const taxa = Math.min(arredondar((bruto * percentual / 100) + fixa), bruto);
      const liquido = arredondar(bruto - taxa);
      escrever(campoTaxa, taxa);
      escrever(campoLiquido, liquido);
      mostrarResumo(taxa, liquido);
    }
    function atualizarPorTaxa() {
      const bruto = numero(valor.value);
      const taxa = Math.min(numero(campoTaxa.value), bruto);
      escrever(campoLiquido, bruto - taxa);
      mostrarResumo(taxa, bruto - taxa);
    }
    function atualizarPorLiquido() {
      const bruto = numero(valor.value);
      const liquido = Math.min(numero(campoLiquido.value), bruto);
      escrever(campoTaxa, bruto - liquido);
      mostrarResumo(bruto - liquido, liquido);
    }
    form.addEventListener("input", function (event) {
      if (event.target === campoTaxa) atualizarPorTaxa();
      else if (event.target === campoLiquido) atualizarPorLiquido();
      else atualizarAutomatico();
    });
    form.addEventListener("change", function (event) {
      if (event.target !== campoTaxa && event.target !== campoLiquido) atualizarAutomatico();
    });
    atualizarAutomatico();
  }

  function iniciar() {
    document.querySelectorAll("form[data-transferencia-taxa]").forEach(preparar);
  }
  document.addEventListener("DOMContentLoaded", iniciar);
  document.addEventListener("htmx:afterSwap", iniciar);
})();
