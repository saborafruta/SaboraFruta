"""Servicos de consistencia entre empresa e filial."""

from apps.core.models import Empresa, Filial


class EmpresaFilialService:
    """Garante que uma empresa ativa tenha uma unidade acessivel."""

    @classmethod
    def garantir_matriz(cls, empresa: Empresa):
        filial = (
            Filial.objects.filter(empresa=empresa, ativo=True, is_matriz=True).first()
            or Filial.objects.filter(empresa=empresa, ativo=True).order_by('created_at', 'pk').first()
        )
        if filial:
            changed = []
            if not filial.is_matriz:
                filial.is_matriz = True
                changed.append('is_matriz')
            if not filial.nome_fantasia and empresa.nome_fantasia:
                filial.nome_fantasia = empresa.nome_fantasia
                changed.append('nome_fantasia')
            if changed:
                changed.append('updated_at')
                filial.save(update_fields=changed)
            return filial, False

        filial_por_documento = Filial.objects.filter(cnpj=empresa.cnpj).first()
        if filial_por_documento and filial_por_documento.empresa_id == empresa.pk:
            filial_por_documento.ativo = True
            filial_por_documento.is_matriz = True
            filial_por_documento.save(update_fields=['ativo', 'is_matriz', 'updated_at'])
            return filial_por_documento, False

        filial = Filial.objects.create(
            empresa=empresa,
            razao_social=empresa.razao_social,
            nome_fantasia=empresa.nome_fantasia or empresa.razao_social[:100],
            cnpj=empresa.cnpj,
            inscricao_estadual=empresa.inscricao_estadual,
            inscricao_municipal=empresa.inscricao_municipal,
            is_matriz=True,
            endereco=empresa.endereco,
            numero=empresa.numero,
            complemento=empresa.complemento,
            bairro=empresa.bairro,
            cidade=empresa.cidade,
            uf=empresa.uf or 'NA',
            cep=empresa.cep,
            codigo_municipio_ibge=empresa.codigo_municipio_ibge,
            telefone=empresa.telefone,
            email=empresa.email,
            regime_tributario=empresa.regime_tributario,
            codigo_regime_tributario=empresa.codigo_regime_tributario,
            ambiente_nfe=empresa.ambiente_nfe,
            ativo=True,
        )
        return filial, True
