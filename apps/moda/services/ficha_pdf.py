"""
Ficha de produção — o papel que desce para a fábrica junto com o corte.

NÃO É O PDF DO PEDIDO. O do pedido é comercial: fala com o cliente, mostra
valores, condição de pagamento, prazo combinado. Este fala com quem costura:
o que cortar, com que tecido, quantas peças de cada tamanho, que material
consumir e em que ordem executar. Preço não aparece em nenhuma linha — a
folha circula pelo chão de fábrica e por terceiros faccionistas, e custo de
material é informação de negociação, não de execução.

REPRODUZ A FICHA FÍSICA, e é por isso que a tabela de operações termina em
três colunas vazias (produzido, data, visto): a ficha de papel da confecção
é preenchida À MÃO no posto, e uma versão digital sem lugar para escrever
seria bonita e inútil — o encarregado imprimiria e riscaria as margens.

Sai da ORDEM DE PRODUÇÃO e não do pedido: um pedido com três produtos vira
três ordens, e cada posto trabalha uma peça de cada vez. Uma folha com os
três produtos obrigaria o operador a achar o dele no meio.

Gerado sob demanda, nunca gravado — mesma razão do PDF do pedido: arquivo
salvo envelhece na primeira troca de arte ou de grade, e passam a existir
duas verdades. O QR do rodapé resolve o resto: quem está com o papel na mão
chega à versão viva pelo celular.
"""
from __future__ import annotations

from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from .pedido_pdf import AZUL, BORDA, CINZA, FUNDO, _estilos, _imagem, _tabela

# Margem de 15 mm, mais estreita que a do PDF do pedido: a tabela de
# operações tem dez colunas, e com 20 mm de margem as três de
# preenchimento à mão ficariam finas demais para escrever.
MARGEM = 15 * mm
LARGURA = A4[0] - 2 * MARGEM

# Altura das linhas onde o operador escreve à mão. 7 mm é o que uma caneta
# esferográfica precisa para caber sem espremer — abaixo disso o
# preenchimento fica ilegível e a folha volta sem servir para apontamento.
LINHA_MANUSCRITA = 7 * mm


def _sem_dados(texto, e):
    """
    Diz o que FALTA, em vez de omitir a seção.

    Ficha sem a lista de materiais e ficha cuja seção de materiais some são
    a mesma folha para quem recebe — e a segunda esconde que o produto não
    tem ficha técnica cadastrada. O buraco declarado é o que faz alguém ir
    cadastrar.
    """
    return Paragraph(f'<i>{texto}</i>', e['pequeno'])


