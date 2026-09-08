"""Regras de negócio de Cliente."""
from __future__ import annotations


from apps.core.tenant_context import tenant_atomic
from apps.cadastros.models import Cliente
from apps.cadastros.services.cep_service import CepService
from apps.cadastros.services.replicacao_service import ReplicacaoCadastrosService
from apps.core.services.exceptions import DadosInvalidosError


class ClienteService:

    @staticmethod
    @tenant_atomic
    def criar(dados: dict, usuario, filial) -> Cliente:
        """Cria cliente validando duplicidade de CPF/CNPJ na filial."""
        cpf_cnpj = dados.get('cpf_cnpj')
        if cpf_cnpj and Cliente.objects.for_filial(filial).filter(cpf_cnpj=cpf_cnpj).exists():
            raise DadosInvalidosError(f'Já existe cliente com este CPF/CNPJ ({cpf_cnpj}) nesta filial.')

        # Remove campos que não pertencem ao model Cliente (ex: tabela_preco)
        campos_validos = {f.name for f in Cliente._meta.get_fields() if hasattr(f, 'column')}
        dados_limpos = {k: v for k, v in dados.items() if k in campos_validos}

        # Garante que novo cliente sempre começa ativo
        dados_limpos['ativo'] = True

        cliente = Cliente(filial=filial, **dados_limpos)
        cliente.save()
        ReplicacaoCadastrosService.sincronizar_cliente(cliente)
        return cliente

    @staticmethod
    @tenant_atomic
    def atualizar(cliente: Cliente, dados: dict) -> Cliente:
        cpf_cnpj = dados.get('cpf_cnpj')
        if cpf_cnpj:
            qs = Cliente.objects.for_filial(cliente.filial).filter(cpf_cnpj=cpf_cnpj).exclude(pk=cliente.pk)
            if qs.exists():
                raise DadosInvalidosError(f'Já existe outro cliente com este CPF/CNPJ ({cpf_cnpj}) na filial.')

        campos_validos = {f.name for f in Cliente._meta.get_fields() if hasattr(f, 'column')}
        # Nunca deixa o form sobrescrever filial ou ativo
        campos_protegidos = {'filial', 'filial_id', 'ativo', 'id', 'created_at', 'updated_at'}
        for campo, valor in dados.items():
            if campo in campos_validos and campo not in campos_protegidos:
                setattr(cliente, campo, valor)
        cliente.save()
        ReplicacaoCadastrosService.sincronizar_cliente(cliente)
        return cliente

    @staticmethod
    def enriquecer_por_cep(cep: str) -> dict | None:
        """Consulta CEP e retorna dicionário pronto para preencher o form."""
        return CepService.consultar(cep)

    @staticmethod
    def calcular_classificacao_estrelas(cliente: Cliente) -> int:
        if cliente.pontos_fidelidade >= 10000:
            return 5
        if cliente.pontos_fidelidade >= 5000:
            return 4
        if cliente.pontos_fidelidade >= 1000:
            return 3
        if cliente.pontos_fidelidade > 0:
            return 2
        return 1
