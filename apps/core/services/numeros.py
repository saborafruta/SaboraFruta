"""
Número escrito como a pessoa daqui escreve.

O TECLADO DAQUI TEM VÍRGULA. Quem digita peso escreve "0,400"; quem digita
valor escreve "1.234,56". Campo que responde "informe um número" a isso está
recusando o dado certo por causa do separador — o erro parece do dado, e é da
tela.

O PONTO CONTINUA VALENDO. Importação, integração e teclado numérico mandam
"1234.56", e recusar isso quebraria o que já funciona.

MILHAR SÓ SAI QUANDO HÁ VÍRGULA DEPOIS. Em "1.200" o ponto pode ser milhar
(1200) ou decimal (1,2) — e em peso, "1.200" com três casas é um quilo e
duzentos. Trocar por 1200 poria uma tonelada na carga. Só se limpa o ponto
quando a vírgula deixa claro quem é quem: "1.200,5".
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


def decimal_ptbr(valor, padrao=None):
    """
    Converte texto em `Decimal`, aceitando vírgula e ponto de milhar.

    Devolve `padrao` quando o texto não é número — quem chama decide se isso
    é um erro de formulário ou um campo em branco.
    """
    if valor is None or valor == '':
        return padrao
    if isinstance(valor, Decimal):
        return valor
    if isinstance(valor, (int, float)):
        return Decimal(str(valor))

    texto = str(valor).strip().replace(' ', '')
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        return Decimal(texto)
    except InvalidOperation:
        return padrao