class FichaProducaoPdfService:

    @classmethod
    def gerar(cls, ordem, base_url: str = '') -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=MARGEM, rightMargin=MARGEM,
            topMargin=12 * mm, bottomMargin=20 * mm,
            title=f'Ficha de produção {ordem.numero}',
            author=str(ordem.filial),
        )
        e = _estilos()

        elementos = [
            *cls._cabecalho(ordem, base_url, e),
            *cls._identificacao(ordem, e),
            *cls._produto(ordem, e),
            *cls._grade(ordem, e),
            *cls._materiais(ordem, e),
            *cls._operacoes(ordem, e),
            *cls._observacoes(ordem, e),
        ]

        rodape = cls._rodape(ordem, base_url, e)
        doc.build(elementos, onFirstPage=rodape, onLaterPages=rodape)
        return buffer.getvalue()

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _cabecalho(ordem, base_url, e) -> list:
        """
        Empresa, título, número da OP e o QR — tudo na primeira olhada.

        O QR vem GRANDE aqui em cima, e repetido pequeno no rodapé de toda
        página: a folha fica presa no fardo com só o topo à vista, e um QR
        que exige desdobrar o papel para escanear não é escaneado.
        """
        empresa = ordem.filial.empresa
        logo = _imagem(getattr(ordem.filial, 'imagem', None), 28 * mm, 16 * mm)

        identificacao = [
            Paragraph(f'<b>{empresa.razao_social}</b>', e['normal']),
            Paragraph(str(ordem.filial), e['pequeno']),
            Spacer(1, 4),
            Paragraph(
                '<font size="13"><b>FICHA DE PRODUÇÃO</b></font>', e['normal'],
            ),
        ]

        numero = [
            Paragraph(
                f'<font size="16"><b>{ordem.numero}</b></font><br/>'
                f'<font size="8" color="#64748b">Pedido #{ordem.pedido.numero:06d}</font>',
                e['normal'],
            ),
            Spacer(1, 3),
            _qr(ordem, base_url, 24 * mm),
        ]

        topo = Table(
            [[logo or '', identificacao, numero]],
            colWidths=[30 * mm, LARGURA - 30 * mm - 42 * mm, 42 * mm],
        )
        topo.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (2, 0), (2, 0), 'RIGHT'),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (-1, 0), (-1, 0), 0),
        ]))
        return [topo, Spacer(1, 6)]

    # ── Identificação ────────────────────────────────────────────────────

    @staticmethod
    def _identificacao(ordem, e) -> list:
        prazo = f'{ordem.prazo:%d/%m/%Y}' if ordem.prazo else 'sem prazo'
        if ordem.atrasada:
            # Vermelho no prazo vencido: a folha some numa pilha de vinte, e
            # a única chance de alguém reagir é o prazo saltar da página.
            prazo = f'<font color="#dc2626"><b>{prazo} — ATRASADA</b></font>'

        dados = [
            ['Ordem de produção', 'Pedido', 'Cliente', 'Emissão', 'Prazo', 'Prioridade'],
            [
                Paragraph(f'<b>{ordem.numero}</b>', e['celula']),
                Paragraph(f'#{ordem.pedido.numero:06d}', e['celula']),
                Paragraph(str(ordem.cliente), e['celula']),
                Paragraph(f'{ordem.emitida_em:%d/%m/%Y}', e['celula']),
                Paragraph(prazo, e['celula']),
                Paragraph(ordem.get_prioridade_display(), e['celula']),
            ],
        ]
        larguras = [30 * mm, 20 * mm, LARGURA - 128 * mm, 22 * mm, 30 * mm, 26 * mm]
        return [_tabela(dados, larguras), Spacer(1, 6)]

    # ── Produto e imagem ─────────────────────────────────────────────────

    @classmethod
    def _produto(cls, ordem, e) -> list:
        item = ordem.item

        dados = [
            [Paragraph('<b>Produto</b>', e['celula']),
             Paragraph(ordem.descricao_produto, e['celula'])],
            [Paragraph('<b>Modelo</b>', e['celula']),
             Paragraph(str(item.modelo) if item.modelo_id else '—', e['celula'])],
            [Paragraph('<b>Tecido / malha</b>', e['celula']),
             Paragraph(item.tecido_exibicao or '—', e['celula'])],
            [Paragraph('<b>Cor</b>', e['celula']),
             Paragraph(str(item.cor) if item.cor_id else '—', e['celula'])],
            [Paragraph('<b>Gola / manga</b>', e['celula']),
             Paragraph(
                 f'{item.get_gola_display() or "—"} / {item.get_manga_display() or "—"}',
                 e['celula'])],
            [Paragraph('<b>Acabamento</b>', e['celula']),
             Paragraph(item.acabamento or '—', e['celula'])],
        ]
        ficha_tabela = _tabela(dados, [30 * mm, LARGURA / 2 - 30 * mm], cabecalho=False)

        # As artes ao lado da especificação, não numa seção depois: quem
        # está no posto compara o desenho com a peça na mão, e virar a
        # página para conferir a cor é o que faz conferir de menos.
        imagens = cls._imagens(ordem)
        lado = imagens or [_sem_dados('Sem arte anexada nesta peça.', e)]

        corpo = Table(
            [[ficha_tabela, lado]],
            colWidths=[LARGURA / 2, LARGURA / 2],
        )
        corpo.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (-1, 0), (-1, 0), 0),
        ]))

        blocos = [Paragraph('PRODUTO', e['secao']), corpo]

        personalizacoes = list(ordem.personalizacoes)
        if personalizacoes:
            texto = ' · '.join(
                f'{p.get_tipo_display()} em {p.get_tecnica_display()}'
                + (f' ({p.local})' if p.local else '')
                for p in personalizacoes
            )
            blocos += [Spacer(1, 3),
                       Paragraph(f'<b>Personalização:</b> {texto}', e['pequeno'])]

        return blocos + [Spacer(1, 6)]

    @staticmethod
    def _imagens(ordem) -> list:
        """Até quatro vistas, lado a lado, com o nome da posição embaixo."""
        vistas = [v for v in ordem.visuais if v.tem_imagem][:4]
        if not vistas:
            return []

        largura = (LARGURA / 2 - 6 * mm) / max(len(vistas), 2)
        celulas, rotulos = [], []
        for v in vistas:
            campo = v.imagem if v.imagem else v.mockup.imagem
            imagem = _imagem(campo, largura - 3 * mm, 32 * mm)
            celulas.append(imagem or '')
            rotulos.append(v.get_posicao_display())

        tabela = Table([celulas, rotulos], colWidths=[largura] * len(vistas))
        tabela.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTSIZE', (0, 1), (-1, 1), 6.5),
            ('TEXTCOLOR', (0, 1), (-1, 1), CINZA),
            ('BOX', (0, 0), (-1, 0), 0.25, BORDA),
            ('INNERGRID', (0, 0), (-1, 0), 0.25, BORDA),
        ]))
        return [tabela]

    # ── Grade e quantidade ───────────────────────────────────────────────

    @staticmethod
    def _grade(ordem, e) -> list:
        grade = list(ordem.grade)
        if not grade:
            return [
                Paragraph('GRADE', e['secao']),
                _sem_dados('Sem grade lançada — a quantidade não está distribuída '
                           'por tamanho.', e),
                Spacer(1, 6),
            ]

        tamanhos = [g.tamanho.sigla for g in grade] + ['TOTAL']
        quantidades = [str(g.quantidade) for g in grade] + [str(ordem.quantidade)]
        # A linha vazia é onde o corte anota o que REALMENTE saiu de cada
        # tamanho. É a coluna que o encarregado desenha à caneta quando a
        # ficha não tem — melhor já vir impressa.
        cortado = [''] * len(grade) + ['']

        largura = min(LARGURA / len(tamanhos), 26 * mm)
        tabela = Table(
            [tamanhos, quantidades, cortado],
            colWidths=[largura] * len(tamanhos),
            rowHeights=[None, None, LINHA_MANUSCRITA],
        )
        tabela.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.25, BORDA),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('BACKGROUND', (-1, 1), (-1, 1), FUNDO),
        ]))

        return [
            Paragraph(f'GRADE — {ordem.quantidade} peças', e['secao']),
            tabela,
            Spacer(1, 2),
            Paragraph(
                'Segunda linha: quantidade planejada. Terceira: preencher com '
                'o que foi efetivamente cortado.', e['pequeno'],
            ),
            Spacer(1, 6),
        ]

    # ── Materiais ────────────────────────────────────────────────────────

    @staticmethod
    def _materiais(ordem, e) -> list:
        linhas = ordem.materiais_da_ordem
        if not linhas:
            return [
                Paragraph('MATERIAIS', e['secao']),
                _sem_dados('Produto sem ficha técnica cadastrada — os materiais '
                           'não podem ser listados.', e),
                Spacer(1, 6),
            ]

        dados = [['Material', 'Código', 'Un.', 'Consumo/peça', 'Perda', 'Total da OP', 'Separado']]
        for linha in linhas:
            m = linha['material']
            dados.append([
                Paragraph(f'<b>{m.get_tipo_display()}</b> — {m.descricao}', e['celula']),
                Paragraph(m.codigo or '—', e['celula']),
                m.get_unidade_display(),
                f'{m.consumo:.4f}'.replace('.', ','),
                f'{m.perda:.1f}%'.replace('.', ','),
                Paragraph(f'<b>{linha["total"]:.4f}</b>'.replace('.', ','), e['celula']),
                '',  # o almoxarifado marca o que já entregou
            ])

        larguras = [
            LARGURA - 132 * mm, 24 * mm, 14 * mm, 24 * mm, 16 * mm, 28 * mm, 26 * mm,
        ]
        tabela = _tabela(dados, larguras)
        tabela.setStyle(TableStyle([
            ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
            ('BACKGROUND', (-1, 1), (-1, -1), colors.white),
        ]))

        return [
            Paragraph('MATERIAIS', e['secao']),
            tabela,
            Spacer(1, 2),
            Paragraph(
                'Consumo por peça já com a perda embutida no total. Última '
                'coluna: visto do almoxarifado na entrega.', e['pequeno'],
            ),
            Spacer(1, 6),
        ]

    # ── Operações e sequência ────────────────────────────────────────────

    @staticmethod
    def _operacoes(ordem, e) -> list:
        linhas = ordem.operacoes_da_ordem
        if not linhas:
            return [
                Paragraph('SEQUÊNCIA DE OPERAÇÕES', e['secao']),
                _sem_dados('Produto sem roteiro cadastrado — a sequência de '
                           'produção não pode ser listada.', e),
                Spacer(1, 6),
            ]

        dados = [[
            'Seq.', 'Operação', 'Setor', 'Máquina', 'Responsável',
            'Tempo/peça', 'Total', 'Produzido', 'Data', 'Visto',
        ]]
        for linha in linhas:
            etapa = linha['etapa']
            dados.append([
                str(etapa.sequencia),
                Paragraph(etapa.operacao.nome, e['celula']),
                Paragraph(etapa.operacao.get_setor_display(), e['celula']),
                Paragraph(etapa.maquina_efetiva or '—', e['celula']),
                Paragraph(etapa.responsavel_efetivo or '—', e['celula']),
                f'{etapa.tempo:.2f}'.replace('.', ','),
                f'{linha["minutos"]:.0f}',
                '', '', '',  # preenchidos à mão no posto
            ])

        # Total de tempo fecha a tabela: é o que o PCP confere quando a
        # folha volta, e recalcular somando dez linhas à mão dá erro.
        dados.append([
            '', Paragraph('<b>Tempo total previsto</b>', e['celula']), '', '', '',
            '', f'{ordem.tempo_total_minutos:.0f}', '', '', '',
        ])

        larguras = [
            10 * mm, LARGURA - 162 * mm, 22 * mm, 22 * mm, 24 * mm,
            16 * mm, 14 * mm, 18 * mm, 16 * mm, 20 * mm,
        ]
        alturas = [None] + [LINHA_MANUSCRITA] * len(linhas) + [None]
        tabela = Table(dados, colWidths=larguras, rowHeights=alturas, repeatRows=1)
        tabela.setStyle(TableStyle([
            ('GRID', (0, 0), (-1, -1), 0.25, BORDA),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (5, 0), (-1, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (6, -2), [colors.white, FUNDO]),
            ('BACKGROUND', (0, -1), (-1, -1), FUNDO),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ]))

        return [
            Paragraph('SEQUÊNCIA DE OPERAÇÕES', e['secao']),
            tabela,
            Spacer(1, 2),
            Paragraph(
                'Tempo em minutos. As três últimas colunas são preenchidas no '
                'posto, a cada operação concluída.', e['pequeno'],
            ),
            Spacer(1, 6),
        ]

    # ── Observações ──────────────────────────────────────────────────────

    @staticmethod
    def _observacoes(ordem, e) -> list:
        """
        As observações da OP mais o espaço em branco para as da fábrica.

        O retângulo vazio parece desperdício de papel e não é: é onde o
        encarregado anota o que deu errado. Sem ele, a anotação vai para o
        verso da folha e não volta para o sistema.
        """
        texto = (ordem.observacoes or '').strip()

        blocos = [Paragraph('OBSERVAÇÕES', e['secao'])]
        if texto:
            blocos.append(Paragraph(texto.replace('\n', '<br/>'), e['normal']))
            blocos.append(Spacer(1, 3))

        avisos = ordem.divergencias
        if avisos:
            # Divergência entre a OP e o pedido tem que ir para o papel: a
            # folha impressa hoje pode estar mandando produzir a quantidade
            # antiga, e só a tela avisaria.
            blocos.append(Paragraph(
                '<font color="#dc2626"><b>ATENÇÃO:</b> ' +
                ' '.join(avisos) + '</font>', e['pequeno'],
            ))
            blocos.append(Spacer(1, 3))

        caixa = Table([['']], colWidths=[LARGURA], rowHeights=[24 * mm])
        caixa.setStyle(TableStyle([('BOX', (0, 0), (-1, -1), 0.5, BORDA)]))
        blocos += [
            Paragraph('Anotações da produção:', e['pequeno']),
            caixa,
        ]
        return [KeepTogether(blocos)]

    # ── Rodapé ───────────────────────────────────────────────────────────

    @staticmethod
    def _rodape(ordem, base_url, e):
        """
        Devolve o desenhador do rodapé — chamado em toda página.

        Repete a identificação em cada folha porque a ficha é destacada e
        distribuída por posto: página solta sem número de OP não volta para
        lugar nenhum.
        """
        import qrcode

        buffer = BytesIO()
        qrcode.make(_destino(ordem, base_url)).save(buffer, 'PNG')
        buffer.seek(0)
        # `ImageReader` e não o BytesIO cru: `drawImage` espera caminho ou
        # leitor, e um buffer se esgota na primeira página — o rodapé é
        # desenhado em todas.
        qr = ImageReader(buffer)

        gerado = timezone.localtime().strftime('%d/%m/%Y %H:%M')
        cliente = str(ordem.cliente)[:60]

        def desenhar(canvas, doc):
            canvas.saveState()
            y = 10 * mm

            canvas.drawImage(
                qr, 15 * mm, y - 1 * mm, width=13 * mm, height=13 * mm,
                preserveAspectRatio=True, mask='auto',
            )
            canvas.setFont('Helvetica-Bold', 8)
            canvas.setFillColor(colors.black)
            canvas.drawString(31 * mm, y + 7 * mm, f'{ordem.numero} · {cliente}')

            canvas.setFont('Helvetica', 6.5)
            canvas.setFillColor(CINZA)
            canvas.drawString(
                31 * mm, y + 3.5 * mm,
                f'Ficha de produção gerada em {gerado} · escaneie para abrir a OP',
            )
            canvas.drawRightString(
                A4[0] - 15 * mm, y + 3.5 * mm, f'Página {canvas.getPageNumber()}',
            )

            canvas.setStrokeColor(BORDA)
            canvas.line(15 * mm, y + 14 * mm, A4[0] - 15 * mm, y + 14 * mm)
            canvas.restoreState()

        return desenhar


def _destino(ordem, base_url: str) -> str:
    """
    O que o QR guarda: a URL curta do leitor.

    Sem `base_url` sobra o código puro — vale para o leitor de bancada, que
    lê texto, mas não abre nada na câmera do celular.
    """
    if base_url and ordem.codigo_qr:
        return f'{base_url}/q/{ordem.codigo_qr}/'
    return ordem.codigo_qr or ordem.numero


def _qr(ordem, base_url: str, tamanho):
    """O QR do cabeçalho, como elemento de fluxo."""
    import qrcode

    imagem = qrcode.make(_destino(ordem, base_url))
    buffer = BytesIO()
    imagem.save(buffer, 'PNG')
    buffer.seek(0)
    return Image(buffer, width=tamanho, height=tamanho)
