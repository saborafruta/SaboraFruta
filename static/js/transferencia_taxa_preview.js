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
    if (!tipo || !forma || !valor || !painel || !texto) return;
    form.dataset.transferenciaTaxaPreparada = "1";

    function atualizar() {
      const opcao = forma.selectedOptions[0];
      const bandeira = form.querySelector('[name="bandeira"]');
      const parcelas = form.querySelector('[name="numero_parcelas"]');
      const ehTransferencia = tipo.value === "transferencia";
      painel.hidden = !ehTransferencia || !forma.value;
      if (painel.hidden || !opcao) return;
      const bruto = numero(valor.value);
      const qtdParcelas = numero(parcelas && parcelas.value) || 1;
      const percentual = taxaPercentual(opcao, qtdParcelas, (bandeira && bandeira.value) || "");
      const fixa = numero(opcao.dataset.taxaFixa);
      const taxa = Math.min(arredondar((bruto * percentual / 100) + fixa), bruto);
      const liquido = arredondar(bruto - taxa);
      texto.textContent = taxa > 0
        ? `${moeda(taxa)} será descontado automaticamente. A conta de destino receberá ${moeda(liquido)}.`
        : "Esta forma não possui taxa configurada. A conta de destino receberá o valor integral.";
    }
    form.addEventListener("input", atualizar);
    form.addEventListener("change", atualizar);
    atualizar();
  }

  function iniciar() {
    document.querySelectorAll("form[data-transferencia-taxa]").forEach(preparar);
  }
  document.addEventListener("DOMContentLoaded", iniciar);
  document.addEventListener("htmx:afterSwap", iniciar);
})();
