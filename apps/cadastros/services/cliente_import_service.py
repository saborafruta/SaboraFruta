"""Importação em lote de clientes via CSV."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List

from django.utils.dateparse import parse_date

from apps.mapas.signals import modo_lote

from apps.cadastros.models import Cliente
from apps.cadastros.services.cliente_service import ClienteService
from apps.core.constants.choices import TipoPessoa, UF
from apps.core.services.exceptions import DomainError

TIPOS_VALIDOS = {t.value for t in Cliente.Tipo}
TIPOS_PESSOA_VALIDOS = {t.value for t in TipoPessoa}
UFS_VALIDAS = {u.value for u in UF}
COLUNAS_OBRIGATORIAS = {'tipo_pessoa', 'razao_social'}
ALIASES_COLUNAS = {
    'tipo_pessoa': 'tipo_pessoa',
    'tipo_de_pessoa': 'tipo_pessoa',
    'razao_social': 'razao_social',
    'nome_razao_social': 'razao_social',
    'nome_fantasia': 'nome_fantasia',
    'cpf_cnpj': 'cpf_cnpj',
    'cpf_ou_cnpj': 'cpf_cnpj',
    'endereco': 'endereco',
    'logradouro': 'endereco',
    'rua': 'endereco',
    'numero': 'numero',
    'nro': 'numero',
    'complemento': 'complemento',
    'bairro': 'bairro',
    'cidade': 'cidade',
    'municipio': 'cidade',
    'uf': 'uf',
    'estado': 'uf',
    'cep': 'cep',
}


@dataclass
class ResultadoImportacao:
    criados: int = 0
    ignorados: int = 0
    erros: List[dict] = field(default_factory=list)

    @property
    def total_linhas(self):
        return self.criados + self.ignorados + len(self.erros)

    @property
    def teve_erro(self):
        return bool(self.erros)


class ClienteImportService:

    @staticmethod
    def importar_csv(arquivo, usuario, filial) -> ResultadoImportacao:
        """
        Lê arquivo CSV (separador ';') e importa clientes na filial.
        Processa linha a linha — erros de uma linha não impedem as demais.
        """
        conteudo = arquivo.read()
        texto = ClienteImportService._decodificar(conteudo)

        reader = csv.DictReader(io.StringIO(texto), delimiter=';')

        if not reader.fieldnames:
            raise DomainError('Arquivo CSV vazio ou sem cabeçalho.')

        colunas = {
            ClienteImportService._normalizar_cabecalho(c)
            for c in reader.fieldnames if c
        }
        faltando = COLUNAS_OBRIGATORIAS - colunas
        if faltando:
            raise DomainError(f'Colunas obrigatórias ausentes: {", ".join(sorted(faltando))}')

        resultado = ResultadoImportacao()

        for num_linha, row in enumerate(reader, start=2):
            row_limpo = {}
            for chave, valor in row.items():
                if not chave:
                    continue
                chave_normalizada = ClienteImportService._normalizar_cabecalho(chave)
                valor_limpo = (valor or '').strip()
                if chave_normalizada not in row_limpo or valor_limpo:
                    row_limpo[chave_normalizada] = valor_limpo

            # Linha completamente vazia → ignora sem contar erro
            if not any(row_limpo.values()):
                resultado.ignorados += 1
                continue

            try:
                dados = ClienteImportService._validar_e_converter(row_limpo, num_linha)
                # Sem modo_lote, cada cliente do CSV dispararia uma chamada de
                # rede ao geocoder e a importacao estouraria o timeout. Eles
                # ficam pendentes para o `manage.py geocodificar`.
                with modo_lote():
                    ClienteService.criar(dados, usuario, filial)
                resultado.criados += 1
            except DomainError as e:
                resultado.erros.append({
                    'linha': num_linha,
                    'razao_social': row_limpo.get('razao_social', ''),
                    'erro': str(e),
                })
            except Exception as e:
                resultado.erros.append({
                    'linha': num_linha,
                    'razao_social': row_limpo.get('razao_social', ''),
                    'erro': f'Erro inesperado: {e}',
                })

        return resultado

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    @staticmethod
    def _normalizar_cabecalho(valor: str) -> str:
        texto = unicodedata.normalize('NFKD', (valor or '').strip())
        texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
        texto = re.sub(r'[^a-z0-9]+', '_', texto).strip('_')
        return ALIASES_COLUNAS.get(texto, texto)

    @staticmethod
    def _decodificar(conteudo: bytes) -> str:
        for enc in ('utf-8-sig', 'utf-8', 'latin-1'):
            try:
                return conteudo.decode(enc)
            except UnicodeDecodeError:
                continue
        raise DomainError('Não foi possível decodificar o arquivo. Salve como UTF-8 ou Latin-1.')

    @staticmethod
    def _validar_e_converter(row: dict, num_linha: int) -> dict:
        # tipo_pessoa
        tipo_pessoa = row.get('tipo_pessoa', '').upper()
        if tipo_pessoa not in TIPOS_PESSOA_VALIDOS:
            raise DomainError(
                f'Linha {num_linha}: tipo_pessoa "{tipo_pessoa}" inválido. Use F, J ou E.'
            )

        # razao_social
        razao_social = row.get('razao_social', '').strip()
        if not razao_social:
            raise DomainError(f'Linha {num_linha}: razao_social é obrigatório.')

        # tipo
        tipo = (row.get('tipo', '') or 'varejo').lower()
        if tipo not in TIPOS_VALIDOS:
            raise DomainError(
                f'Linha {num_linha}: tipo "{tipo}" inválido. Use varejo, atacado ou distribuidor.'
            )

        # uf
        uf = (row.get('uf', '') or '').upper()
        if uf and uf not in UFS_VALIDAS:
            raise DomainError(f'Linha {num_linha}: UF "{uf}" inválida.')

        # cpf_cnpj
        cpf_cnpj = ''.join(c for c in row.get('cpf_cnpj', '') if c.isdigit())
        if cpf_cnpj and len(cpf_cnpj) not in (11, 14):
            raise DomainError(
                f'Linha {num_linha}: cpf_cnpj deve ter 11 dígitos (CPF) ou 14 dígitos (CNPJ).'
            )

        # cep
        cep = ''.join(c for c in row.get('cep', '') if c.isdigit())
        if cep and len(cep) != 8:
            raise DomainError(f'Linha {num_linha}: CEP deve ter 8 dígitos.')

        # data_nascimento
        data_nascimento = None
        dn_str = row.get('data_nascimento', '').strip()
        if dn_str:
            data_nascimento = parse_date(dn_str)
            if data_nascimento is None:
                raise DomainError(
                    f'Linha {num_linha}: data_nascimento "{dn_str}" inválida. Use o formato AAAA-MM-DD.'
                )

        def _bool(key, default=False):
            v = row.get(key, '').strip()
            return True if v == '1' else (False if v == '0' else default)

        def _decimal(key, default=0):
            v = row.get(key, '').strip().replace(',', '.')
            try:
                return float(v) if v else default
            except ValueError:
                return default

        def _int(key, default=0):
            v = row.get(key, '').strip()
            try:
                return int(v) if v else default
            except ValueError:
                return default

        return {
            'tipo_pessoa':          tipo_pessoa,
            'tipo':                 tipo,
            'razao_social':         razao_social[:150],
            'nome_fantasia':        row.get('nome_fantasia', '')[:100],
            'cpf_cnpj':             cpf_cnpj,
            'rg_ie':                row.get('rg_ie', '')[:20],
            'inscricao_estadual':   row.get('inscricao_estadual', '')[:20],
            'inscricao_municipal':  row.get('inscricao_municipal', '')[:20],
            'data_nascimento':      data_nascimento,
            'sexo':                 row.get('sexo', '')[:1].upper(),
            'cep':                  cep,
            'endereco':             row.get('endereco', '')[:255],
            'numero':               row.get('numero', '')[:10],
            'complemento':          row.get('complemento', '')[:60],
            'bairro':               row.get('bairro', '')[:80],
            'cidade':               row.get('cidade', '')[:80],
            'uf':                   uf[:2],
            'telefone':             row.get('telefone', '')[:20],
            'celular':              row.get('celular', '')[:20],
            'email':                row.get('email', '')[:120],
            'email_nfe':            row.get('email_nfe', '')[:120],
            'contato_nome':         row.get('contato_nome', '')[:100],
            'limite_credito':       _decimal('limite_credito', 0),
            'prazo_pagamento_dias': _int('prazo_pagamento_dias', 0),
            'grupo_desconto':       row.get('grupo_desconto', '')[:50],
            'consumidor_final':     _bool('consumidor_final', True),
            'contribuinte_icms':    _bool('contribuinte_icms', False),
            'optante_simples':      _bool('optante_simples', False),
            'bloqueado':            _bool('bloqueado', False),
            'observacao':           row.get('observacao', ''),
            'id_externo':           row.get('id_externo', '')[:100],
        }
