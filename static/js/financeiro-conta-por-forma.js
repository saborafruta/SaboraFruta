(function () {
  'use strict';

  function contaDoFormulario(forma) {
    var nomeAlvo = forma.dataset.contaAlvo;
    if (!nomeAlvo) return null;
    var formulario = forma.closest('form');
    return formulario ? formulario.querySelector('[name="' + nomeAlvo + '"]') : null;
  }

  function selecionarContaDaForma(forma) {
    if (!forma || !forma.matches('select[data-conta-alvo]')) return;
    var opcao = forma.options[forma.selectedIndex];
    var contaId = opcao && opcao.dataset.conta;
    var conta = contaDoFormulario(forma);
    if (!conta || !contaId) return;
    var existe = Array.from(conta.options || []).some(function (item) {
      return String(item.value) === String(contaId);
    });
    if (!existe) return;
    conta.value = String(contaId);
    conta.dispatchEvent(new Event('input', { bubbles: true }));
    conta.dispatchEvent(new Event('change', { bubbles: true }));
  }

  document.addEventListener('change', function (event) {
    selecionarContaDaForma(event.target);
  });
  window.financeiroSelecionarContaDaForma = selecionarContaDaForma;
})();
