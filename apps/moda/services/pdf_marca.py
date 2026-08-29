"""
A marca da casa nos documentos — tarja do topo e bloco da empresa.

VIVE FORA dos dois serviços de PDF de propósito. O orçamento e o pedido têm
o MESMO cabeçalho, e enquanto ele era código do orçamento, dar o mesmo
cabeçalho ao pedido significava copiar. Duas cópias divergem na primeira
mudança de cor ou de campo, e aí a casa passa a mandar dois documentos que
não parecem da mesma empresa.

As cores foram amostradas do PDF de referência aprovado, não escolhidas a
olho.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, Table, TableStyle

VERMELHO = colors.HexColor('#eb4123')         # tarja do topo
VERMELHO_TABELA = colors.HexColor('#eb2d46')  # cabeçalho de tabela e títulos
FAIXA = colors.HexColor('#f5fafa')            # fundo do título de seção
TEXTO = colors.HexColor('#333333')

MARGEM = 18 * mm
ALTURA_TARJA = 26 * mm
LARGURA_UTIL = A4[0] - 2 * MARGEM
LOGO_PRETA_ERK = Path(__file__).resolve().parent.parent / 'static' / 'moda' / 'img' / 'logo_erk_preta.png'


def esc(valor) -> str:
    """
    Texto do cadastro pronto para entrar num Paragraph.

    O `Paragraph` do reportlab interpreta um dialeto de XML, então `&`, `<` e
    `>` vindos do cadastro precisam ser escapados. Sem isso "L&R SPORTS LTDA"
    saía impresso "L&R; SPORTS LTDA" no documento que vai para o cliente.
    """
    return escape('' if valor is None else str(valor))


def cep(valor: str) -> str:
    digitos = ''.join(ch for ch in (valor or '') if ch.isdigit())
    return f'{digitos[:5]}-{digitos[5:]}' if len(digitos) == 8 else (valor or '')


def cnpj(valor: str) -> str:
    d = ''.join(ch for ch in (valor or '') if ch.isdigit())
    if len(d) != 14:
        return valor or ''
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def logo(filial, largura, altura):
    """
    A logo preta ERK usada nos PDFs da OP e do orçamento.

    O arquivo faz parte do próprio deploy e é aberto pelo caminho do módulo,
    sem depender de collectstatic nem dos dados cadastrados na filial.
    """
    try:
        return Image(
            str(LOGO_PRETA_ERK), width=largura, height=altura, kind='proportional',
        )
    except Exception:
        return None


def estilos_empresa():
    base = getSampleStyleSheet()
    return {
        'empresa': ParagraphStyle(
            'emp', parent=base['Normal'], fontSize=8.5, leading=12, textColor=TEXTO,
        ),
        'empresa_dir': ParagraphStyle(
            'empd', parent=base['Normal'], fontSize=8.5, leading=12,
            textColor=TEXTO, alignment=2,
        ),
    }


def desenhar_tarja(filial, titulo: str, subtitulo: str = ''):
    """
    Devolve o desenhador da faixa vermelha do topo, para o `onPage`.

    Desenhada no CANVAS e não como flowable porque ela sangra de ponta a
    ponta da folha, e flowable nenhum alcança a margem.
    """
    def desenhar(canvas, doc):
        canvas.saveState()
        # Respeita a orientação escolhida pelo documento. O orçamento usa
        # A4 retrato e a ficha de produção usa A4 paisagem.
        largura, altura = canvas._pagesize
        canvas.setFillColor(VERMELHO)
        canvas.rect(0, altura - ALTURA_TARJA, largura, ALTURA_TARJA, stroke=0, fill=1)

        marca = logo(filial, 34 * mm, 16 * mm)
        fim_marca = MARGEM
        if marca is not None:
            l, a = marca.wrapOn(canvas, 34 * mm, 16 * mm)
            marca.drawOn(canvas, MARGEM, altura - ALTURA_TARJA + (ALTURA_TARJA - a) / 2)
            fim_marca = MARGEM + l

        # O título é centralizado no espaço QUE SOBRA à direita da marca.
        # Centralizar na folha inteira jogaria a palavra por cima do logo
        # quando a marca é larga.
        centro = fim_marca + (largura - fim_marca - MARGEM) / 2
        base = altura - ALTURA_TARJA / 2

        canvas.setFillColor(colors.white)
        canvas.setFont('Helvetica-Bold', 22)
        canvas.drawCentredString(centro, base - (2 if subtitulo else 7), titulo)
        if subtitulo:
            canvas.setFont('Helvetica-Bold', 12)
            canvas.drawCentredString(centro, base - 16, subtitulo)
        canvas.restoreState()

    return desenhar


def _campo(filial, empresa, nome: str) -> str:
    """
    O valor da FILIAL e, quando ela não tem, o da EMPRESA.

    Endereço e contato existem nos dois cadastros. Uma filial recém-criada
    costuma vir só com nome e CNPJ, e sem a queda o documento saía com o
    nome e mais nada -- enquanto o endereço estava cadastrado ali do lado,
    na empresa. O que a filial preenche continua vencendo: filial com
    endereço próprio é outro endereço, não um detalhe.
    """
    return (getattr(filial, nome, '') or getattr(empresa, nome, '') or '').strip()


def bloco_empresa(filial, e, largura_util=LARGURA_UTIL) -> Table:
    """
    Os dados da casa em duas colunas: identificação à esquerda, contato à
    direita.

    Montado CAMPO A CAMPO, e não pelo `str(filial)`: aquele devolve
    "Eureka (/)" quando cidade e UF estão em branco, e um endereço com
    parênteses vazios num documento que vai para o cliente parece defeito.

    O NOME E O CNPJ andam juntos, e por isso não caem para a empresa: o
    nome de uma pessoa jurídica ao lado do CNPJ de outra é pior do que um
    campo vazio -- num documento comercial, é erro de identificação.
    """
    empresa = filial.empresa

    endereco = _campo(filial, empresa, 'endereco')
    numero = _campo(filial, empresa, 'numero')
    bairro = _campo(filial, empresa, 'bairro')
    municipio = _campo(filial, empresa, 'cidade')
    uf = _campo(filial, empresa, 'uf')
    codigo_postal = _campo(filial, empresa, 'cep')
    telefone = _campo(filial, empresa, 'telefone')
    email = _campo(filial, empresa, 'email')
    site = (getattr(empresa, 'site', '') or '').strip()

    rua = ', '.join(x for x in [endereco, numero] if x)
    if bairro:
        rua = f'{rua}, {bairro}' if rua else bairro
    cidade = ' - '.join(x for x in [municipio, uf] if x)
    if codigo_postal:
        cidade = (
            f'{cidade} | CEP: {cep(codigo_postal)}' if cidade
            else f'CEP: {cep(codigo_postal)}'
        )

    esquerda = [f'<b>{esc(filial.razao_social)}</b>']
    if rua:
        esquerda.append(esc(rua))
    if cidade:
        esquerda.append(esc(cidade))

    direita = []
    if filial.cnpj:
        direita.append(f'CNPJ: {esc(cnpj(filial.cnpj))}')
    if email:
        direita.append(esc(email))
    contato = ' '.join(
        x for x in [esc(telefone), f'<b>{esc(site)}</b>' if site else ''] if x
    )
    if contato:
        direita.append(contato)

    bloco = Table(
        [[Paragraph('<br/>'.join(esquerda), e['empresa']),
          Paragraph('<br/>'.join(direita), e['empresa_dir'])]],
        colWidths=[largura_util * 0.55, largura_util * 0.45],
    )
    bloco.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return bloco
