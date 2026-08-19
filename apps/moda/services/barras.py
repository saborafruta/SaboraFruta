"""
Código de barras Code128 — o irmão linear do QR.

POR QUE OS DOIS, e não só o QR: são lidos por aparelhos diferentes. O QR
precisa de câmera, e é o que o celular do operador faz. O leitor de bancada
e a pistola do almoxarifado são LASER — varrem uma linha e não enxergam QR
nenhum. Ter só QR obrigaria a fábrica a trocar de equipamento; ter só
barras tiraria o atalho do celular.

Code128 porque aceita letras, números e os caracteres que o `token_urlsafe`
produz (`-` e `_`). EAN e Code39 não serviriam: o primeiro só carrega
dígitos, o segundo não tem minúsculas — e o código de um documento é
`OP-7hK2mQx3Za_1`, com caixa alta e baixa que precisam sobreviver.

SVG, E NÃO PNG. O `renderPM` do reportlab precisa de um backend nativo
(`rlPyCairo` ou `_rl_renderPM`) que não está instalado aqui, e acrescentar
dependência binária ao Docker por causa de um desenho de barras seria caro
pelo que entrega. O `renderSVG` é Python puro, já vem junto — e o resultado
é melhor: vetor imprime nítido em qualquer impressora térmica, sem a
conversa de DPI que o PNG exige.

Gerado sob demanda, como o QR: a imagem é derivada do código, e um arquivo
salvo seria mais uma coisa para sincronizar sem ganho nenhum.
"""
from __future__ import annotations

# Code128 B cobre ASCII 32 a 126. Fora disso o desenho sai errado em
# silêncio — pior do que não sair, porque o leitor devolve outro código.
MENOR_IMPRIMIVEL = 32
MAIOR_IMPRIMIVEL = 126


def suportado(codigo: str) -> bool:
    return bool(codigo) and all(
        MENOR_IMPRIMIVEL <= ord(c) <= MAIOR_IMPRIMIVEL for c in codigo
    )


def svg(codigo: str, altura_mm: float = 14.0, legivel: bool = True) -> bytes:
    """
    O desenho do código de barras, em SVG.

    `humanReadable` liga o texto embaixo das barras. Não é enfeite: quando a
    etiqueta amassa e o leitor recusa, alguém digita — e sem o texto não há
    o que digitar.
    """
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import createBarcodeDrawing

    if not suportado(codigo):
        raise ValueError(
            'Código com caractere fora do Code128 — o desenho sairia errado '
            'em silêncio.'
        )

    desenho = createBarcodeDrawing(
        'Code128',
        value=codigo,
        barHeight=altura_mm * 72 / 25.4,
        humanReadable=legivel,
        # Barra fina de 0,5 mm: abaixo disso a impressora térmica comum da
        # confecção borra o desenho e o leitor recusa.
        barWidth=0.5 * 72 / 25.4 / 2,
    )
    return renderSVG.drawToString(desenho).encode('utf-8')
