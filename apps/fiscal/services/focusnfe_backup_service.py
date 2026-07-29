"""Consulta e leitura segura dos backups mensais da Focus NFe."""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Iterable, Iterator, Optional

from apps.core.services.exceptions import DomainError
from apps.fiscal.integrations.focusnfe import FocusNFeClient


MAX_BACKUP_BYTES = 250 * 1024 * 1024
MAX_XML_BYTES = 20 * 1024 * 1024
MAX_XMLS_POR_BACKUP = 100_000


@dataclass(frozen=True)
class BackupFocus:
    mes: str
    url_xmls: str


@dataclass(frozen=True)
class XMLBackupFocus:
    mes: str
    nome: str
    conteudo: str


class FocusNFeBackupService:
    def __init__(self, client: FocusNFeClient) -> None:
        self.client = client

    def listar(self, cnpj: str) -> list[BackupFocus]:
        backups = []
        cnpj_limpo = re.sub(r"\D", "", cnpj or "")
        for item in self.client.backups.listar(cnpj_limpo):
            mes = str(item.get("mes") or "").strip()
            url_xmls = str(item.get("xmls") or "").strip()
            if re.fullmatch(r"\d{6}", mes) and url_xmls.startswith(("http://", "https://")):
                backups.append(BackupFocus(mes=mes, url_xmls=url_xmls))
        return sorted(backups, key=lambda backup: backup.mes)

    def selecionar(
        self,
        cnpj: str,
        meses: Optional[set[str]] = None,
    ) -> list[BackupFocus]:
        backups = self.listar(cnpj)
        if meses is not None:
            backups = [backup for backup in backups if backup.mes in meses]
        return backups

    def iter_xmls(self, backups: Iterable[BackupFocus]) -> Iterator[XMLBackupFocus]:
        for backup in backups:
            conteudo_zip = self.client.backups.baixar_xmls(backup.url_xmls)
            if not conteudo_zip:
                continue
            if len(conteudo_zip) > MAX_BACKUP_BYTES:
                raise DomainError(
                    f"O backup Focus de {backup.mes} excede o limite seguro de download."
                )
            try:
                with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as arquivo:
                    infos = [
                        info
                        for info in arquivo.infolist()
                        if not info.is_dir() and info.filename.lower().endswith(".xml")
                    ]
                    if len(infos) > MAX_XMLS_POR_BACKUP:
                        raise DomainError(
                            f"O backup Focus de {backup.mes} possui arquivos demais."
                        )
                    for info in infos:
                        if info.file_size > MAX_XML_BYTES:
                            continue
                        nome = PurePosixPath(info.filename.replace("\\", "/")).name
                        bruto = arquivo.read(info)
                        yield XMLBackupFocus(
                            mes=backup.mes,
                            nome=nome,
                            conteudo=_decodificar_xml(bruto),
                        )
            except zipfile.BadZipFile as exc:
                raise DomainError(
                    f"A Focus retornou um backup invalido para {backup.mes}."
                ) from exc


def classificar_xml_fiscal(nome: str, conteudo: str) -> str:
    modelo = re.search(
        r"<(?:[A-Za-z_][\w.-]*:)?mod>\s*(55|57|58|62|65|67)\s*</(?:[A-Za-z_][\w.-]*:)?mod>",
        conteudo,
        flags=re.IGNORECASE,
    )
    codigo_modelo = modelo.group(1) if modelo else ""
    if not codigo_modelo:
        for chave in extrair_chaves_xml(nome, conteudo):
            if chave[20:22] in {"55", "57", "58", "62", "65", "67"}:
                codigo_modelo = chave[20:22]
                break
    if not codigo_modelo and re.search(
        r"<(?:[A-Za-z_][\w.-]*:)?(?:CompNfse|NFS-e|NFSe)\b",
        conteudo,
        flags=re.IGNORECASE,
    ):
        return "NFS-e"
    return {
        "55": "NF-e",
        "57": "CT-e",
        "58": "MDF-e",
        "62": "NFCom",
        "65": "NFC-e",
        "67": "CT-e OS",
    }.get(codigo_modelo, "Outros")


def extrair_chaves_xml(nome: str, conteudo: str) -> list[str]:
    """Extrai chaves fiscais de 44 digitos sem repetir a ordem encontrada."""
    return list(dict.fromkeys(
        re.findall(r"(?<!\d)\d{44}(?!\d)", f"{nome}\n{conteudo}")
    ))


def meses_entre(data_inicial: date, data_final: date) -> set[str]:
    atual = date(data_inicial.year, data_inicial.month, 1)
    limite = date(data_final.year, data_final.month, 1)
    meses = set()
    while atual <= limite:
        meses.add(atual.strftime("%Y%m"))
        if atual.month == 12:
            atual = date(atual.year + 1, 1, 1)
        else:
            atual = date(atual.year, atual.month + 1, 1)
    return meses


def _decodificar_xml(conteudo: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return conteudo.decode(encoding)
        except UnicodeDecodeError:
            continue
    return conteudo.decode("utf-8", errors="replace")
