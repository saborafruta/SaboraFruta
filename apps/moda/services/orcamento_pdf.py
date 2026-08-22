"""
PDF do ORÇAMENTO — a folha comercial que vai para o cliente.

Documento SEPARADO da ficha do pedido (`pedido_pdf.py`), de propósito: a
ficha carrega grade, arte, personalização individual e QR porque quem lê é
a fábrica. O orçamento é o que se manda para fechar negócio — produto,
quantidade, preço e prazo — e mais nada. Mandar a ficha inteira para um
cliente que ainda não comprou expõe detalhe de produção e afoga o preço,
que é a única coisa que ele quer ver.

O leiaute segue o modelo aprovado pela casa: tarja vermelha com a marca e
a palavra ORÇAMENTO, dados da empresa, identificação do cliente, tabela de
itens, totais à direita, observações e a assinatura centralizada no pé.

GERADO SOB DEMANDA, nunca gravado — mesmo motivo da ficha: arquivo salvo
envelhece na primeira alteração de valor, e passam a existir duas versões
do mesmo orçamento.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

# Amostradas do modelo em PDF, não escolhidas a olho.
VERMELHO = colors.HexColor('#eb4123')        # tarja do topo
VERMELHO_TABELA = colors.HexColor('#eb2d46')  # cabeçalho da tabela e títulos
FAIXA = colors.HexColor('#f5fafa')            # fundo do título de seção
TEXTO = colors.HexColor('#333333')
CINZA = colors.HexColor('#6b7280')

MARGEM = 18 * mm
ALTURA_TARJA = 26 * mm
LARGURA_UTIL = A4[0] - 2 * MARGEM

MESES = (
    'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
)


def esc(valor) -> str:
    """
    Texto do usuário pronto para entrar num Paragraph.

    O `Paragraph` do reportlab interpreta um dialeto de XML, então `&`, `<`
    e `>` vindos do cadastro precisam ser escapados. Sem isso "L&R SPORTS"
    sai impresso como "L&R; SPORTS" -- o parser tenta ler `&R` como
    entidade e devolve lixo no documento que vai para o cliente.
    """
    return escape('' if valor is None else str(valor))


def brl(valor) -> str:
    """R$1.234,56 — sem espaço depois do cifrão, como no modelo."""
    return f'R${valor or Decimal("0"):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def _por_extenso(data) -> str:
    return f'{data.day} de {MESES[data.month - 1]} de {data.year}'


def _cep(valor: str) -> str:
    digitos = ''.join(ch for ch in (valor or '') if ch.isdigit())
    return f'{digitos[:5]}-{digitos[5:]}' if len(digitos) == 8 else (valor or '')


def _cnpj(valor: str) -> str:
    d = ''.join(ch for ch in (valor or '') if ch.isdigit())
    if len(d) != 14:
        return valor or ''
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def _logo(filial, largura, altura):
    """
    A marca da filial, ou None quando o arquivo não abre.

    Storage fora do ar não pode derrubar o orçamento inteiro: a folha sai
    sem a marca, que é muito melhor do que não sair.
    """
    campo = getattr(filial, 'imagem', None)
    if not campo:
        return None
    try:
        campo.open('rb')
        dados = BytesIO(campo.read())
        campo.close()
        return Image(dados, width=largura, height=altura, kind='proportional')
    except Exception:
        return None


def _estilos():
    base = getSampleStyleSheet()
    return {
        'empresa': ParagraphStyle(
            'emp', parent=base['Normal'], fontSize=8.5, leading=12, textColor=TEXTO,
        ),
        'empresa_dir': ParagraphStyle(
            'empd', parent=base['Normal'], fontSize=8.5, leading=12,
            textColor=TEXTO, alignment=2,
        ),
        'secao': ParagraphStyle(
            'sec', parent=base['Normal'], fontSize=9, leading=12,
            textColor=VERMELHO_TABELA, fontName='Helvetica-Bold',
        ),
        'campo': ParagraphStyle(
            'cam', parent=base['Normal'], fontSize=9, leading=14, textColor=TEXTO,
        ),
        'th': ParagraphStyle(
            'th', parent=base['Normal'], fontSize=8, leading=10,
            textColor=colors.white, fontName='Helvetica-Bold',
        ),
        'td': ParagraphStyle(
            'td', parent=base['Normal'], fontSize=8.5, leading=12, textColor=TEXTO,
        ),
        'td_centro': ParagraphStyle(
            'tdc', parent=base['Normal'], fontSize=8.5, leading=12,
            textColor=TEXTO, alignment=1,
        ),
        'td_dir': ParagraphStyle(
            'tdd', parent=base['Normal'], fontSize=8.5, leading=12,
            textColor=TEXTO, alignment=2,
        ),
        'obs': ParagraphStyle(
            'obs', parent=base['Normal'], fontSize=8.5, leading=13,
            textColor=TEXTO, leftIndent=10, bulletIndent=2,
        ),
        # O modelo traz ainda uma assinatura de marca ("ERK Sport - A marca
        # do atleta...") abaixo do nome da empresa. Fica de fora porque não
        # existe campo para ela no cadastro, e escrever a frase da ERK no
        # código a imprimiria no orçamento de toda filial do sistema.
        'pe': ParagraphStyle(
            'pe', parent=base['Normal'], fontSize=9, leading=15,
            textColor=TEXTO, alignment=1,
        ),
    }


class OrcamentoPdfService:

    @classmethod
    def gerar(cls, pedido) -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=MARGEM, rightMargin=MARGEM,
            # O topo abre abaixo da tarja, que é desenhada no canvas e não
            # participa do fluxo -- senão o texto entraria por baixo dela.
            topMargin=ALTURA_TARJA + 10 * mm,
            bottomMargin=15 * mm,
            title=f'Orçamento {pedido.numero:06d}',
            author=str(pedido.filial),
        )
        e = _estilos()

        elementos = []
        elementos += cls._empresa(pedido, e)
        elementos += cls._cliente(pedido, e)
        elementos += cls._itens(pedido, e)
        elementos += cls._observacoes(pedido, e)
        elementos += cls._assinatura(pedido, e)

        tarja = cls._tarja(pedido)
        doc.build(elementos, onFirstPage=tarja, onLaterPages=tarja)
        return buffer.getvalue()

    # ── Tarja vermelha ───────────────────────────────────────────────────

    @staticmethod
    def _tarja(pedido):
        """
        A faixa do topo é desenhada no canvas, não como flowable.

        Ela sangra de ponta a ponta da folha, e flowable nenhum alcança a
        margem -- desenhar por cima é o único jeito de a cor chegar à borda.
        """
        filial = pedido.filial

        def desenhar(canvas, doc):
            canvas.saveState()
            largura, altura = A4
            canvas.setFillColor(VERMELHO)
            canvas.rect(0, altura - ALTURA_TARJA, largura, ALTURA_TARJA, stroke=0, fill=1)

            marca = _logo(filial, 34 * mm, 16 * mm)
            fim_marca = MARGEM
            if marca is not None:
                l, a = marca.wrapOn(canvas, 34 * mm, 16 * mm)
                marca.drawOn(canvas, MARGEM, altura - ALTURA_TARJA + (ALTURA_TARJA - a) / 2)
                fim_marca = MARGEM + l

            # "ORÇAMENTO" centralizado no espaço QUE SOBRA à direita da
            # marca, como no modelo -- centralizar na folha inteira jogaria
            # a palavra por cima do logo em marca larga.
            canvas.setFillColor(colors.white)
            canvas.setFont('Helvetica-Bold', 22)
            centro = fim_marca + (largura - fim_marca - MARGEM) / 2
            canvas.drawCentredString(centro, altura - ALTURA_TARJA / 2 - 7, 'ORÇAMENTO')
            canvas.restoreState()

        return desenhar

    # ── Dados da empresa ─────────────────────────────────────────────────

    @staticmethod
    def _empresa(pedido, e) -> list:
        filial = pedido.filial
        empresa = filial.empresa

        rua = ', '.join(x for x in [filial.endereco, filial.numero] if x)
        if filial.bairro:
            rua = f'{rua}, {filial.bairro}' if rua else filial.bairro
        cidade = ' - '.join(x for x in [filial.cidade, filial.uf] if x)
        if filial.cep:
            cidade = f'{cidade} | CEP: {_cep(filial.cep)}' if cidade else f'CEP: {_cep(filial.cep)}'

        esquerda = [f'<b>{esc(filial.razao_social)}</b>']
        if rua:
            esquerda.append(esc(rua))
        if cidade:
            esquerda.append(esc(cidade))

        direita = []
        if filial.cnpj:
            direita.append(f'CNPJ: {esc(_cnpj(filial.cnpj))}')
        if filial.email:
            direita.append(esc(filial.email))
        contato = ' '.join(
            x for x in [esc(filial.telefone),
                        f'<b>{esc(empresa.site)}</b>' if empresa.site else '']
            if x
        )
        if contato:
            direita.append(contato)

        bloco = Table(
            [[Paragraph('<br/>'.join(esquerda), e['empresa']),
              Paragraph('<br/>'.join(direita), e['empresa_dir'])]],
            colWidths=[LARGURA_UTIL * 0.55, LARGURA_UTIL * 0.45],
        )
        bloco.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return [bloco, Spacer(1, 14)]

    # ── Título de seção ──────────────────────────────────────────────────

    @staticmethod
    def _titulo(texto, e, faixa=True):
        """
        O título sobre a faixa clara.

        No modelo a faixa aparece em "Identificação do Cliente" e em "Itens
        Solicitados", mas NÃO em "Observações e Pagamento" -- `faixa=False`
        reproduz isso.
        """
        celula = Table([[Paragraph(texto, e['secao'])]], colWidths=[LARGURA_UTIL])
        estilo = [
            ('LEFTPADDING', (0, 0), (-1, -1), 8 if faixa else 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]
        if faixa:
            estilo.append(('BACKGROUND', (0, 0), (-1, -1), FAIXA))
        celula.setStyle(TableStyle(estilo))
        return celula

    # ── Cliente ──────────────────────────────────────────────────────────

    @classmethod
    def _cliente(cls, pedido, e) -> list:
        cliente = pedido.cliente
        nome = getattr(cliente, 'razao_social', None) or str(cliente)
        linhas = [f'<b>Cliente:</b>  {esc(nome)}']
        documento = getattr(cliente, 'cpf_cnpj', '') or ''
        if documento:
            linhas.append(f'<b>CNPJ/CPF:</b> {esc(documento)}')
        linhas.append(f'<b>Data de Emissão:</b> {pedido.data_pedido:%d/%m/%Y}')

        return [
            cls._titulo('IDENTIFICAÇÃO DO CLIENTE', e),
            Spacer(1, 4),
            Paragraph('<br/>'.join(linhas), e['campo']),
            Spacer(1, 12),
        ]

    # ── Itens e totais ───────────────────────────────────────────────────

    @classmethod
    def _itens(cls, pedido, e) -> list:
        cabecalho = [
            Paragraph('ITEM', e['th']),
            Paragraph('DESCRIÇÃO DO PRODUTO', e['th']),
            Paragraph('QTD', ParagraphStyle('thc', parent=e['th'], alignment=1)),
            Paragraph('V. UNIT', ParagraphStyle('thc2', parent=e['th'], alignment=1)),
            Paragraph('SUBTOTAL', ParagraphStyle('thc3', parent=e['th'], alignment=1)),
        ]

        linhas = [cabecalho]
        for indice, item in enumerate(pedido.itens.all(), start=1):
            unitario = item.valor_unitario or Decimal('0')
            # A DESCRIÇÃO é o que o cliente lê para reconhecer a peça: o
            # nome do produto mais os detalhes que ele combinou. Sem isso
            # sobra "Camisa" numa folha que decide uma compra.
            partes = [esc(item.nome_exibicao)]
            detalhes = [
                esc(x) for x in [
                    getattr(item, 'tecido', None), getattr(item, 'cor', None),
                    item.get_gola_display() if item.gola else None,
                    item.get_manga_display() if item.manga else None,
                    item.acabamento or None,
                ] if x
            ]
            if detalhes:
                partes.append(', '.join(detalhes))
            linhas.append([
                Paragraph(str(indice), e['td_centro']),
                Paragraph(' — '.join(partes), e['td']),
                Paragraph(str(item.quantidade), e['td_centro']),
                Paragraph(brl(unitario), e['td_centro']),
                Paragraph(brl(unitario * item.quantidade), e['td_centro']),
            ])

        larguras = [14 * mm, LARGURA_UTIL - 84 * mm, 16 * mm, 26 * mm, 28 * mm]
        tabela = Table(linhas, colWidths=larguras, repeatRows=1)
        tabela.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), VERMELHO_TABELA),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            # Corpo sem grade nenhuma, como no modelo: a folha respira e o
            # olho segue a linha pelo espaçamento, não por fio de tabela.
            ('TOPPADDING', (0, 1), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 9),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ]))

        return [
            cls._titulo('ITENS SOLICITADOS', e),
            Spacer(1, 2),
            tabela,
            Spacer(1, 10),
            cls._totais(pedido, e),
            Spacer(1, 16),
        ]

    @staticmethod
    def _totais(pedido, e) -> Table:
        rotulo = ParagraphStyle('tr', parent=e['td_dir'], textColor=CINZA)
        forte = ParagraphStyle(
            'tf', parent=e['td_dir'], fontSize=11, fontName='Helvetica-Bold', textColor=TEXTO,
        )

        linhas = [[Paragraph('Subtotal:', rotulo), Paragraph(brl(pedido.subtotal), e['td_dir'])]]
        for nome, valor, sinal in (
            ('Desconto:', pedido.desconto, '− '),
            ('Acréscimo:', pedido.acrescimo, '+ '),
            ('Frete:', pedido.frete, '+ '),
        ):
            if valor:
                linhas.append([Paragraph(nome, rotulo),
                               Paragraph(f'{sinal}{brl(valor)}', e['td_dir'])])
        linhas.append([Paragraph('Total:', forte), Paragraph(brl(pedido.valor_total), forte)])
        if pedido.entrada:
            linhas.append([Paragraph('Entrada:', rotulo),
                           Paragraph(f'− {brl(pedido.entrada)}', e['td_dir'])])
            linhas.append([Paragraph('Saldo:', rotulo), Paragraph(brl(pedido.saldo), e['td_dir'])])

        # Empurrado para a direita por uma coluna vazia, que é o que alinha
        # os totais com a coluna SUBTOTAL da tabela acima.
        tabela = Table(
            [[''] + linha for linha in linhas],
            colWidths=[LARGURA_UTIL - 62 * mm, 34 * mm, 28 * mm],
        )
        tabela.setStyle(TableStyle([
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (-1, 0), (-1, -1), 6),
        ]))
        return tabela

    # ── Observações ──────────────────────────────────────────────────────

    @classmethod
    def _observacoes(cls, pedido, e) -> list:
        itens = []
        if pedido.data_prevista_entrega:
            itens.append(f'Prazo de entrega: {pedido.data_prevista_entrega:%d/%m/%Y}')
        if pedido.forma_pagamento_id:
            itens.append(f'Forma de pagamento: {esc(pedido.forma_pagamento)}')
        if pedido.condicao_pagamento_id:
            itens.append(f'Condição: {esc(pedido.condicao_pagamento)}')
        if pedido.observacoes:
            itens += [esc(l.strip()) for l in pedido.observacoes.splitlines() if l.strip()]

        # Seção vazia num documento que vai para o cliente parece erro de
        # sistema -- sem nada a dizer, o bloco inteiro não sai.
        if not itens:
            return []

        blocos = [cls._titulo('OBSERVAÇÕES E PAGAMENTO', e, faixa=False), Spacer(1, 2)]
        blocos += [Paragraph(texto, e['obs'], bulletText='•') for texto in itens]
        return blocos

    # ── Assinatura ───────────────────────────────────────────────────────

    @classmethod
    def _assinatura(cls, pedido, e) -> list:
        filial = pedido.filial
        cidade = filial.cidade or ''
        data = _por_extenso(pedido.data_pedido)
        local = f'{cidade}, {data}' if cidade else data

        blocos = [
            Paragraph(f'<b>{esc(local)}</b>', e['pe']),
            Spacer(1, 10),
        ]
        if filial.nome_fantasia:
            blocos.append(Paragraph(
                f'<b>{esc(filial.nome_fantasia.upper())}</b>',
                ParagraphStyle('pf', parent=e['pe'], fontSize=11),
            ))
        blocos.append(Paragraph(
            f'<b>{esc(filial.razao_social.upper())}</b>',
            ParagraphStyle('pr', parent=e['pe'], fontSize=8),
        ))

        marca = _logo(filial, 26 * mm, 12 * mm)
        if marca is not None:
            marca.hAlign = 'CENTER'
            blocos += [Spacer(1, 12), marca]

        # Inteira ou na página seguinte: num orçamento longo a data ficava
        # sozinha no pé de uma página e o nome da empresa aparecia na outra,
        # o que faz a assinatura parecer de outro documento.
        return [Spacer(1, 22 * mm), KeepTogether(blocos)]
