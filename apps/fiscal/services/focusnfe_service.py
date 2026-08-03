"""
Serviço de integração Focus NFe — Fase 1 (fundação).

Conecta o SDK ``apps.fiscal.integrations.focusnfe`` aos modelos fiscais do ERP.
Responsável por: emitir, consultar e cancelar um ``DocumentoFiscal`` via Focus NFe,
persistir o retorno (chave, protocolo, status, DANFE) e registrar cada chamada
em ``LogIntegracaoFiscal``.

A construção do payload (JSON específico de cada documento) é responsabilidade
da Fase 2 — aqui o ``payload`` já chega pronto em ``emitir()``.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

from django.utils import timezone

from apps.financeiro.constants.enums import StatusDocumentoFiscal
from apps.financeiro.models.fiscal import DocumentoFiscal, LogIntegracaoFiscal
from apps.fiscal.integrations.focusnfe import FocusNFeClient
from apps.fiscal.integrations.focusnfe.config import HOMOLOGACAO, PRODUCAO, URLS
from apps.fiscal.integrations.focusnfe.exceptions import (
    FocusNFeAuthError,
    FocusNFeError,
    FocusNFeProcessingError,
)

logger = logging.getLogger(__name__)

PROVEDOR = "focusnfe"

# tipo_documento (DocumentoFiscal) -> atributo resource no FocusNFeClient
RESOURCE_POR_TIPO: Dict[str, str] = {
    "nfe": "nfe",
    "nfce": "nfce",
    "nfse": "nfse",
    "nfse_nacional": "nfse_nacional",
    "cte": "cte",
    "cte_os": "cte_os",
    "mdfe": "mdfe",
    "nfcom": "nfcom",
}

# status retornado pela Focus NFe -> StatusDocumentoFiscal do ERP
STATUS_FOCUS_PARA_ERP: Dict[str, str] = {
    "autorizado": StatusDocumentoFiscal.AUTORIZADA,
    "cancelado": StatusDocumentoFiscal.CANCELADA,
    "cancelada": StatusDocumentoFiscal.CANCELADA,
    "processando_autorizacao": StatusDocumentoFiscal.PROCESSANDO,
    "contingencia_offline": StatusDocumentoFiscal.PROCESSANDO,
    "autorizado_contingencia": StatusDocumentoFiscal.PROCESSANDO,
    "erro_autorizacao": StatusDocumentoFiscal.REJEITADA,
    "nao_autorizado": StatusDocumentoFiscal.REJEITADA,
    "rejeitado": StatusDocumentoFiscal.REJEITADA,
    "rejeitada": StatusDocumentoFiscal.REJEITADA,
    "denegado": StatusDocumentoFiscal.DENEGADA,
}

# chaves possíveis no JSON de retorno (variam por tipo de documento)
_CHAVE_KEYS = ("chave_nfe", "chave_nfce", "chave_cte", "chave_mdfe", "chave_nfcom", "chave")
_PROTOCOLO_KEYS = ("numero_protocolo", "protocolo")
_PDF_KEYS = ("caminho_danfe", "caminho_dacte", "caminho_damdfe", "caminho_pdf", "url_danfe")
_XML_KEYS = ("caminho_xml_nota_fiscal", "caminho_xml", "caminho_xml_cte", "caminho_xml_mdfe")


def _mensagem_sefaz_diagnostica(codigo: str, mensagem: str) -> str:
    codigo = str(codigo or "").strip()
    mensagem = str(mensagem or "").strip()
    if codigo == "464" or "hash no qr-code" in mensagem.lower():
        detalhe = (
            " Verifique o CSC Token e o CSC ID NFC-e cadastrados na Focus/SEFAZ "
            "para o mesmo ambiente da filial. A emissao da nota nao envia CSC; "
            "esse dado e usado pela Focus para montar o hash do QR-Code."
        )
        if "CSC Token" not in mensagem:
            return f"{mensagem}{detalhe}" if mensagem else detalhe.strip()
    return mensagem


def gerar_ref(documento: DocumentoFiscal) -> str:
    """Referência única enviada à Focus NFe. Reconstrói o vínculo no webhook."""
    return f"df-{documento.pk}"


def parse_ref(ref: str) -> Optional[int]:
    """Extrai o pk do DocumentoFiscal a partir da ref ``df-<pk>``."""
    if ref and ref.startswith("df-"):
        try:
            return int(ref[3:])
        except ValueError:
            return None
    return None


class FocusNFeService:
    """Orquestra as operações fiscais via Focus NFe sobre um DocumentoFiscal."""

    def __init__(self, client: Optional[FocusNFeClient] = None) -> None:
        self._client = client

    @property
    def client(self) -> FocusNFeClient:
        """Cliente Focus NFe (token/ambiente lidos das settings)."""
        if self._client is None:
            self._client = FocusNFeClient()
        return self._client

    # ------------------------------------------------------------------ infra
    def _resource(self, tipo_documento: str):
        attr = RESOURCE_POR_TIPO.get((tipo_documento or "").lower())
        if not attr:
            raise ValueError(
                f"Tipo de documento sem resource Focus NFe: '{tipo_documento}'."
            )
        return getattr(self.client, attr)

    def _base_url(self) -> str:
        """URL base da Focus NFe — derivada do ambiente, sem exigir token."""
        if self._client is not None:
            return self._client.config.base_url
        try:
            from django.conf import settings as dj_settings
            ambiente = int(getattr(dj_settings, "ERP_FOCUSNFE_AMBIENTE", HOMOLOGACAO))
        except Exception:
            ambiente = HOMOLOGACAO
        return URLS.get(ambiente, URLS[HOMOLOGACAO])

    def _url_absoluta(self, caminho: str) -> str:
        if not caminho:
            return ""
        if caminho.startswith("http://") or caminho.startswith("https://"):
            return caminho
        base = self._base_url().rstrip("/")
        return f"{base}/{caminho.lstrip('/')}"

    def _registrar_log(
        self,
        documento: DocumentoFiscal,
        acao: str,
        *,
        endpoint: str = "",
        request: Any = None,
        response: Any = None,
        http: Optional[int] = None,
        sucesso: Optional[bool] = None,
        ms: Optional[int] = None,
        status_sefaz: str = "",
        usuario=None,
    ) -> None:
        """Grava um LogIntegracaoFiscal. Nunca interrompe o fluxo principal."""
        try:
            LogIntegracaoFiscal.objects.create(
                filial=documento.filial,
                usuario=usuario or documento.usuario,
                documento_fiscal=documento if documento.pk else None,
                provedor=PROVEDOR,
                acao=acao[:30],
                endpoint=endpoint[:200],
                request_json=(
                    json.dumps(request, ensure_ascii=False, default=str)
                    if request is not None else ""
                ),
                response_json=(
                    json.dumps(response, ensure_ascii=False, default=str)
                    if response is not None else ""
                ),
                codigo_http=http,
                codigo_status_sefaz=(status_sefaz or "")[:3],
                sucesso=sucesso,
                tempo_resposta_ms=ms,
                tentativa=documento.tentativas_envio or 1,
            )
        except Exception:  # logging nunca pode quebrar a operação fiscal
            logger.exception("Falha ao registrar LogIntegracaoFiscal")

    # ------------------------------------------------------------- retorno
    def aplicar_retorno(self, documento: DocumentoFiscal, retorno: Dict[str, Any]) -> DocumentoFiscal:
        """
        Atualiza o DocumentoFiscal a partir de um JSON de retorno da Focus NFe.
        Usado tanto após emitir/consultar quanto pelo webhook.
        """
        retorno = retorno or {}

        status_focus = str(retorno.get("status") or "").lower()
        novo_status = STATUS_FOCUS_PARA_ERP.get(status_focus)
        if novo_status:
            documento.status = novo_status

        for k in _CHAVE_KEYS:
            if retorno.get(k):
                documento.chave = str(retorno[k])[:44]
                break

        for k in _PROTOCOLO_KEYS:
            if retorno.get(k):
                documento.protocolo = str(retorno[k])[:20]
                break

        if retorno.get("status_sefaz"):
            documento.codigo_status_sefaz = str(retorno["status_sefaz"])[:3]
        if retorno.get("mensagem_sefaz"):
            documento.mensagem_sefaz = _mensagem_sefaz_diagnostica(
                documento.codigo_status_sefaz,
                str(retorno["mensagem_sefaz"]),
            )

        for k in _PDF_KEYS:
            if retorno.get(k):
                documento.pdf_danfe_url = self._url_absoluta(str(retorno[k]))[:500]
                break

        for k in _XML_KEYS:
            if retorno.get(k):
                documento.xml_retorno = self._url_absoluta(str(retorno[k]))
                break

        agora = timezone.now()
        if documento.status == StatusDocumentoFiscal.AUTORIZADA and not documento.data_autorizacao:
            documento.data_autorizacao = agora
        if documento.status == StatusDocumentoFiscal.CANCELADA and not documento.data_cancelamento:
            documento.data_cancelamento = agora

        documento.save()
        if documento.status == StatusDocumentoFiscal.AUTORIZADA:
            self.garantir_xml_autorizado(documento)
        elif documento.status == StatusDocumentoFiscal.CANCELADA:
            self.garantir_xml_cancelamento(documento)
        return documento

    # -------------------------------------------------------------- emissão
    def emitir(self, documento: DocumentoFiscal, payload: Dict[str, Any], *, contingencia: bool = False) -> DocumentoFiscal:
        """
        Envia o documento para autorização na SEFAZ via Focus NFe.

        A autorização é assíncrona: a Focus normalmente responde
        ``processando_autorizacao`` e o status final chega pelo webhook
        (ou via :meth:`consultar`).
        """
        if documento.status == StatusDocumentoFiscal.AUTORIZADA:
            return documento  # já autorizado — idempotente

        resource = self._resource(documento.tipo_documento)
        ref = gerar_ref(documento)
        endpoint = f"/v2/{getattr(resource, 'endpoint', documento.tipo_documento)}"

        documento.tentativas_envio = (documento.tentativas_envio or 0) + 1
        t0 = time.monotonic()
        try:
            if contingencia:
                if documento.tipo_documento != "nfce" or not hasattr(resource, "autorizar_offline"):
                    raise ValueError("Contingencia offline disponivel somente para NFC-e.")
                retorno = resource.autorizar_offline(ref, payload)
            else:
                retorno = resource.autorizar(ref, payload)
        except FocusNFeError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            self._registrar_log(
                documento, "emitir", endpoint=endpoint, request=payload,
                response=exc.response_json, http=exc.status_code, sucesso=False, ms=ms,
            )
            documento.status = StatusDocumentoFiscal.REJEITADA
            resposta = exc.response_json if isinstance(exc.response_json, dict) else {}
            codigo = str(resposta.get("status_sefaz") or resposta.get("codigo_status_sefaz") or "")
            # str(exc) ja traz a mensagem base + o detalhamento campo a campo
            # do array "erros" (ex.: erros de Schema XML 422). So caimos para
            # mensagem_sefaz especifica da SEFAZ quando ela existir.
            mensagem = str(resposta.get("mensagem_sefaz") or exc)
            if codigo:
                documento.codigo_status_sefaz = codigo[:3]
            documento.mensagem_sefaz = _mensagem_sefaz_diagnostica(codigo, mensagem)
            documento.save()
            raise

        ms = int((time.monotonic() - t0) * 1000)
        self._registrar_log(
            documento, "emitir", endpoint=endpoint, request=payload,
            response=retorno, sucesso=True, ms=ms,
        )
        # status inicial: processando, salvo se o retorno já trouxer algo definitivo
        if documento.status == StatusDocumentoFiscal.PENDENTE:
            documento.status = StatusDocumentoFiscal.PROCESSANDO
        if retorno is not None and not isinstance(retorno, dict):
            raise FocusNFeProcessingError(
                "A Focus recebeu o documento, mas devolveu uma resposta inesperada. "
                "Consulte o documento antes de tentar emitir novamente.",
                response_text=str(retorno)[:2000],
            )
        return self.aplicar_retorno(documento, retorno or {})

    # ------------------------------------------------------------- consulta
    def consultar(self, documento: DocumentoFiscal) -> DocumentoFiscal:
        """Consulta o status atual do documento e atualiza o ERP."""
        resource = self._resource(documento.tipo_documento)
        ref = gerar_ref(documento)
        t0 = time.monotonic()
        try:
            retorno = resource.consultar(ref)
        except FocusNFeError as exc:
            self._registrar_log(
                documento, "consultar", response=exc.response_json,
                http=exc.status_code, sucesso=False,
            )
            raise
        ms = int((time.monotonic() - t0) * 1000)
        self._registrar_log(documento, "consultar", response=retorno, sucesso=True, ms=ms)
        return self.aplicar_retorno(documento, retorno or {})

    # ------------------------------------------------------------ cancelar
    def cancelar(
        self,
        documento: DocumentoFiscal,
        justificativa: str,
        *,
        usuario=None,
    ) -> DocumentoFiscal:
        """Cancela um documento autorizado."""
        # O endpoint da Focus passa a devolver o XML do evento depois do
        # cancelamento. Preserve antes o XML autorizado para manter os dois.
        self.garantir_xml_autorizado(documento)
        resource = self._resource(documento.tipo_documento)
        ref = gerar_ref(documento)
        t0 = time.monotonic()
        try:
            retorno = resource.cancelar(ref, justificativa)
        except FocusNFeError as exc:
            self._registrar_log(
                documento, "cancelar", request={"justificativa": justificativa},
                response=exc.response_json, http=exc.status_code, sucesso=False,
                usuario=usuario,
            )
            raise
        ms = int((time.monotonic() - t0) * 1000)
        status_focus = str((retorno or {}).get("status") or "").lower()
        if status_focus not in ("cancelado", "cancelada"):
            mensagem = str(
                (retorno or {}).get("mensagem_sefaz")
                or (retorno or {}).get("mensagem")
                or "A Focus nao confirmou o cancelamento do documento."
            )
            self._registrar_log(
                documento, "cancelar", request={"justificativa": justificativa},
                response=retorno, sucesso=False, ms=ms,
                usuario=usuario,
            )
            raise FocusNFeProcessingError(mensagem, response_json=retorno)

        self._registrar_log(
            documento, "cancelar", request={"justificativa": justificativa},
            response=retorno, sucesso=True, ms=ms,
            usuario=usuario,
        )
        return self.aplicar_retorno(documento, retorno or {})

    # -------------------------------------------------------------- arquivos
    def baixar_pdf(self, documento: DocumentoFiscal) -> bytes:
        """
        Baixa o DANFE/DACTE/DAMDFE em PDF (binário).

        Confere a assinatura do arquivo antes de devolver. O provider às vezes
        responde 200 com um JSON ("documento ainda em processamento", por
        exemplo) e o cliente HTTP entrega esses bytes como se fossem o PDF —
        o navegador então mostra apenas "Falha ao carregar documento PDF",
        que é um beco sem saída: não diz o que houve nem o que fazer.

        Com a checagem, o motivo real do provider chega até a tela.
        """
        resource = self._resource(documento.tipo_documento)
        if not hasattr(resource, "baixar_pdf"):
            raise ValueError(f"{documento.tipo_documento} não suporta download de PDF.")

        conteudo = resource.baixar_pdf(gerar_ref(documento))
        self._exigir_pdf(conteudo)
        return conteudo

    @staticmethod
    def _exigir_pdf(conteudo) -> None:
        """Levanta FocusNFeError quando o retorno não é um PDF."""
        if not conteudo:
            raise FocusNFeError(
                "O provider devolveu um arquivo vazio no lugar do PDF. "
                "Se o documento acabou de ser autorizado, aguarde alguns "
                "segundos e tente de novo."
            )

        # Todo PDF comeca com "%PDF-". Qualquer outra coisa e mensagem, nao
        # documento.
        if isinstance(conteudo, bytes) and conteudo[:4] == b"%PDF":
            return

        texto = conteudo if isinstance(conteudo, str) else conteudo.decode(
            "utf-8", errors="replace"
        )
        raise FocusNFeError(
            "O provider não devolveu um PDF. Resposta: "
            + " ".join(texto.split())[:300]
        )

    def baixar_xml(self, documento: DocumentoFiscal) -> bytes:
        """Baixa o XML autorizado (binário)."""
        resource = self._resource(documento.tipo_documento)
        if not hasattr(resource, "baixar_xml"):
            raise ValueError(f"{documento.tipo_documento} não suporta download de XML.")
        return resource.baixar_xml(gerar_ref(documento))

    @staticmethod
    def _xml_texto(conteudo: bytes | str) -> str:
        if isinstance(conteudo, str):
            return conteudo
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return conteudo.decode(encoding)
            except UnicodeDecodeError:
                continue
        return conteudo.decode("utf-8", errors="replace")

    @staticmethod
    def _tem_xml(valor: str) -> bool:
        return bool(valor and valor.lstrip().startswith("<"))

    def _guardar_xml(self, documento: DocumentoFiscal, campo: str) -> str:
        atual = getattr(documento, campo, "") or ""
        if self._tem_xml(atual):
            return atual
        try:
            xml = self._xml_texto(self.baixar_xml(documento))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Nao foi possivel arquivar %s do documento fiscal %s: %s",
                campo,
                documento.pk,
                exc,
            )
            return ""
        if not self._tem_xml(xml):
            logger.warning(
                "A Focus nao retornou XML valido para %s do documento fiscal %s.",
                campo,
                documento.pk,
            )
            return ""
        setattr(documento, campo, xml)
        documento.save(update_fields=[campo, "updated_at"])
        return xml

    def garantir_xml_autorizado(self, documento: DocumentoFiscal) -> str:
        """Arquiva o XML processado autorizado sem sobrescrever uma copia existente."""
        return self._guardar_xml(documento, "xml_assinado")

    def garantir_xml_cancelamento(self, documento: DocumentoFiscal) -> str:
        """Arquiva o XML retornado depois da confirmacao do cancelamento."""
        return self._guardar_xml(documento, "xml_cancelamento")

    # ----------------------------------------- cadastro de empresa na Focus
    def sincronizar_empresa(self, filial, params) -> Dict[str, Any]:
        """
        Envia/atualiza os dados da empresa (filial) na Focus NFe, incluindo:
          - certificado digital A1 (base64)
          - senha do certificado
          - CSC NFC-e (produção e homologação)
          - regime tributário e endereço

        `filial` — instância de Filial (com cnpj, nome, endereço, regime, token)
        `params` — instância de ParametrosSistema (com certificado, senha, csc)

        Faz upsert: cria se não existir, atualiza se já cadastrado.
        Retorna o dict retornado pela Focus.
        """
        import base64
        import os

        cnpj = (filial.cnpj or "").replace(".", "").replace("/", "").replace("-", "").strip()
        if not cnpj or len(cnpj) != 14:
            raise ValueError("CNPJ da filial inválido ou não preenchido.")

        token_principal = (getattr(params, "focusnfe_token_principal", "") or "").strip()
        if not token_principal:
            raise ValueError(
                "Token Principal Focus nao configurado. Copie o Token principal producao "
                "do Painel API da Focus."
            )

        # --- Certificado digital -------------------------------------------
        # Prefere base64 salvo no banco (persiste entre redeploys no Railway).
        # Tenta ler o arquivo somente como fallback.
        cert_b64: Optional[str] = None
        db_b64 = (getattr(params, "certificado_base64", "") or "").strip()
        if db_b64:
            cert_b64 = db_b64
        elif params.certificado_digital and params.certificado_digital.name:
            try:
                params.certificado_digital.open("rb")
                cert_bytes = params.certificado_digital.read()
                params.certificado_digital.close()
                cert_b64 = base64.b64encode(cert_bytes).decode("ascii")
                # Persiste no banco para sobreviver a redeploys
                params.certificado_base64 = cert_b64
                params.save(update_fields=["certificado_base64"])
            except OSError:
                # Arquivo não existe no disco (filesystem efêmero do Railway).
                # O base64 estará vazio — o certificado não será enviado agora.
                # O usuário deve fazer um novo upload na tela de parâmetros.
                logger.warning(
                    "Certificado digital não encontrado em disco (%s). "
                    "Sincronizando apenas CSC e dados da empresa.",
                    params.certificado_digital.name,
                )
                cert_b64 = None

        # --- Monta payload base -------------------------------------------
        payload: Dict[str, Any] = {
            "cnpj": cnpj,
            "nome": filial.razao_social or filial.nome_fantasia or cnpj,
            "inscricao_estadual": (filial.inscricao_estadual or "").strip() or "ISENTO",
            "habilita_nfce": True,
            "habilita_nfe": True,
            "habilita_contingencia_offline_nfce": True,
            "reaproveita_numero_nfce_contingencia": True,
        }

        # Regime tributário (1=SN, 2=SN excesso, 3=Normal)
        regime = (
            filial.codigo_regime_tributario
            or getattr(getattr(filial, "empresa", None), "codigo_regime_tributario", None)
        )
        if regime:
            try:
                payload["regime_tributario"] = int(regime)
            except (TypeError, ValueError):
                pass

        # Endereço
        if filial.endereco:
            payload["logradouro"] = filial.endereco
        if filial.numero:
            payload["numero"] = filial.numero
        if filial.complemento:
            payload["complemento"] = filial.complemento
        if filial.bairro:
            payload["bairro"] = filial.bairro
        if filial.cep:
            payload["cep"] = (filial.cep or "").replace("-", "").strip()
        if filial.cidade:
            payload["municipio"] = filial.cidade
        if filial.uf:
            payload["uf"] = filial.uf

        sem_certificado = cert_b64 is None

        # Certificado
        if cert_b64:
            payload["arquivo_certificado_base64"] = cert_b64
        senha = (params.senha_certificado or "").strip()
        if senha:
            payload["senha_certificado"] = senha

        # CSC (Código de Segurança do Contribuinte) — necessário para NFC-e
        ambiente = getattr(filial, "focusnfe_ambiente", 2)  # 1=prod, 2=homolog
        csc_token = (params.nfce_csc_token or "").strip()
        csc_id = (params.nfce_csc_id or "").strip()
        if csc_token and csc_id:
            if ambiente == 1:
                payload["csc_nfce_producao"] = csc_token
                payload["id_token_nfce_producao"] = csc_id
            else:
                payload["csc_nfce_homologacao"] = csc_token
                payload["id_token_nfce_homologacao"] = csc_id

        # A API de empresas exige o Token Principal no endpoint de producao.
        # O token e o ambiente da filial ficam reservados para emitir documentos.
        from apps.fiscal.integrations.focusnfe import FocusNFeClient
        from apps.fiscal.integrations.focusnfe.config import FocusNFeConfig
        config = FocusNFeConfig.from_env(token=token_principal, ambiente=PRODUCAO)
        client = FocusNFeClient(config=config)

        try:
            retorno = client.empresas.upsert(cnpj, payload)
        except FocusNFeAuthError as exc:
            raise ValueError(
                "Token Principal Focus invalido. Nao use o Token Producao da empresa "
                "neste campo."
            ) from exc
        if sem_certificado:
            if isinstance(retorno, dict):
                retorno["_aviso_certificado"] = (
                    "Certificado não enviado (arquivo não encontrado no servidor). "
                    "Faça upload novamente na tela de Parâmetros e salve para registrá-lo."
                )
        return retorno
