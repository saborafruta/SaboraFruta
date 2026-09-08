"""Importacao de XML de NF-e para Entrada de Mercadoria."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from xml.etree import ElementTree

from django.db import DatabaseError, connection
from django.utils import timezone

from apps.core.tenant_context import tenant_atomic
from apps.cadastros.models import Fornecedor, FornecedorFilial
from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.compras.models import EntradaNF, EntradaNFParcela
from apps.core.constants.choices import TipoPessoa
from apps.core.services.exceptions import DadosInvalidosError
from apps.produtos.models import (
    Produto, ProdutoCodigoBarras, ProdutoFornecedorEquivalencia,
)


FORNECEDOR_PADRAO_NOME = 'Fornecedor nao cadastrado'


@dataclass
class ProdutoResolvido:
    produto: Produto | None
    fator_conversao: Decimal
    unidade_estoque: str
    origem: str


@dataclass
class LoteXml:
    numero_lote: str = ''
    quantidade_xml: Decimal = Decimal('0')
    data_fabricacao: date | None = None
    data_validade: date | None = None


class EntradaXMLDuplicadaError(DadosInvalidosError):
    """XML ja importado para uma entrada da filial."""

    def __init__(self, entrada: EntradaNF):
        self.entrada = entrada
        super().__init__('Esta chave de acesso ja foi importada nesta filial.')


def somente_digitos(valor: str | None) -> str:
    return ''.join(ch for ch in str(valor or '') if ch.isdigit())


def normalizar_chave(chave: str | None) -> str:
    return somente_digitos(chave)


def decimal_xml(valor: str | None, default: str = '0') -> Decimal:
    try:
        return Decimal(str(valor or default).strip() or default)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def texto(el, path: str, default: str = '') -> str:
    found = el.find(path)
    return (found.text or '').strip() if found is not None and found.text else default


def _ns(root):
    if root.tag.startswith('{'):
        return {'nfe': root.tag.split('}')[0].strip('{')}
    return {}


def _path(ns, path):
    if not ns:
        return path.replace('nfe:', '')
    return path.replace('nfe:', f'{{{ns["nfe"]}}}')


def get_fornecedor_padrao(filial) -> Fornecedor:
    fornecedor = Fornecedor.objects.for_filial(filial).filter(
        razao_social=FORNECEDOR_PADRAO_NOME,
    ).first()
    if fornecedor:
        FornecedorFilial.objects.get_or_create(
            fornecedor=fornecedor,
            filial=filial,
            defaults={'ativo': True},
        )
        return fornecedor

    fornecedor = Fornecedor.objects.create(
        filial=filial,
        tipo_pessoa=TipoPessoa.JURIDICA,
        razao_social=FORNECEDOR_PADRAO_NOME,
        nome_fantasia=FORNECEDOR_PADRAO_NOME,
        cpf_cnpj='',
        uf=filial.uf,
        observacao=(
            'Fornecedor tecnico usado em entradas de NF quando o emitente ainda '
            'nao foi cadastrado.'
        ),
        ativo=True,
    )
    FornecedorFilial.objects.get_or_create(
        fornecedor=fornecedor,
        filial=filial,
        defaults={'ativo': True},
    )
    return fornecedor


def criar_fornecedor_por_emitente_xml(
    filial,
    emitente: dict,
    exigir_dados: bool = False,
) -> Fornecedor | None:
    documento = somente_digitos(emitente.get('documento', ''))
    razao_social = (emitente.get('razao_social') or '').strip()
    if not documento or not razao_social:
        if exigir_dados:
            raise DadosInvalidosError('XML sem documento ou razao social do emitente para cadastrar fornecedor.')
        return None

    fornecedor = Fornecedor.objects.for_filial(filial).filter(
        cpf_cnpj=documento,
        ativo=True,
    ).first()
    if fornecedor:
        return fornecedor

    fornecedor = Fornecedor.objects.create(
        filial=filial,
        tipo_pessoa=TipoPessoa.FISICA if len(documento) == 11 else TipoPessoa.JURIDICA,
        razao_social=razao_social,
        nome_fantasia=emitente.get('nome_fantasia') or razao_social[:100],
        cpf_cnpj=documento,
        inscricao_estadual=emitente.get('ie', ''),
        endereco=emitente.get('endereco', ''),
        cidade=emitente.get('municipio', ''),
        uf=(emitente.get('uf', '') or '')[:2],
        cep=somente_digitos(emitente.get('cep', ''))[:8],
        telefone=emitente.get('telefone', ''),
        observacao='Criado automaticamente a partir do XML de entrada.',
        ativo=True,
    )
    ReplicacaoCadastrosService.sincronizar_fornecedor(fornecedor)
    return fornecedor


def atualizar_equivalencias_fornecedor_xml(filial, fornecedor: Fornecedor, documento_xml: str) -> int:
    documento = somente_digitos(documento_xml)
    if not documento:
        return 0
    return ProdutoFornecedorEquivalencia.objects.filter(
        fornecedor__isnull=True,
        fornecedor_cnpj_xml=documento,
        produto__filiais_vinculo__filial=filial,
        produto__filiais_vinculo__ativo=True,
    ).update(fornecedor=fornecedor)


def localizar_fornecedor(
    filial,
    documento: str,
    emitente_xml: dict | None = None,
    cadastrar_automaticamente: bool = False,
) -> tuple[Fornecedor, bool]:
    doc = somente_digitos(documento)
    if doc:
        fornecedor = Fornecedor.objects.for_filial(filial).filter(cpf_cnpj=doc, ativo=True).first()
        if fornecedor:
            return fornecedor, False
    if cadastrar_automaticamente and emitente_xml:
        fornecedor = criar_fornecedor_por_emitente_xml(filial, emitente_xml)
        if fornecedor:
            return fornecedor, False
    return get_fornecedor_padrao(filial), True


def _ean_valido(ean: str) -> bool:
    if not ean:
        return False
    ean_limpo = ean.strip().upper()
    return ean_limpo not in {'SEM GTIN', 'SEMGTIN', 'ISENTO'}


def _buscar_produto_por_ean_extra(filial, ean: str) -> Produto | None:
    qs = Produto.objects.for_filial(filial).filter(ativo=True).select_related('unidade_medida')
    if connection.vendor == 'sqlite':
        return next((produto for produto in qs if ean in (produto.codigos_barras_extras or [])), None)
    try:
        return qs.filter(codigos_barras_extras__contains=[ean]).first()
    except DatabaseError:
        return next((produto for produto in qs if ean in (produto.codigos_barras_extras or [])), None)


def resolver_produto(
    filial,
    ean: str = '',
    codigo_fornecedor: str = '',
    fornecedor: Fornecedor | None = None,
    fornecedor_cnpj_xml: str = '',
) -> ProdutoResolvido:
    ean = ean.strip()
    if _ean_valido(ean):
        produto = Produto.objects.for_filial(filial).filter(
            ativo=True,
            codigo_barras=ean,
        ).select_related('unidade_medida').first()
        if produto:
            return ProdutoResolvido(produto, Decimal('1'), produto.unidade_medida.sigla, 'codigo_barras')

        produto = _buscar_produto_por_ean_extra(filial, ean)
        if produto:
            return ProdutoResolvido(produto, Decimal('1'), produto.unidade_medida.sigla, 'ean_alternativo_json')

        codigo = (
            ProdutoCodigoBarras.objects
            .filter(ean=ean, ativo=True, produto__ativo=True)
            .select_related('produto', 'produto__unidade_medida')
            .filter(produto__filiais_vinculo__filial=filial, produto__filiais_vinculo__ativo=True)
            .first()
        )
        if codigo:
            return ProdutoResolvido(
                codigo.produto,
                codigo.quantidade_conversao or Decimal('1'),
                codigo.produto.unidade_medida.sigla,
                'produto_codigo_barras',
            )

    filtros = {'ativo': True}
    if fornecedor:
        filtros['fornecedor'] = fornecedor
    elif fornecedor_cnpj_xml:
        filtros['fornecedor_cnpj_xml'] = fornecedor_cnpj_xml
    equivalencia = None
    if codigo_fornecedor:
        equivalencia = (
            ProdutoFornecedorEquivalencia.objects
            .filter(codigo_fornecedor=codigo_fornecedor, **filtros)
            .select_related('produto', 'produto__unidade_medida')
            .first()
        )
    if not equivalencia and _ean_valido(ean):
        equivalencia = (
            ProdutoFornecedorEquivalencia.objects
            .filter(ean_utilizado=ean, **filtros)
            .select_related('produto', 'produto__unidade_medida')
            .first()
        )
    if equivalencia:
        produto = equivalencia.produto
        return ProdutoResolvido(
            produto,
            equivalencia.fator_conversao or Decimal('1'),
            equivalencia.unidade_estoque or produto.unidade_medida.sigla,
            'equivalencia_fornecedor',
        )

    return ProdutoResolvido(None, Decimal('1'), '', 'sem_vinculo')


def _inf_nfe(root):
    ns = _ns(root)
    inf = root.find(_path(ns, './/nfe:infNFe'), ns)
    if inf is None:
        inf = root.find('.//infNFe')
    if inf is None:
        raise DadosInvalidosError('XML nao parece ser uma NF-e valida: infNFe nao encontrado.')
    return inf, ns


def _emitente(inf, ns):
    emit = inf.find(_path(ns, 'nfe:emit'), ns) or inf.find('emit')
    if emit is None:
        return {}
    ender = emit.find(_path(ns, 'nfe:enderEmit'), ns) or emit.find('enderEmit')
    doc = texto(emit, _path(ns, 'nfe:CNPJ')) or texto(emit, 'CNPJ')
    if not doc:
        doc = texto(emit, _path(ns, 'nfe:CPF')) or texto(emit, 'CPF')
    return {
        'documento': somente_digitos(doc),
        'razao_social': texto(emit, _path(ns, 'nfe:xNome')) or texto(emit, 'xNome'),
        'nome_fantasia': texto(emit, _path(ns, 'nfe:xFant')) or texto(emit, 'xFant'),
        'ie': texto(emit, _path(ns, 'nfe:IE')) or texto(emit, 'IE'),
        'endereco': ' '.join(part for part in [
            texto(ender, _path(ns, 'nfe:xLgr')) if ender is not None else '',
            texto(ender, _path(ns, 'nfe:nro')) if ender is not None else '',
        ] if part).strip(),
        'municipio': texto(ender, _path(ns, 'nfe:xMun')) if ender is not None else '',
        'uf': texto(ender, _path(ns, 'nfe:UF')) if ender is not None else '',
        'cep': texto(ender, _path(ns, 'nfe:CEP')) if ender is not None else '',
        'telefone': texto(ender, _path(ns, 'nfe:fone')) if ender is not None else '',
    }


def _destinatario(inf, ns):
    dest = inf.find(_path(ns, 'nfe:dest'), ns) or inf.find('dest')
    if dest is None:
        return {}
    doc = texto(dest, _path(ns, 'nfe:CNPJ')) or texto(dest, 'CNPJ')
    if not doc:
        doc = texto(dest, _path(ns, 'nfe:CPF')) or texto(dest, 'CPF')
    return {
        'documento': somente_digitos(doc),
        'nome': texto(dest, _path(ns, 'nfe:xNome')) or texto(dest, 'xNome'),
    }


def _data_emissao(inf, ns):
    raw = (
        texto(inf, _path(ns, 'nfe:ide/nfe:dhEmi'))
        or texto(inf, _path(ns, 'nfe:ide/nfe:dEmi'))
        or texto(inf, 'ide/dhEmi')
        or texto(inf, 'ide/dEmi')
    )
    if raw:
        try:
            return timezone.datetime.fromisoformat(raw[:10]).date()
        except ValueError:
            pass
    return timezone.localdate()


def _data_xml(valor: str | None):
    raw = (valor or '').strip()
    if not raw:
        return None
    try:
        return timezone.datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def _chave_acesso(inf):
    chave = (inf.attrib.get('Id') or '').replace('NFe', '')
    return normalizar_chave(chave)


def extrair_chave_xml(xml_texto: str) -> str:
    try:
        root = ElementTree.fromstring(xml_texto)
    except ElementTree.ParseError as exc:
        raise DadosInvalidosError(f'XML invalido: {exc}') from exc

    inf, _ = _inf_nfe(root)
    return _chave_acesso(inf)


def _totais(inf, ns):
    total = inf.find(_path(ns, 'nfe:total/nfe:ICMSTot'), ns) or inf.find('total/ICMSTot')
    if total is None:
        return {}
    return {
        'valor_produtos': decimal_xml(texto(total, _path(ns, 'nfe:vProd')) or texto(total, 'vProd')),
        'valor_frete': decimal_xml(texto(total, _path(ns, 'nfe:vFrete')) or texto(total, 'vFrete')),
        'valor_seguro': decimal_xml(texto(total, _path(ns, 'nfe:vSeg')) or texto(total, 'vSeg')),
        'valor_desconto': decimal_xml(texto(total, _path(ns, 'nfe:vDesc')) or texto(total, 'vDesc')),
        'valor_outras_despesas': decimal_xml(texto(total, _path(ns, 'nfe:vOutro')) or texto(total, 'vOutro')),
        'valor_ipi': decimal_xml(texto(total, _path(ns, 'nfe:vIPI')) or texto(total, 'vIPI')),
        'valor_icms': decimal_xml(texto(total, _path(ns, 'nfe:vICMS')) or texto(total, 'vICMS')),
        'valor_icms_st': decimal_xml(texto(total, _path(ns, 'nfe:vST')) or texto(total, 'vST')),
        'valor_total': decimal_xml(texto(total, _path(ns, 'nfe:vNF')) or texto(total, 'vNF')),
    }


def _parcelas_financeiras(inf, ns) -> list[dict]:
    parcelas = []
    duplicatas = inf.findall(_path(ns, 'nfe:cobr/nfe:dup'), ns) or inf.findall('cobr/dup')
    for index, dup in enumerate(duplicatas, start=1):
        valor = decimal_xml(texto(dup, _path(ns, 'nfe:vDup')) or texto(dup, 'vDup'))
        if valor <= 0:
            continue
        numero = texto(dup, _path(ns, 'nfe:nDup')) or texto(dup, 'nDup') or str(index)
        vencimento = _data_xml(texto(dup, _path(ns, 'nfe:dVenc')) or texto(dup, 'dVenc'))
        parcelas.append({
            'numero': numero[:40],
            'data_vencimento': vencimento,
            'valor': valor,
            'observacao': 'Importada da duplicata informada no XML.',
        })

    if parcelas:
        return parcelas

    fatura = inf.find(_path(ns, 'nfe:cobr/nfe:fat'), ns) or inf.find('cobr/fat')
    if fatura is None:
        return []
    valor_fatura = (
        decimal_xml(texto(fatura, _path(ns, 'nfe:vLiq')) or texto(fatura, 'vLiq'))
        or decimal_xml(texto(fatura, _path(ns, 'nfe:vOrig')) or texto(fatura, 'vOrig'))
    )
    if valor_fatura <= 0:
        return []
    numero = texto(fatura, _path(ns, 'nfe:nFat')) or texto(fatura, 'nFat') or '1'
    return [{
        'numero': numero[:40],
        'data_vencimento': None,
        'valor': valor_fatura,
        'observacao': 'Importada da fatura informada no XML; revise o vencimento.',
    }]


def _data_texto_br(valor: str | None) -> date | None:
    raw = (valor or '').strip()
    if not raw:
        return None
    for separador in ('/', '-'):
        partes = raw.split(separador)
        if len(partes) != 3:
            continue
        try:
            dia, mes, ano = (int(parte) for parte in partes)
        except ValueError:
            continue
        if ano < 100:
            ano += 2000
        try:
            return date(ano, mes, dia)
        except ValueError:
            return None
    return None


def _rastros_inf_ad_prod(texto_livre: str) -> list[LoteXml]:
    texto_livre = (texto_livre or '').strip()
    if not texto_livre:
        return []

    lote_match = re.search(
        r'\b(?:lote|lot\.?|lt)\s*[:\-]?\s*([A-Z0-9][A-Z0-9./_-]{0,59})',
        texto_livre,
        flags=re.IGNORECASE,
    )
    validade_match = re.search(
        r'\b(?:val(?:idade)?\.?|venc(?:imento)?\.?)\s*[:\-]?\s*(\d{2}[/-]\d{2}[/-]\d{2,4})',
        texto_livre,
        flags=re.IGNORECASE,
    )
    fabricacao_match = re.search(
        r'\b(?:fab(?:ricacao)?\.?)\s*[:\-]?\s*(\d{2}[/-]\d{2}[/-]\d{2,4})',
        texto_livre,
        flags=re.IGNORECASE,
    )
    if not any((lote_match, validade_match, fabricacao_match)):
        return []

    return [LoteXml(
        numero_lote=(lote_match.group(1).rstrip('.,;') if lote_match else '')[:60],
        data_fabricacao=_data_texto_br(fabricacao_match.group(1) if fabricacao_match else None),
        data_validade=_data_texto_br(validade_match.group(1) if validade_match else None),
    )]


def _rastros_item(det_xml, prod_xml, ns) -> list[LoteXml]:
    rastros = []
    for rastro in prod_xml.findall(_path(ns, 'nfe:rastro'), ns) or prod_xml.findall('rastro'):
        numero_lote = (
            texto(rastro, _path(ns, 'nfe:nLote'))
            or texto(rastro, 'nLote')
        ).strip()
        quantidade = decimal_xml(
            texto(rastro, _path(ns, 'nfe:qLote')) or texto(rastro, 'qLote'),
        )
        data_fabricacao = _data_xml(
            texto(rastro, _path(ns, 'nfe:dFab')) or texto(rastro, 'dFab'),
        )
        data_validade = _data_xml(
            texto(rastro, _path(ns, 'nfe:dVal')) or texto(rastro, 'dVal'),
        )
        if numero_lote or quantidade > 0 or data_fabricacao or data_validade:
            rastros.append(LoteXml(
                numero_lote=numero_lote[:60],
                quantidade_xml=quantidade,
                data_fabricacao=data_fabricacao,
                data_validade=data_validade,
            ))
    if rastros:
        return rastros

    inf_ad_prod = (
        texto(det_xml, _path(ns, 'nfe:infAdProd'))
        or texto(det_xml, 'infAdProd')
    )
    return _rastros_inf_ad_prod(inf_ad_prod)


def _linhas_por_lote_xml(
    rastros: list[LoteXml],
    quantidade_xml: Decimal,
    valor_total: Decimal,
) -> list[dict]:
    if not rastros:
        return [{
            'quantidade_xml': quantidade_xml,
            'valor_total': valor_total,
            'lote': LoteXml(),
            'observacao': '',
        }]

    if len(rastros) == 1:
        lote = rastros[0]
        return [{
            'quantidade_xml': lote.quantidade_xml or quantidade_xml,
            'valor_total': valor_total,
            'lote': lote,
            'observacao': 'Lote importado do grupo rastro da NF-e.',
        }]

    rastros_com_quantidade = [lote for lote in rastros if lote.quantidade_xml > 0]
    if rastros_com_quantidade:
        rastros = rastros_com_quantidade
    total_lotes = sum((lote.quantidade_xml for lote in rastros), Decimal('0'))
    if total_lotes <= 0:
        lote = rastros[0]
        return [{
            'quantidade_xml': quantidade_xml,
            'valor_total': valor_total,
            'lote': lote,
            'observacao': (
                'XML informou multiplos lotes sem quantidade por lote; '
                'foi usado o primeiro lote para revisao manual.'
            ),
        }]

    linhas = []
    valor_alocado = Decimal('0')
    for indice, lote in enumerate(rastros, start=1):
        if indice == len(rastros):
            valor_linha = valor_total - valor_alocado
        else:
            valor_linha = (
                (valor_total * lote.quantidade_xml / total_lotes)
                .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            )
            valor_alocado += valor_linha
        linhas.append({
            'quantidade_xml': lote.quantidade_xml,
            'valor_total': valor_linha,
            'lote': lote,
            'observacao': f'Lote {indice}/{len(rastros)} importado do grupo rastro da NF-e.',
        })
    return linhas


def importar_xml_para_entrada(xml_texto: str, filial, usuario, nome_arquivo: str = '') -> EntradaNF:
    try:
        root = ElementTree.fromstring(xml_texto)
    except ElementTree.ParseError as exc:
        raise DadosInvalidosError(f'XML invalido: {exc}') from exc

    inf, ns = _inf_nfe(root)
    chave = _chave_acesso(inf)
    if chave and len(chave) != 44:
        raise DadosInvalidosError('Chave de acesso do XML deve ter 44 digitos.')
    if chave:
        entrada_existente = EntradaNF.objects.for_filial(filial).filter(chave_acesso_nf=chave).first()
        if entrada_existente:
            raise EntradaXMLDuplicadaError(entrada_existente)

    emitente = _emitente(inf, ns)
    destinatario = _destinatario(inf, ns)
    totais = _totais(inf, ns)
    parcelas_xml = _parcelas_financeiras(inf, ns)
    numero_nf = texto(inf, _path(ns, 'nfe:ide/nfe:nNF')) or texto(inf, 'ide/nNF') or (chave[25:34].lstrip('0') if chave else '')
    serie_nf = texto(inf, _path(ns, 'nfe:ide/nfe:serie')) or texto(inf, 'ide/serie') or (chave[22:25].lstrip('0') if chave else '1')
    doc_filial = somente_digitos(getattr(filial, 'cnpj', ''))
    doc_dest = destinatario.get('documento', '')

    with tenant_atomic():
        fornecedor, fornecedor_pendente = localizar_fornecedor(
            filial,
            emitente.get('documento', ''),
            emitente_xml=emitente,
            cadastrar_automaticamente=True,
        )
        entrada = EntradaNF.objects.create(
            filial=filial,
            fornecedor=fornecedor,
            numero_nf=numero_nf or 'SEM-NUMERO',
            serie_nf=serie_nf or '1',
            chave_acesso_nf=chave,
            origem_entrada=EntradaNF.OrigemEntrada.XML,
            xml_original=xml_texto,
            xml_nome_arquivo=nome_arquivo[:180],
            destinatario_documento_xml=doc_dest,
            destinatario_nome_xml=destinatario.get('nome', ''),
            destinatario_documento_diferente=bool(doc_dest and doc_filial and doc_dest != doc_filial),
            data_emissao_nf=_data_emissao(inf, ns),
            data_entrada=timezone.now(),
            status=EntradaNF.Status.AGUARDANDO_CONFERENCIA,
            usuario=usuario,
            fornecedor_pendente=fornecedor_pendente,
            emitente_cnpj_xml=emitente.get('documento', ''),
            emitente_razao_social_xml=emitente.get('razao_social', ''),
            emitente_nome_fantasia_xml=emitente.get('nome_fantasia', ''),
            emitente_ie_xml=emitente.get('ie', ''),
            emitente_endereco_xml=emitente.get('endereco', ''),
            emitente_municipio_xml=emitente.get('municipio', ''),
            emitente_uf_xml=emitente.get('uf', ''),
            emitente_cep_xml=emitente.get('cep', ''),
            emitente_telefone_xml=emitente.get('telefone', ''),
            observacao='Importada por XML.',
            **totais,
        )

        tem_bloqueio = fornecedor_pendente
        from apps.compras.services.compra_service import CompraService
        for det in inf.findall(_path(ns, 'nfe:det'), ns) or inf.findall('det'):
            prod_xml = det.find(_path(ns, 'nfe:prod'), ns) or det.find('prod')
            if prod_xml is None:
                continue
            ean = texto(prod_xml, _path(ns, 'nfe:cEAN')) or texto(prod_xml, 'cEAN')
            ncm = somente_digitos(texto(prod_xml, _path(ns, 'nfe:NCM')) or texto(prod_xml, 'NCM'))[:8]
            codigo_fornecedor = texto(prod_xml, _path(ns, 'nfe:cProd')) or texto(prod_xml, 'cProd')
            descricao = texto(prod_xml, _path(ns, 'nfe:xProd')) or texto(prod_xml, 'xProd')
            unidade_xml = texto(prod_xml, _path(ns, 'nfe:uCom')) or texto(prod_xml, 'uCom')
            quantidade_xml = decimal_xml(texto(prod_xml, _path(ns, 'nfe:qCom')) or texto(prod_xml, 'qCom'))
            valor_unitario = decimal_xml(texto(prod_xml, _path(ns, 'nfe:vUnCom')) or texto(prod_xml, 'vUnCom'))
            valor_total = decimal_xml(texto(prod_xml, _path(ns, 'nfe:vProd')) or texto(prod_xml, 'vProd'))
            rastros = _rastros_item(det, prod_xml, ns)
            resolvido = resolver_produto(
                filial=filial,
                ean=ean,
                codigo_fornecedor=codigo_fornecedor,
                fornecedor=None if fornecedor_pendente else fornecedor,
                fornecedor_cnpj_xml=emitente.get('documento', ''),
            )
            for linha_lote in _linhas_por_lote_xml(rastros, quantidade_xml, valor_total):
                quantidade_linha = linha_lote['quantidade_xml']
                valor_linha = linha_lote['valor_total']
                lote_xml = linha_lote['lote']
                quantidade_estoque = quantidade_linha * resolvido.fator_conversao
                valor_unitario_estoque = (
                    valor_linha / quantidade_estoque
                    if quantidade_estoque
                    else valor_unitario
                )
                item = entrada.itens.create(
                    produto=resolvido.produto,
                    numero_item=entrada.itens.count() + 1,
                    quantidade=quantidade_estoque,
                    quantidade_xml=quantidade_linha,
                    quantidade_estoque=quantidade_estoque,
                    quantidade_recebida=quantidade_estoque,
                    unidade_xml=unidade_xml,
                    unidade_estoque=resolvido.unidade_estoque,
                    fator_conversao=resolvido.fator_conversao,
                    valor_unitario=valor_unitario_estoque,
                    valor_bruto=valor_linha,
                    valor_total=valor_linha,
                    numero_lote=lote_xml.numero_lote,
                    data_fabricacao=lote_xml.data_fabricacao,
                    data_validade=lote_xml.data_validade,
                    ean_xml=ean,
                    ncm_xml=ncm,
                    codigo_produto_fornecedor=codigo_fornecedor,
                    descricao_xml=descricao,
                    observacao=linha_lote['observacao'],
                )
                item.calcular_totais()
                item.save()

                CompraService.atualizar_diferenca_item(item)
                if item.diferenca_bloqueante:
                    tem_bloqueio = True

        for parcela_xml in parcelas_xml:
            EntradaNFParcela.objects.create(
                entrada=entrada,
                numero=parcela_xml['numero'],
                data_vencimento=parcela_xml['data_vencimento'],
                valor=parcela_xml['valor'],
                origem=EntradaNFParcela.Origem.XML,
                status=EntradaNFParcela.Status.PENDENTE,
                fornecedor_pendente=fornecedor_pendente,
                emitente_documento_xml=emitente.get('documento', ''),
                emitente_nome_xml=emitente.get('razao_social', ''),
                observacao=parcela_xml['observacao'],
            )

        if tem_bloqueio:
            entrada.status = EntradaNF.Status.AGUARDANDO_VINCULOS
            entrada.save(update_fields=['status', 'updated_at'])
        else:
            CompraService._atualizar_status_conferencia(entrada)
        return entrada
