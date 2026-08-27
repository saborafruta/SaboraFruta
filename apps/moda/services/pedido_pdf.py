"""
PDF do pedido de produção — a ficha que vai para o cliente e para a fábrica.

GERADO SOB DEMANDA, nunca gravado. Um arquivo salvo no momento em que o
pedido é fechado envelhece na primeira alteração de grade, arte ou valor, e
aí passam a existir duas versões do mesmo pedido: a do papel e a da tela.
Como a URL é estável, o link continua valendo — o que muda é que ele sempre
devolve o pedido de agora.

O documento monta só as seções que têm conteúdo. Um pedido sem
personalização individual não ganha uma página em branco intitulada
"Personalização" — seção vazia num documento que vai para o cliente parece
erro de sistema.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    HRFlowable, Image, KeepInFrame, KeepTogether, Paragraph, SimpleDocTemplate,
    PageBreak, Spacer, Table, TableStyle,
)

from .pdf_marca import (
    ALTURA_TARJA, MARGEM, bloco_empresa, desenhar_tarja, esc,
    estilos_empresa,
)

AZUL = colors.HexColor('#2563eb')
CINZA = colors.HexColor('#64748b')
BORDA = colors.HexColor('#d1d5db')
FUNDO = colors.HexColor('#f8fafc')
PAGINA = landscape(A4)
LARGURA_UTIL = PAGINA[0] - 2 * MARGEM

# O QUE O PDF CONSEGUE DESENHAR. Não é a mesma lista de `pode_pre_visualizar`,
# que é do NAVEGADOR: o SVG entra lá e não aqui, porque o `Image` do reportlab
# lê bitmap pelo PIL. Sem esta lista o SVG caía na exceção de `_imagem` e
# sumia -- funcionava, mas por acidente, e acidente não avisa quando muda.
DESENHAVEIS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def _estilos():
    base = getSampleStyleSheet()
    return {
        **estilos_empresa(),
        'titulo': ParagraphStyle('t', parent=base['Title'], fontSize=16, spaceAfter=2),
        'secao': ParagraphStyle(
            's', parent=base['Heading2'], fontSize=10.5, textColor=AZUL,
            spaceBefore=5, spaceAfter=2,
        ),
        'normal': ParagraphStyle('n', parent=base['Normal'], fontSize=8.5, leading=11),
        'pequeno': ParagraphStyle(
            'p', parent=base['Normal'], fontSize=7, leading=9, textColor=CINZA,
        ),
        'celula': ParagraphStyle('c', parent=base['Normal'], fontSize=8, leading=10),
    }


def _tabela(dados, larguras, cabecalho=True):
    """Tabela no padrão já usado nos outros PDFs do sistema."""
    estilo = [
        ('GRID', (0, 0), (-1, -1), 0.25, BORDA),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]
    if cabecalho:
        estilo += [
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, FUNDO]),
        ]
    tabela = Table(dados, colWidths=larguras, repeatRows=1 if cabecalho else 0)
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _imagem(campo, largura, altura):
    """
    Imagem do storage, ou None quando o arquivo não abre.

    Arquivo apagado do disco ou storage remoto fora do ar não pode derrubar
    a geração do pedido inteiro: o documento sai sem a foto, que é bem
    melhor do que não sair.
    """
    if not campo:
        return None
    try:
        campo.open('rb')
        dados = BytesIO(campo.read())
        campo.close()
        return Image(dados, width=largura, height=altura, kind='proportional')
    except Exception:
        return None


def _estrutura_item(item):
    """Separa observação livre dos campos preenchidos da estrutura da peça."""
    texto = (item.observacoes or '').strip()
    marcador = 'Estrutura da peça:'
    if marcador not in texto:
        return texto, []
    livre, bloco = texto.split(marcador, 1)
    campos = []
    for linha in bloco.splitlines():
        linha = linha.strip()
        if not linha or ':' not in linha:
            continue
        rotulo, valor = (parte.strip() for parte in linha.split(':', 1))
        if valor:
            campos.append((rotulo, valor))
    return livre.strip(), campos


def whatsapp_numero(pedido) -> str:
    """
    Número do cliente em formato aceito pelo wa.me: só dígitos, com DDI.

    O 55 é acrescentado quando o cadastro tem só DDD + número, que é como
    quase todo telefone brasileiro é digitado. Número que já vem com DDI
    (13 dígitos) passa intacto — reescrevê-lo transformaria um celular certo
    num errado.
    """
    bruto = (
        getattr(pedido, 'contato_telefone', '')
        or getattr(pedido.cliente, 'celular', '')
        or getattr(pedido.cliente, 'telefone', '')
        or ''
    )
    digitos = ''.join(c for c in bruto if c.isdigit())
    if not digitos:
        return ''
    if len(digitos) in (10, 11):
        return f'55{digitos}'
    return digitos


def mensagem_whatsapp(pedido, link: str) -> str:
    """
    A mensagem padrão da especificação, com o link do pedido no lugar do anexo.

    O texto original dizia "em anexo". O wa.me — que é o que abre o WhatsApp
    a partir do navegador — só carrega TEXTO: não existe forma de anexar
    arquivo por link. Prometer anexo e mandar só texto faria o cliente
    procurar um arquivo que não chegou, então a frase virou o link. Anexar
    de verdade continua possível pelo botão de baixar, arrastando o arquivo
    na conversa.

    O link é o da PÁGINA do pedido, não o do PDF: no celular o PDF abre no
    visualizador e o status do pedido fica de fora — e é o status que o
    cliente volta para consultar depois. O PDF está a um toque de lá.
    """
    entrega = (
        f'{pedido.data_prevista_entrega:%d/%m/%Y}'
        if pedido.data_prevista_entrega else 'a combinar'
    )
    cliente = (
        getattr(pedido.cliente, 'nome_fantasia', '')
        or getattr(pedido.cliente, 'razao_social', '')
        or 'cliente'
    )
    return (
        f'Olá, {cliente}!\n\n'
        f'Seu pedido #{pedido.numero:06d} foi finalizado.\n\n'
        f'Neste link você acompanha o pedido e baixa o PDF:\n'
        f'{link}\n\n'
        f'Prazo de entrega: {entrega}.\n\n'
        f'Obrigado!'
    )


class PedidoPdfService:

    @classmethod
    def gerar(cls, pedido, base_url: str = '') -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=PAGINA,
            leftMargin=MARGEM, rightMargin=MARGEM,
            # A ficha segue o modelo operacional: quase toda a A4 pertence
            # ao produto, sem uma tarja alta consumindo a área de trabalho.
            topMargin=8 * mm, bottomMargin=28 * mm,
            title=f'Pedido {pedido.numero:06d}',
            author=str(pedido.filial),
        )
        e = _estilos()
        elementos = []
        itens = list(pedido.itens.all())
        # O frame do ReportLab desconta 6 pt em cada borda além das margens.
        altura_util = PAGINA[1] - doc.topMargin - doc.bottomMargin - 14
        if not itens:
            blocos = cls._cliente(pedido, e)
            blocos += cls._artes_do_pedido(pedido, e)
            blocos += cls._financeiro(pedido, e, LARGURA_UTIL)
            elementos += blocos
        else:
            for indice, item in enumerate(itens):
                meia = (LARGURA_UTIL - 8 * mm) / 2
                esquerda = cls._cliente(pedido, e, meia)
                esquerda += cls._item(pedido, item, e, 0, largura_util=meia)
                esquerda += cls._personalizacao_item(item, e, meia)
                if indice == len(itens) - 1:
                    esquerda += cls._financeiro(pedido, e, meia)
                direita = []
                if indice == 0:
                    direita += cls._artes_do_pedido(pedido, e, meia)
                direita += cls._arte(item, e, meia, altura_imagem=52 * mm)
                if not direita:
                    direita = [Paragraph('SEM IMAGENS ANEXADAS', e['pequeno'])]
                pagina = Table([[
                    KeepInFrame(meia, altura_util, esquerda, mode='shrink'),
                    KeepInFrame(meia, altura_util, direita, mode='shrink'),
                ]], colWidths=[meia, meia], rowHeights=[altura_util])
                pagina.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('LEFTPADDING', (0, 0), (0, 0), 0),
                    ('RIGHTPADDING', (0, 0), (0, 0), 4 * mm),
                    ('LEFTPADDING', (1, 0), (1, 0), 4 * mm),
                    ('RIGHTPADDING', (1, 0), (1, 0), 0),
                    ('LINEBEFORE', (1, 0), (1, 0), .5, BORDA),
                ]))
                elementos.append(pagina)
                if indice < len(itens) - 1:
                    elementos.append(PageBreak())

        # A tarja e o rodapé são os dois desenhos de canvas da folha, e os
        # dois valem em TODA página: a marca no topo e a identificação com
        # QR embaixo. Uma função só chama os dois, na ordem em que se lê.
        rodape = cls._rodape(pedido, base_url, e)
        def moldura(canvas, doc_):
            rodape(canvas, doc_)

        doc.build(elementos, onFirstPage=moldura, onLaterPages=moldura)
        return buffer.getvalue()

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _cabecalho(pedido, e) -> list:
        """
        Os dados da casa, abaixo da tarja — o MESMO bloco do orçamento.

        A marca e o título saem no canvas (`desenhar_tarja`), e não aqui: a
        faixa sangra até a borda da folha, e flowable nenhum alcança a
        margem. Aqui fica só o que corre no fluxo.
        """
        dados = [[
            Paragraph('<b>Data do pedido</b>', e['celula']),
            Paragraph('<b>Entrega prevista</b>', e['celula']),
            Paragraph('<b>Status</b>', e['celula']),
            Paragraph('<b>Prioridade</b>', e['celula']),
        ], [
            Paragraph(f'{pedido.data_pedido:%d/%m/%Y}', e['celula']),
            Paragraph(
                f'{pedido.data_prevista_entrega:%d/%m/%Y}'
                if pedido.data_prevista_entrega else '—', e['celula'],
            ),
            Paragraph(pedido.get_status_display(), e['celula']),
            Paragraph(pedido.get_prioridade_display(), e['celula']),
        ]]
        largura = LARGURA_UTIL / 4

        return [
            bloco_empresa(pedido.filial, e, LARGURA_UTIL), Spacer(1, 6),
            _tabela(dados, [largura] * 4, cabecalho=False), Spacer(1, 4),
        ]

    # ── Cliente ──────────────────────────────────────────────────────────

    @staticmethod
    def _cliente(pedido, e, largura_util=LARGURA_UTIL) -> list:
        c = pedido.cliente
        # WhatsApp: o cadastro guarda celular, e é dele que sai o contato de
        # WhatsApp na prática. Um campo próprio não existe — dizer "celular"
        # seria mais honesto, mas o pedido pede WhatsApp e é o mesmo número.
        whatsapp = getattr(c, 'celular', '') or ''
        contato = pedido.contato_nome or (c.nome_fantasia or '')
        telefone = pedido.contato_telefone or getattr(c, 'telefone', '') or ''

        dados = [
            [Paragraph('<b>Cliente</b>', e['celula']),
             Paragraph(esc(c.razao_social), e['celula']),
             Paragraph('<b>CPF/CNPJ</b>', e['celula']),
             Paragraph(esc(getattr(c, 'cpf_cnpj', '')) or '—', e['celula'])],
            [Paragraph('<b>Contato</b>', e['celula']),
             Paragraph(esc(contato) or '—', e['celula']),
             Paragraph('<b>Telefone</b>', e['celula']),
             Paragraph(esc(telefone) or '—', e['celula'])],
            [Paragraph('<b>WhatsApp</b>', e['celula']),
             Paragraph(esc(whatsapp) or '—', e['celula']),
             Paragraph('<b>E-mail</b>', e['celula']),
             Paragraph(esc(getattr(c, 'email', '')) or '—', e['celula'])],
        ]
        largura_rotulo = min(22 * mm, largura_util * .22)
        largura_valor = largura_util / 2 - largura_rotulo
        larguras = [largura_rotulo, largura_valor] * 2

        return [
            Paragraph(f'INFORMAÇÕES DO CLIENTE — PEDIDO #{pedido.numero:06d}', e['secao']),
            _tabela(dados, larguras, cabecalho=False),
        ]

    # ── Arte do pedido ───────────────────────────────────────────────────

    @staticmethod
    def _artes_do_pedido(pedido, e, largura_util=LARGURA_UTIL) -> list:
        """
        A ARTE, desenhada — logo depois do cliente, ainda na primeira página.

        Era o que faltava no documento: o pedido guardava a arte desde o
        primeiro contato, a página do link já a mostrava, e só o PDF saía sem
        ela. Quem recebia o arquivo via a ficha inteira e nenhuma imagem da
        peça que estava comprando.

        Todos os anexos entram no índice do documento. Imagens são exibidas;
        formatos que o ReportLab não desenha aparecem pelo nome para que
        nenhum arquivo anexado fique invisível na conferência.
        """
        nomes_visuais = {
            visual.imagem.name
            for item in pedido.itens.all()
            for visual in item.visuais.all()
            if visual.imagem
        }
        artes = [
            anexo for anexo in pedido.arquivos.all()
            if not anexo.arquivo or anexo.arquivo.name not in nomes_visuais
        ]
        if not artes:
            return []

        # Três por linha: menor que isso desperdiça a folha, maior que isso
        # deixa a arte pequena demais para conferir escudo e numeração.
        por_linha = 3
        largura = largura_util / por_linha
        blocos = [Paragraph('ANEXOS E ARTES', e['secao'])]

        for inicio in range(0, len(artes), por_linha):
            faixa = artes[inicio:inicio + por_linha]
            imagens, rotulos = [], []
            for arte in faixa:
                imagem = (
                    _imagem(arte.arquivo, largura - 6 * mm, 34 * mm)
                    if arte.extensao in DESENHAVEIS else None
                )
                # Arquivo que não abre vira o NOME, e não um buraco: o
                # storage pode estar fora do ar, e some a imagem, não a
                # informação de que ela existe.
                imagens.append(imagem or Paragraph(esc(arte.nome_arquivo), e['pequeno']))
                rotulos.append(Paragraph(
                    arte.descricao or arte.nome_arquivo,
                    ParagraphStyle('rot', parent=e['pequeno'], alignment=1),
                ))
            # A última linha não estica as imagens para preencher a folha.
            while len(imagens) < por_linha:
                imagens.append('')
                rotulos.append('')

            grade = Table([imagens, rotulos], colWidths=[largura] * por_linha)
            grade.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('TOPPADDING', (0, 1), (-1, 1), 3),
                ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
            ]))
            blocos.append(grade)

        return blocos

    # ── Produto, arte e grade ────────────────────────────────────────────

    @classmethod
    def _item(cls, pedido, item, e, indice, largura_util=LARGURA_UTIL) -> list:
        """
        Um produto: identificação, arte e grade.

        UM ABAIXO DO OUTRO, e não uma folha por produto. Antes cada item
        começava em página nova a partir do segundo, para a fábrica poder
        destacar uma folha por peça -- só que um pedido de três camisas
        virava três folhas quase vazias, e quem confere a ficha inteira
        passava a folhear. Agora o produto seguinte ocupa o espaço que
        sobrou.

        O bloco vai em `KeepTogether`: cabendo, o produto fica inteiro na
        mesma folha; não cabendo, desce todo para a próxima. O que não pode
        é o título numa página e a grade na outra -- quem lê no chão de
        fábrica perde a referência de qual peça está olhando.
        """
        blocos = []
        if indice:
            # Uma régua fina separa um produto do outro, já que a quebra de
            # página deixou de fazer esse papel.
            blocos.append(Spacer(1, 5))
            blocos.append(HRFlowable(
                width='100%', thickness=0.5, color=BORDA, spaceAfter=6,
            ))

        blocos.append(Paragraph(f'PRODUTO — {esc(item.nome_exibicao)}', e['secao']))

        observacao, estrutura = _estrutura_item(item)
        campos = [('Produto', item.nome_exibicao)]
        campos += [
            (rotulo, valor) for rotulo, valor in (
                ('Modelo', str(item.modelo) if item.modelo_id else ''),
                ('Cor', str(item.cor) if item.cor_id else ''),
                ('Tecido / Malha', item.tecido_exibicao),
                ('Gola', item.get_gola_display() if item.gola else ''),
                ('Manga', item.get_manga_display() if item.manga else ''),
                ('Acabamento', item.acabamento),
            ) if valor
        ]
        existentes = {rotulo.casefold() for rotulo, _ in campos}
        campos += [par for par in estrutura if par[0].casefold() not in existentes]
        dados = []
        for inicio in range(0, len(campos), 2):
            linha = []
            for rotulo, valor in campos[inicio:inicio + 2]:
                linha += [Paragraph(f'<b>{esc(rotulo)}</b>', e['pequeno']),
                          Paragraph(esc(valor), e['celula'])]
            while len(linha) < 4:
                linha += ['', '']
            dados.append(linha)
        blocos.append(_tabela(
            dados,
            [22 * mm, largura_util / 2 - 22 * mm] * 2,
            cabecalho=False,
        ))

        if observacao:
            blocos.append(Spacer(1, 3))
            blocos.append(Paragraph(esc(observacao), e['pequeno']))

        blocos += cls._grade(item, e, largura_util)
        # Na ficha horizontal, deixar os sub-blocos fluírem aproveita o
        # restante da página. O KeepTogether empurrava o produto inteiro
        # para outra folha mesmo quando ainda havia bastante espaço.
        return blocos

    @staticmethod
    def _arte(item, e, largura_util=LARGURA_UTIL, altura_imagem=27 * mm) -> list:
        personalizacoes = list(item.personalizacoes.all())
        visuais = list(item.visuais.all())
        if not personalizacoes and not visuais:
            return []

        blocos = [Paragraph(f'IMAGENS E IMPRESSÃO — {esc(item.nome_exibicao)}', e['secao'])]

        # Sem limite fixo: cada nova imagem vira mais uma célula e,
        # quando necessário, uma nova linha no PDF.
        por_linha = min(3, max(1, len(visuais)))
        largura = largura_util / por_linha
        for inicio in range(0, len(visuais), por_linha):
            faixa = visuais[inicio:inicio + por_linha]
            celulas = []
            for visual in faixa:
                campo = visual.imagem or (
                    getattr(visual.mockup, 'imagem', None) if visual.mockup_id else None
                )
                imagem = _imagem(campo, largura - 6 * mm, altura_imagem)
                celulas.append(imagem or Paragraph('—', e['pequeno']))
            while len(celulas) < por_linha:
                celulas.append('')
            grade = Table([celulas], colWidths=[largura] * por_linha)
            grade.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 5),
            ]))
            # A linha inteira desce para a página seguinte se não houver
            # espaço suficiente.
            blocos.append(grade)

        for p in personalizacoes:
            partes = [getattr(p, 'get_tecnica_display', lambda: '')() or str(p)]
            if getattr(p, 'observacoes', ''):
                partes.append(p.observacoes)
            texto = Paragraph(' - '.join(esc(x) for x in partes if x), e['normal'])

            # A ARTE APLICADA, desenhada ao lado do texto. Antes saía só
            # "Arte / estampa — peito esquerdo": o chão de fábrica lia ONDE
            # aplicar sem ver O QUE aplicar, e ia procurar a imagem no
            # WhatsApp de quem atendeu.
            arte = (
                _imagem(p.arquivo, 26 * mm, 26 * mm)
                if getattr(p, 'extensao', '') in DESENHAVEIS else None
            )
            if arte is None:
                blocos.append(texto)
                continue

            linha = Table(
                [[arte, texto]], colWidths=[28 * mm, largura_util - 28 * mm],
            )
            linha.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (0, 0), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            blocos.append(linha)

        return blocos

    @staticmethod
    def _grade(item, e, largura_util=LARGURA_UTIL) -> list:
        celulas = list(item.grade.all())
        if not celulas:
            return []

        # Os tamanhos vêm da grade lançada, não de uma lista fixa PP..G3: o
        # pedido pode ter tamanhos infantis ou plus size, e uma coluna fixa
        # esconderia justamente os que fogem do padrão.
        cabecalho = [c.tamanho.sigla for c in celulas] + ['TOTAL']
        valores = [str(c.quantidade) for c in celulas]
        total = sum(c.quantidade for c in celulas)
        valores.append(str(total))

        largura = min(largura_util / len(cabecalho), 22 * mm)
        tabela = _tabela([cabecalho, valores], [largura] * len(cabecalho))
        tabela.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (-1, 1), (-1, 1), 'Helvetica-Bold'),
            ('BACKGROUND', (-1, 1), (-1, 1), FUNDO),
        ]))

        return [Paragraph('GRADE', e['secao']), tabela]

    # ── Personalização individual ────────────────────────────────────────

    @staticmethod
    def _personalizacao(pedido, e, largura_util=LARGURA_UTIL) -> list:
        pessoas = list(pedido.individuais.all())
        if not pessoas:
            return []

        dados = [['#', 'Nome', 'Número', 'Tamanho', 'Produto']]
        for i, p in enumerate(pessoas, start=1):
            dados.append([
                str(i),
                Paragraph(esc(p.nome) or '—', e['celula']),
                p.numero or '—',
                p.tamanho.sigla if p.tamanho_id else '—',
                Paragraph(esc(p.item.nome_exibicao) if p.item_id else '—', e['celula']),
            ])

        larguras = [
            8 * mm, largura_util * 0.28, 15 * mm, 18 * mm,
            largura_util - (41 * mm + largura_util * 0.28),
        ]
        return [
            Spacer(1, 8),
            Paragraph(f'PERSONALIZAÇÃO POR PESSOA — {len(pessoas)}', e['secao']),
            _tabela(dados, larguras),
        ]

    @staticmethod
    def _personalizacao_item(item, e, largura_util=LARGURA_UTIL) -> list:
        """Lista compacta e exclusiva do produto, sem repetir o nome dele."""
        pessoas = list(item.individuais.all())
        if not pessoas:
            return []
        dados = [['#', 'Nome', 'Número', 'Tamanho']]
        for indice, pessoa in enumerate(pessoas, start=1):
            dados.append([
                str(indice), Paragraph(esc(pessoa.nome) or '—', e['celula']),
                pessoa.numero or '—',
                pessoa.tamanho.sigla if pessoa.tamanho_id else '—',
            ])
        larguras = [8 * mm, largura_util - 43 * mm, 17 * mm, 18 * mm]
        return [
            Spacer(1, 4),
            Paragraph(f'PERSONALIZAÇÃO POR PESSOA — {len(pessoas)}', e['secao']),
            _tabela(dados, larguras),
        ]

    # ── Financeiro ───────────────────────────────────────────────────────

    @staticmethod
    def _financeiro(pedido, e, largura_util=LARGURA_UTIL) -> list:
        def brl(valor):
            return f'R$ {valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')

        linhas = [
            ('Quantidade', f'{pedido.quantidade_total} peça(s)'),
            ('Subtotal', brl(pedido.subtotal)),
            ('Desconto', f'− {brl(pedido.desconto or Decimal("0"))}'),
            ('Acréscimo', f'+ {brl(pedido.acrescimo or Decimal("0"))}'),
            ('Frete', f'+ {brl(pedido.frete or Decimal("0"))}'),
            ('TOTAL', brl(pedido.valor_total)),
        ]
        if pedido.entrada:
            linhas += [
                ('Entrada', f'− {brl(pedido.entrada)}'),
                ('Saldo', brl(pedido.saldo)),
            ]

        rotulos_compactos = {
            'Quantidade': 'Peças', 'Desconto': 'Desc.', 'Acréscimo': 'Acrésc.',
        }
        dados = [
            [Paragraph(
                f'<b>{rotulos_compactos.get(rotulo, rotulo)}</b>', e['celula'],
            ) for rotulo, _ in linhas],
            [Paragraph(
                f'<b>{valor}</b>' if rotulo == 'TOTAL' else valor,
                ParagraphStyle(f'v-{indice}', parent=e['celula'], alignment=2),
            ) for indice, (rotulo, valor) in enumerate(linhas)],
        ]

        pagamento = []
        if pedido.forma_pagamento_id:
            pagamento.append(f'Forma: {pedido.forma_pagamento}')
        if pedido.condicao_pagamento_id:
            pagamento.append(f'Condição: {pedido.condicao_pagamento}')

        blocos = [
            Spacer(1, 5),
            Paragraph('FINANCEIRO', e['secao']),
            _tabela(dados, [largura_util / len(linhas)] * len(linhas), cabecalho=False),
        ]
        if pagamento:
            blocos += [Spacer(1, 3), Paragraph(' · '.join(pagamento), e['normal'])]
        if pedido.observacoes:
            blocos += [
                Spacer(1, 6),
                Paragraph('OBSERVAÇÕES', e['secao']),
                # Escapa PRIMEIRO e só então troca a quebra de linha pelo
                # <br/>: na ordem inversa o escape comeria a própria tag.
                Paragraph(esc(pedido.observacoes).replace('\n', '<br/>'), e['normal']),
            ]
        return blocos

    # ── Rodapé ───────────────────────────────────────────────────────────

    @staticmethod
    def _rodape(pedido, base_url, e):
        """
        Devolve o desenhador do rodapé — chamado em toda página.

        O QR aponta para a tela do pedido no sistema. Quem recebe a folha
        impressa chega à versão viva do documento pelo celular, que é o que
        resolve o problema de o papel envelhecer.
        """
        import qrcode

        empresa = pedido.filial.empresa
        destino = (
            f'{base_url}/pedido/{pedido.token_publico}/'
            if base_url else str(pedido.numero)
        )

        imagem = qrcode.make(destino)
        qr_buffer = BytesIO()
        imagem.save(qr_buffer, 'PNG')
        qr_buffer.seek(0)
        # `ImageReader` e não o BytesIO cru: `drawImage` espera caminho ou
        # leitor, e um buffer se esgota na primeira página — o rodapé é
        # desenhado em todas.
        qr = ImageReader(qr_buffer)

        gerado = timezone.localtime().strftime('%d/%m/%Y %H:%M')
        rodape_empresa = ' · '.join(x for x in (
            empresa.razao_social,
            f'CNPJ {empresa.cnpj}' if empresa.cnpj else '',
            getattr(empresa, 'telefone', '') or '',
            getattr(empresa, 'email', '') or '',
        ) if x)

        def desenhar(canvas, doc):
            canvas.saveState()
            y = 10 * mm

            canvas.drawImage(
                qr, MARGEM, y - 2 * mm, width=16 * mm, height=16 * mm,
                preserveAspectRatio=True, mask='auto',
            )
            canvas.setFont('Helvetica-Bold', 8)
            canvas.setFillColor(colors.black)
            canvas.drawString(MARGEM + 19 * mm, y + 9 * mm, f'Pedido #{pedido.numero:06d}')

            canvas.setFont('Helvetica', 6.5)
            canvas.setFillColor(CINZA)
            canvas.drawString(MARGEM + 19 * mm, y + 5.5 * mm, f'Gerado em {gerado}')
            canvas.drawString(MARGEM + 19 * mm, y + 2.5 * mm, rodape_empresa[:110])
            canvas.drawRightString(
                canvas._pagesize[0] - MARGEM, y + 2.5 * mm,
                f'Página {canvas.getPageNumber()}',
            )

            canvas.setStrokeColor(BORDA)
            canvas.line(
                MARGEM, y + 18 * mm,
                canvas._pagesize[0] - MARGEM, y + 18 * mm,
            )
            canvas.restoreState()

        return desenhar
