"""Geracao da carga basica de itens para balancas Toledo Prix via MGV7."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import unicodedata


TAMANHO_LINHA_ITENSMGV_BASICO = 113
TIPOS_PRODUTO_POR_PESO = {'granel_peso', 'granel_volume', 'granel_metragem', 'fracionado'}


class ErroExportacaoMGV7(ValueError):
    """Indica que um ou mais produtos nao podem compor a carga."""


def _somente_ascii(valor):
    normalizado = unicodedata.normalize('NFKD', str(valor or ''))
    sem_acentos = normalizado.encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', sem_acentos).strip().upper()


def _texto(valor, tamanho):
    return _somente_ascii(valor)[:tamanho].ljust(tamanho)


def _inteiro(valor, tamanho, campo):
    texto = str(valor or '').strip()
    if not texto.isdigit() or int(texto) <= 0:
        raise ErroExportacaoMGV7(f'{campo} deve conter somente numeros e ser maior que zero.')
    if len(str(int(texto))) > tamanho:
        raise ErroExportacaoMGV7(f'{campo} deve ter no maximo {tamanho} digitos.')
    return str(int(texto)).zfill(tamanho)


def _preco_centavos(valor):
    try:
        preco = Decimal(str(valor or 0)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ErroExportacaoMGV7('Preco de venda invalido.') from exc
    centavos = int(preco * 100)
    if centavos <= 0:
        raise ErroExportacaoMGV7('Preco de venda deve ser maior que zero.')
    if centavos > 999999:
        raise ErroExportacaoMGV7('Preco de venda excede o limite de R$ 9.999,99 do layout.')
    return str(centavos).zfill(6)


def validar_produto_mgv7(produto):
    """Retorna as pendencias que impedem a exportacao de um produto."""
    erros = []
    try:
        _inteiro(produto.codigo_balanca, 6, 'PLU')
    except ErroExportacaoMGV7 as exc:
        erros.append(str(exc))
    try:
        _preco_centavos(produto.preco_venda)
    except ErroExportacaoMGV7 as exc:
        erros.append(str(exc))
    if not _somente_ascii(produto.descricao_pdv or produto.descricao):
        erros.append('Nome do produto nao informado.')
    return erros


def produto_vendido_por_peso_mgv7(produto):
    return bool(
        produto.vendido_por_peso_granel
        or produto.tipo_produto in TIPOS_PRODUTO_POR_PESO
    )


def linha_item_mgv7(produto, departamento=1):
    """Monta o layout basico de 113 bytes, aceito pelo importador do MGV7."""
    pendencias = validar_produto_mgv7(produto)
    if pendencias:
        raise ErroExportacaoMGV7('; '.join(pendencias))

    departamento_formatado = _inteiro(departamento, 2, 'Departamento')
    plu = _inteiro(produto.codigo_balanca, 6, 'PLU')
    tipo = '0' if produto_vendido_por_peso_mgv7(produto) else '1'
    descricao = _somente_ascii(produto.descricao_pdv or produto.descricao)

    linha = ''.join([
        departamento_formatado,
        tipo,
        plu,
        _preco_centavos(produto.preco_venda),
        '000',
        _texto(descricao[:25], 25),
        _texto(descricao[25:50], 25),
        '000000',
        '0000',
        '000000',
        '0',
        '0',
        '0000',
        ' ' * 12,
        '0' * 11,
    ])
    if len(linha.encode('ascii')) != TAMANHO_LINHA_ITENSMGV_BASICO:
        raise ErroExportacaoMGV7('Falha interna ao montar o layout Itensmgv.txt.')
    return linha


def gerar_itensmgv(produtos, departamento=1):
    """Gera o Itensmgv.txt em ASCII e com terminadores CRLF."""
    produtos = list(produtos)
    plus = {}
    erros = []
    for produto in produtos:
        pendencias = validar_produto_mgv7(produto)
        if pendencias:
            erros.append(f'{produto.descricao}: {"; ".join(pendencias)}')
            continue
        plu_normalizado = str(int(produto.codigo_balanca))
        if plu_normalizado in plus:
            erros.append(
                f'PLU {produto.codigo_balanca} repetido em "{plus[plu_normalizado]}" e "{produto.descricao}".'
            )
        else:
            plus[plu_normalizado] = produto.descricao
    if erros:
        raise ErroExportacaoMGV7('\n'.join(erros))

    linhas = [linha_item_mgv7(produto, departamento) for produto in produtos]
    return (''.join(f'{linha}\r\n' for linha in linhas)).encode('ascii')
