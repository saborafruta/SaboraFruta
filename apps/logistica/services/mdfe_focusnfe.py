"""Emissão e eventos de MDF-e rodoviário pela Focus NFe."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.core.services.exceptions import DadosInvalidosError
from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.financeiro.models import DocumentoFiscal
from apps.fiscal.integrations.focusnfe import FocusNFeClient
from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
from apps.fiscal.integrations.focusnfe.exceptions import FocusNFeProcessingError
from apps.fiscal.services.focusnfe_service import FocusNFeService, gerar_ref
from apps.logistica.models import DocumentoMDFe, MDFe


RODADO_FOCUS = {
    "Truck": "01",
    "Toco": "02",
    "Carreta": "03",
    "Van": "04",
    "VUC": "05",
    "Furgão": "05",
    "Carro": "05",
    "Moto": "06",
}

CARROCERIA_FOCUS = {
    "Aberta": "01",
    "Fechada": "02",
    "Baú": "02",
    "Graneleira": "03",
    "Porta-container": "04",
    "Sider": "05",
    "Cegonha": "00",
}


def _digitos(valor: Any) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _texto(valor: Any) -> str:
    return " ".join(str(valor or "").split()).strip()


def _cliente_focus(filial) -> FocusNFeClient:
    token = _texto(getattr(filial, "focusnfe_token", ""))
    if not token:
        raise DadosInvalidosError(
            "Configure o Token de emissão Focus na filial de origem antes de emitir o MDF-e."
        )
    ambiente = getattr(filial, "focusnfe_ambiente", None)
    return FocusNFeClient(config=FocusNFeConfig.from_env(token=token, ambiente=ambiente))


def _validar_filial_mdfe(filial, *, destino=False) -> None:
    campos = {
        "CNPJ": _digitos(filial.cnpj),
        "razão social": _texto(filial.razao_social),
        "inscrição estadual": _texto(filial.inscricao_estadual),
        "logradouro": _texto(filial.endereco),
        "número": _texto(filial.numero),
        "bairro": _texto(filial.bairro),
        "município": _texto(filial.cidade),
        "código IBGE do município": _digitos(filial.codigo_municipio_ibge),
        "UF": _texto(filial.uf),
    }
    obrigatorios = (
        ("CNPJ", 14),
        ("código IBGE do município", 7),
    )
    invalidos = [nome for nome, tamanho in obrigatorios if len(campos[nome]) != tamanho]
    invalidos += [
        nome
        for nome in ("razão social", "município", "UF")
        if not campos[nome]
    ]
    if not destino:
        invalidos += [
            nome
            for nome in ("inscrição estadual", "logradouro", "número", "bairro")
            if not campos[nome]
        ]
    if invalidos:
        alvo = "destino" if destino else "origem"
        raise DadosInvalidosError(
            f"Complete o cadastro da filial de {alvo} para emitir o MDF-e: "
            + ", ".join(dict.fromkeys(invalidos))
            + "."
        )


def _validar_transporte(motorista, veiculo, peso_bruto: Decimal) -> None:
    erros = []
    if len(_digitos(motorista.cpf)) != 11:
        erros.append("CPF do motorista")
    if len(_texto(veiculo.placa).replace("-", "")) != 7:
        erros.append("placa do veículo")
    if not veiculo.tara or veiculo.tara <= 0:
        erros.append("tara do veículo")
    if not veiculo.uf_placa:
        erros.append("UF de licenciamento do veículo")
    if not getattr(veiculo, "tipo_rodado", ""):
        erros.append("tipo de rodado do veículo")
    if not getattr(veiculo, "tipo_carroceria", ""):
        erros.append("tipo de carroceria do veículo")
    if peso_bruto <= 0:
        erros.append("peso bruto da carga")
    if erros:
        raise DadosInvalidosError(
            "Antes de emitir o MDF-e, informe: " + ", ".join(erros) + "."
        )


def construir_payload_mdfe(mdfe: MDFe) -> dict[str, Any]:
    filial = mdfe.filial
    _validar_filial_mdfe(filial)

    documentos = list(
        mdfe.documentos.select_related("documento_fiscal").filter(tipo_documento="nfe")
    )
    if not documentos:
        raise DadosInvalidosError("Adicione ao MDF-e ao menos uma NF-e autorizada.")
    chaves = []
    for vinculo in documentos:
        documento = vinculo.documento_fiscal
        chave = _digitos(
            documento.chave if documento else vinculo.chave_acesso
        )
        if documento and documento.status != StatusDocumentoFiscal.AUTORIZADA:
            raise DadosInvalidosError(
                f"A NF-e nº {documento.numero} ainda não foi autorizada."
            )
        if len(chave) != 44:
            raise DadosInvalidosError(
                "A NF-e vinculada ainda não possui chave de acesso autorizada."
            )
        chaves.append(chave)

    cnpj = _digitos(filial.cnpj)
    placa = _texto(mdfe.veiculo_placa).replace("-", "").upper()
    cpf = _digitos(mdfe.motorista_cpf)
    tara = int(Decimal(str(getattr(mdfe, "_veiculo_tara", 0) or 0)))
    uf_placa = _texto(getattr(mdfe, "_veiculo_uf", ""))
    tipo_rodado = _texto(getattr(mdfe, "_veiculo_tipo_rodado", ""))
    tipo_carroceria = _texto(getattr(mdfe, "_veiculo_tipo_carroceria", ""))

    # Instâncias recarregadas usam os dados persistidos no campo de observação técnica.
    metadados = mdfe.transporte_metadados or {}
    if not metadados and placa:
        from apps.cadastros.models import Veiculo

        veiculo = next(
            (
                item
                for item in Veiculo.objects.for_filial(filial).filter(ativo=True)
                if _texto(item.placa).replace("-", "").upper() == placa
            ),
            None,
        )
        if veiculo:
            metadados = {
                "tara": str(veiculo.tara or ""),
                "capacidade_kg": str(veiculo.capacidade_kg or ""),
                "renavam": veiculo.renavam,
                "uf_placa": veiculo.uf_placa,
                "tipo_rodado": veiculo.tipo_rodado,
                "tipo_carroceria": veiculo.tipo_carroceria,
            }
    tara = tara or int(Decimal(str(metadados.get("tara") or 0)))
    uf_placa = uf_placa or _texto(metadados.get("uf_placa"))
    tipo_rodado = tipo_rodado or _texto(metadados.get("tipo_rodado"))
    tipo_carroceria = tipo_carroceria or _texto(metadados.get("tipo_carroceria"))

    if (
        len(cpf) != 11
        or len(placa) != 7
        or tara <= 0
        or not uf_placa
        or not tipo_rodado
        or not tipo_carroceria
    ):
        raise DadosInvalidosError(
            "Revise motorista e veículo do MDF-e: CPF, placa, tara, UF, tipo de "
            "rodado e tipo de carroceria são obrigatórios."
        )

    veiculo_tracao = {
        "codigo_veiculo": placa,
        "placa_veiculo": placa,
        "tara_veiculo": tara,
        "condutores": [{"nome": _texto(mdfe.motorista_nome)[:60], "cpf": cpf}],
        "tipo_rodado_veiculo": RODADO_FOCUS.get(tipo_rodado, "06"),
        "tipo_carroceria_veiculo": CARROCERIA_FOCUS.get(tipo_carroceria, "00"),
        "uf_licenciamento_veiculo": uf_placa[:2].upper(),
    }
    renavam = _digitos(metadados.get("renavam"))
    if 9 <= len(renavam) <= 11:
        veiculo_tracao["renavam_veiculo"] = renavam
    capacidade = int(Decimal(str(metadados.get("capacidade_kg") or 0)))
    if capacidade > 0:
        veiculo_tracao["capacidade_kg_veiculo"] = capacidade

    codigo_carregamento = _digitos(
        mdfe.codigo_municipio_carregamento or filial.codigo_municipio_ibge
    )
    codigo_descarregamento = _digitos(mdfe.codigo_municipio_descarregamento)
    municipio_carregamento = _texto(mdfe.municipio_carregamento)
    municipio_descarregamento = _texto(mdfe.municipio_descarregamento)
    uf_carregamento = _texto(mdfe.uf_carregamento).upper()
    uf_descarregamento = _texto(mdfe.uf_descarregamento).upper()
    erros_rota = []
    if len(codigo_carregamento) != 7 or not municipio_carregamento:
        erros_rota.append("municipio de carregamento com codigo IBGE")
    if len(codigo_descarregamento) != 7 or not municipio_descarregamento:
        erros_rota.append("municipio de descarregamento com codigo IBGE")
    if len(uf_carregamento) != 2:
        erros_rota.append("UF de carregamento")
    if len(uf_descarregamento) != 2:
        erros_rota.append("UF de descarregamento")
    if Decimal(str(mdfe.peso_total_kg or 0)) <= 0:
        erros_rota.append("peso bruto da carga")
    if not _texto(mdfe.motorista_nome):
        erros_rota.append("nome do motorista")
    if erros_rota:
        raise DadosInvalidosError(
            "Antes de emitir o MDF-e, informe: " + ", ".join(erros_rota) + "."
        )

    data_emissao = timezone.localtime().replace(microsecond=0).isoformat()
    payload = {
        "data_emissao": data_emissao,
        "emitente": "2",
        "serie": int(mdfe.serie or 1),
        "numero": mdfe.numero,
        "uf_inicio": uf_carregamento,
        "uf_fim": uf_descarregamento,
        "municipios_carregamento": [{
            "codigo": int(codigo_carregamento),
            "nome": municipio_carregamento,
        }],
        "cnpj_emitente": cnpj,
        "inscricao_estadual_emitente": _digitos(filial.inscricao_estadual),
        "nome_emitente": _texto(filial.razao_social)[:60],
        "nome_fantasia_emitente": _texto(filial.nome_fantasia)[:60],
        "logradouro_emitente": _texto(filial.endereco)[:60],
        "numero_emitente": _texto(filial.numero)[:60],
        "bairro_emitente": _texto(filial.bairro)[:60],
        "codigo_municipio_emitente": int(_digitos(filial.codigo_municipio_ibge)),
        "municipio_emitente": _texto(filial.cidade)[:60],
        "uf_emitente": _texto(filial.uf)[:2].upper(),
        "municipios_descarregamento": [{
            "codigo": int(codigo_descarregamento),
            "nome": municipio_descarregamento,
            "notas_fiscais": [{"chave_nfe": chave} for chave in chaves],
        }],
        "seguros_carga": [{"responsavel_seguro": "1"}],
        "veiculo_tracao": veiculo_tracao,
        "quantidade_total_nfe": len(chaves),
        "valor_total_carga": float(mdfe.valor_total),
        "codigo_unidade_medida_peso_bruto": "01",
        "peso_bruto": f"{mdfe.peso_total_kg:.4f}",
        "tipo_carga": "03",
        "descricao_produto": "Polpas e produtos alimentícios",
        "informacao_complementar": _texto(mdfe.observacao)[:5000],
    }
    data_hora_inicio_viagem = getattr(mdfe, "data_hora_inicio_viagem", None)
    if data_hora_inicio_viagem:
        payload["data_hora_previsto_inicio_viagem"] = timezone.localtime(
            data_hora_inicio_viagem
        ).replace(microsecond=0).isoformat()
    cep = _digitos(filial.cep)
    if len(cep) == 8:
        payload["cep_emitente"] = cep
    complemento = _texto(filial.complemento)
    if complemento:
        payload["complemento_emitente"] = complemento[:60]
    percursos = [
        uf.strip().upper()
        for uf in (getattr(mdfe, "percurso_ufs", "") or "").split(",")
        if len(uf.strip()) == 2
    ]
    if percursos:
        payload["percursos"] = [{"uf_percurso": uf} for uf in percursos]
    return payload


def _sincronizar_status(mdfe: MDFe) -> MDFe:
    documento = mdfe.documento_fiscal
    if not documento:
        return mdfe
    mapa = {
        StatusDocumentoFiscal.PENDENTE: MDFe.Status.RASCUNHO,
        StatusDocumentoFiscal.PROCESSANDO: MDFe.Status.PROCESSANDO,
        StatusDocumentoFiscal.AUTORIZADA: MDFe.Status.AUTORIZADO,
        StatusDocumentoFiscal.REJEITADA: MDFe.Status.REJEITADO,
        StatusDocumentoFiscal.DENEGADA: MDFe.Status.REJEITADO,
        StatusDocumentoFiscal.CANCELADA: MDFe.Status.CANCELADO,
    }
    mdfe.status = mapa.get(documento.status, mdfe.status)
    mdfe.chave_acesso = documento.chave or mdfe.chave_acesso
    mdfe.protocolo_autorizacao = documento.protocolo or mdfe.protocolo_autorizacao
    mdfe.data_autorizacao = documento.data_autorizacao
    mdfe.data_cancelamento = documento.data_cancelamento
    mdfe.mensagem_sefaz = documento.mensagem_sefaz
    mdfe.save()
    return mdfe


def sincronizar_mdfe_por_documento(documento: DocumentoFiscal) -> None:
    mdfe = getattr(documento, "mdfe_logistico", None)
    if mdfe:
        _sincronizar_status(mdfe)


@transaction.atomic
def criar_mdfe_transferencia(
    *,
    nfe: DocumentoFiscal,
    filial_destino,
    motorista,
    veiculo,
    peso_bruto: Decimal,
    usuario,
    observacao: str = "",
) -> MDFe:
    from apps.core.models.parametros import ParametroDocumentoFiscal, ParametrosSistema

    _validar_filial_mdfe(nfe.filial)
    _validar_filial_mdfe(filial_destino, destino=True)
    _validar_transporte(motorista, veiculo, peso_bruto)

    params, _ = ParametrosSistema.objects.get_or_create(filial=nfe.filial)
    doc_params, _ = ParametroDocumentoFiscal.objects.select_for_update().get_or_create(
        parametros=params,
        tipo_documento="mdfe",
        defaults={"habilitado": True, "serie": 1, "proximo_numero": 1},
    )
    if not doc_params.habilitado:
        raise DadosInvalidosError(
            "Habilite a emissão de MDF-e nos parâmetros fiscais da filial de origem."
        )
    numero = doc_params.proximo_numero
    serie = doc_params.serie or 1
    doc_params.proximo_numero = numero + 1
    doc_params.save(update_fields=["proximo_numero"])

    documento_mdfe = DocumentoFiscal.objects.create(
        filial=nfe.filial,
        tipo_documento=TipoDocumentoFiscal.MDFE,
        origem_tipo="transferencia_estoque",
        origem_id=nfe.origem_id,
        numero=numero,
        serie=serie,
        natureza_operacao_descricao="Manifesto de transferência entre filiais",
        tipo_operacao="1",
        emitente_cnpj=_digitos(nfe.filial.cnpj),
        destinatario_tipo="filial",
        destinatario_id=filial_destino.pk,
        destinatario_snapshot={
            "nome": filial_destino.razao_social,
            "cpf_cnpj": _digitos(filial_destino.cnpj),
        },
        valor_produtos=nfe.valor_produtos,
        valor_total=nfe.valor_total,
        status=StatusDocumentoFiscal.PENDENTE,
        data_emissao=timezone.now(),
        usuario=usuario,
    )
    metadados = {
        "tara": str(veiculo.tara),
        "capacidade_kg": str(veiculo.capacidade_kg or ""),
        "renavam": veiculo.renavam,
        "uf_placa": veiculo.uf_placa,
        "tipo_rodado": veiculo.tipo_rodado,
        "tipo_carroceria": veiculo.tipo_carroceria,
    }
    obs = _texto(observacao)
    mdfe = MDFe.objects.create(
        filial=nfe.filial,
        documento_fiscal=documento_mdfe,
        numero=numero,
        serie=str(serie),
        status=(
            MDFe.Status.RASCUNHO
            if nfe.status == StatusDocumentoFiscal.AUTORIZADA
            else MDFe.Status.AGUARDANDO_NFE
        ),
        responsavel=usuario,
        motorista_nome=motorista.nome,
        motorista_cpf=_digitos(motorista.cpf),
        motorista_cnh=motorista.cnh,
        veiculo_placa=_texto(veiculo.placa).replace("-", "").upper(),
        veiculo_descricao=_texto(veiculo.descricao or f"{veiculo.marca} {veiculo.modelo}"),
        uf_carregamento=_texto(nfe.filial.uf).upper(),
        municipio_carregamento=_texto(nfe.filial.cidade),
        codigo_municipio_carregamento=_digitos(nfe.filial.codigo_municipio_ibge),
        uf_descarregamento=_texto(filial_destino.uf).upper(),
        municipio_descarregamento=_texto(filial_destino.cidade),
        codigo_municipio_descarregamento=_digitos(filial_destino.codigo_municipio_ibge),
        qtd_nfes=1,
        peso_total_kg=peso_bruto,
        valor_total=nfe.valor_total,
        transporte_metadados=metadados,
        observacao=obs[:5000],
    )
    mdfe._transporte_metadados = metadados
    DocumentoMDFe.objects.create(
        mdfe=mdfe,
        documento_fiscal=nfe,
        tipo_documento=DocumentoMDFe.TipoDocumento.NFE,
        chave_acesso=nfe.chave or "",
        numero_documento=str(nfe.numero),
        serie=str(nfe.serie),
        emitente_nome=nfe.filial.razao_social,
        emitente_documento=_digitos(nfe.filial.cnpj),
        municipio_descarga=filial_destino.cidade,
        uf_descarga=filial_destino.uf,
        peso_kg=peso_bruto,
        valor=nfe.valor_total,
    )
    if nfe.status == StatusDocumentoFiscal.AUTORIZADA:
        return emitir_mdfe(mdfe)
    return mdfe


def _obter_ou_criar_documento_mdfe(mdfe: MDFe, usuario=None) -> DocumentoFiscal:
    if mdfe.documento_fiscal:
        return mdfe.documento_fiscal
    documento = DocumentoFiscal.objects.filter(
        origem_tipo="mdfe",
        origem_id=mdfe.pk,
    ).first()
    if not documento:
        documento = DocumentoFiscal.objects.create(
            filial=mdfe.filial,
            tipo_documento=TipoDocumentoFiscal.MDFE,
            origem_tipo="mdfe",
            origem_id=mdfe.pk,
            numero=mdfe.numero,
            serie=int(mdfe.serie or 1),
            natureza_operacao_descricao="Manifesto de documentos fiscais",
            tipo_operacao="1",
            emitente_cnpj=_digitos(mdfe.filial.cnpj),
            valor_total=mdfe.valor_total,
            status=StatusDocumentoFiscal.PENDENTE,
            data_emissao=timezone.now(),
            usuario=usuario or mdfe.responsavel,
        )
    mdfe.documento_fiscal = documento
    mdfe.save(update_fields=["documento_fiscal", "updated_at"])
    return documento


def emitir_mdfe(mdfe: MDFe, usuario=None) -> MDFe:
    if mdfe.status in (MDFe.Status.PROCESSANDO, MDFe.Status.AUTORIZADO, MDFe.Status.ENCERRADO):
        return mdfe
    _obter_ou_criar_documento_mdfe(mdfe, usuario)
    payload = construir_payload_mdfe(mdfe)
    service = FocusNFeService(client=_cliente_focus(mdfe.filial))
    service.emitir(mdfe.documento_fiscal, payload)
    return _sincronizar_status(mdfe)


def consultar_mdfe(mdfe: MDFe) -> MDFe:
    if not mdfe.documento_fiscal:
        raise DadosInvalidosError("Este MDF-e não possui documento fiscal vinculado.")
    FocusNFeService(client=_cliente_focus(mdfe.filial)).consultar(mdfe.documento_fiscal)
    return _sincronizar_status(mdfe)


def cancelar_mdfe(mdfe: MDFe, justificativa: str, usuario=None) -> MDFe:
    if not mdfe.documento_fiscal:
        raise DadosInvalidosError("Este MDF-e não possui documento fiscal vinculado.")
    justificativa = _texto(justificativa)
    if not 15 <= len(justificativa) <= 255:
        raise DadosInvalidosError("A justificativa deve ter entre 15 e 255 caracteres.")
    FocusNFeService(client=_cliente_focus(mdfe.filial)).cancelar(
        mdfe.documento_fiscal, justificativa, usuario=usuario
    )
    mdfe.justificativa_cancelamento = justificativa
    mdfe.save(update_fields=["justificativa_cancelamento", "updated_at"])
    return _sincronizar_status(mdfe)


def encerrar_mdfe(mdfe: MDFe) -> MDFe:
    if mdfe.status != MDFe.Status.AUTORIZADO:
        raise DadosInvalidosError("Somente um MDF-e autorizado pode ser encerrado.")
    retorno = _cliente_focus(mdfe.filial).mdfe.encerrar(
        gerar_ref(mdfe.documento_fiscal),
        data=timezone.localdate().isoformat(),
        sigla_uf=mdfe.uf_descarregamento,
        nome_municipio=mdfe.municipio_descarregamento,
    )
    status = _texto((retorno or {}).get("status")).lower()
    if status not in {"encerrado", "autorizado"}:
        raise FocusNFeProcessingError(
            (retorno or {}).get("mensagem_sefaz")
            or (retorno or {}).get("mensagem")
            or "A Focus não confirmou o encerramento do MDF-e.",
            response_json=retorno,
        )
    mdfe.status = MDFe.Status.ENCERRADO
    mdfe.data_encerramento = timezone.localdate()
    mdfe.save(update_fields=["status", "data_encerramento", "updated_at"])
    return mdfe


def damdfe_pdf(mdfe: MDFe) -> bytes:
    if not mdfe.documento_fiscal:
        raise DadosInvalidosError("Este MDF-e ainda não foi enviado para emissão.")
    return FocusNFeService(
        client=_cliente_focus(mdfe.filial)
    ).baixar_pdf(mdfe.documento_fiscal)


def processar_nfe_transferencia_autorizada(nfe: DocumentoFiscal) -> None:
    if nfe.tipo_documento != TipoDocumentoFiscal.NFE:
        return
    if nfe.status != StatusDocumentoFiscal.AUTORIZADA:
        return
    vinculos = DocumentoMDFe.objects.select_related("mdfe__documento_fiscal").filter(
        documento_fiscal=nfe,
        mdfe__status=MDFe.Status.AGUARDANDO_NFE,
    )
    for vinculo in vinculos:
        vinculo.chave_acesso = nfe.chave or ""
        vinculo.save(update_fields=["chave_acesso", "updated_at"])
        emitir_mdfe(vinculo.mdfe)
