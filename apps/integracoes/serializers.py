from decimal import Decimal

from rest_framework import serializers


def dinheiro(valor):
    return f'{Decimal(valor or 0):.2f}'


class FilialSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    razao_social = serializers.CharField()
    nome_fantasia = serializers.CharField()
    cnpj = serializers.CharField()
    cidade = serializers.CharField()
    uf = serializers.CharField()
    is_matriz = serializers.BooleanField()
    ativo = serializers.BooleanField()
    atualizado_em = serializers.DateTimeField(source='updated_at')


class ProdutoSerializer(serializers.Serializer):
    def to_representation(self, produto):
        return {
            'id': produto.pk,
            'filial': {
                'id': produto.filial_id,
                'cnpj': produto.filial.cnpj,
                'nome': produto.filial.nome_fantasia or produto.filial.razao_social,
            },
            'codigo': produto.codigo,
            'codigo_barras': produto.codigo_barras,
            'codigos_barras_extras': produto.codigos_barras_extras or [],
            'descricao': produto.descricao,
            'descricao_curta': produto.descricao_curta,
            'descricao_pdv': produto.descricao_pdv,
            'tipo': produto.tipo_produto,
            'unidade': str(produto.unidade_medida),
            'preco_venda': dinheiro(produto.preco_venda),
            'preco_promocional': dinheiro(produto.preco_promocional),
            'moeda': produto.moeda,
            'codigo_balanca': produto.codigo_balanca,
            'gera_etiqueta_balanca': produto.gera_etiqueta_balanca,
            'peso_liquido': str(produto.peso_liquido) if produto.peso_liquido is not None else None,
            'foto_url': produto.foto_url,
            'id_externo': produto.id_externo,
            'ativo': produto.ativo,
            'atualizado_em': produto.updated_at.isoformat(),
        }


class ClienteSerializer(serializers.Serializer):
    def to_representation(self, cliente):
        return {
            'id': cliente.pk,
            'filial_origem': {
                'id': cliente.filial_id,
                'cnpj': cliente.filial.cnpj,
            },
            'tipo_pessoa': cliente.tipo_pessoa,
            'razao_social': cliente.razao_social,
            'nome_fantasia': cliente.nome_fantasia,
            'cpf_cnpj': cliente.cpf_cnpj,
            'telefone': cliente.telefone,
            'celular': cliente.celular,
            'email': cliente.email,
            'contato_nome': cliente.contato_nome,
            'endereco': {
                'logradouro': cliente.endereco,
                'numero': cliente.numero,
                'complemento': cliente.complemento,
                'bairro': cliente.bairro,
                'cidade': cliente.cidade,
                'uf': cliente.uf,
                'cep': cliente.cep,
                'pais': cliente.pais,
            },
            'bloqueado': cliente.bloqueado,
            'ativo': cliente.ativo,
            'atualizado_em': cliente.updated_at.isoformat(),
        }


class EstoqueSerializer(serializers.Serializer):
    def to_representation(self, estoque):
        return {
            'id': estoque.pk,
            'filial': {
                'id': estoque.filial_id,
                'cnpj': estoque.filial.cnpj,
            },
            'produto': {
                'id': estoque.produto_id,
                'codigo': estoque.produto.codigo,
                'codigo_barras': estoque.produto.codigo_barras,
                'descricao': estoque.produto.descricao,
            },
            'deposito': {
                'id': estoque.deposito_id,
                'nome': estoque.deposito.nome,
            },
            'quantidade_atual': str(estoque.quantidade_atual),
            'quantidade_reservada': str(estoque.quantidade_reservada),
            'quantidade_disponivel': str(estoque.quantidade_disponivel),
            'ultima_entrada': estoque.ultima_entrada.isoformat() if estoque.ultima_entrada else None,
            'ultima_saida': estoque.ultima_saida.isoformat() if estoque.ultima_saida else None,
            'atualizado_em': estoque.updated_at.isoformat(),
        }


class VarianteModaSerializer(serializers.Serializer):
    def to_representation(self, variante):
        produto = variante.produto
        produto_cor = variante.produto_cor
        return {
            'id': variante.pk,
            'filial': {
                'id': produto.filial_id,
                'cnpj': produto.filial.cnpj,
            },
            'produto': {
                'id': produto.pk,
                'codigo': produto.codigo,
                'referencia': produto.referencia,
                'nome': produto.nome,
            },
            'sku': variante.sku,
            'codigo_barras': variante.codigo_barras,
            'cor': {
                'id': produto_cor.cor_id,
                'nome': produto_cor.cor.nome,
                'sigla': produto_cor.cor.sigla,
                'hex': produto_cor.cor.hex_cor,
                'pantone': produto_cor.cor.codigo_pantone,
                'referencia': produto_cor.referencia_cor,
            },
            'tamanho': {
                'id': variante.tamanho_id,
                'nome': variante.tamanho.nome,
                'sigla': variante.tamanho.sigla,
            },
            'ativo': variante.ativo,
            'criado_em': variante.criado_em.isoformat(),
        }


class ProdutoModaSerializer(serializers.Serializer):
    def to_representation(self, produto):
        request = self.context.get('request')
        incluir_variantes = bool(
            request and request.query_params.get('expandir') == 'variantes'
        )
        data = {
            'id': produto.pk,
            'filial': {
                'id': produto.filial_id,
                'cnpj': produto.filial.cnpj,
                'nome': produto.filial.nome_fantasia or produto.filial.razao_social,
            },
            'codigo': produto.codigo,
            'referencia': produto.referencia,
            'nome': produto.nome,
            'descricao': produto.descricao,
            'tipo_impressao': produto.tipo_impressao,
            'tecido': str(produto.tecido) if produto.tecido_id else '',
            'composicao': produto.composicao,
            'gramatura': produto.gramatura,
            'grade': str(produto.grade) if produto.grade_id else '',
            'status': produto.status,
            'ativo': produto.ativo,
            'atualizado_em': produto.updated_at.isoformat(),
        }
        if incluir_variantes:
            data['variantes'] = VarianteModaSerializer(
                produto.variantes.all(), many=True,
            ).data
        return data


class OrdemProducaoSerializer(serializers.Serializer):
    def to_representation(self, ordem):
        return {
            'id': ordem.pk,
            'numero': ordem.numero,
            'filial': {
                'id': ordem.filial_id,
                'cnpj': ordem.filial.cnpj,
                'nome': ordem.filial.nome_fantasia or ordem.filial.razao_social,
            },
            'pedido': {
                'id': ordem.pedido_id,
                'numero': ordem.pedido.numero,
            },
            'cliente': {
                'id': ordem.pedido.cliente_id,
                'nome': (
                    ordem.pedido.cliente.nome_fantasia
                    or ordem.pedido.cliente.razao_social
                ),
            },
            'item': {
                'id': ordem.item_id,
                'descricao': ordem.descricao_produto,
                'referencia': ordem.item.referencia,
                'cor': str(ordem.item.cor) if ordem.item.cor_id else '',
                'tecido': ordem.item.tecido_exibicao,
            },
            'quantidade': ordem.quantidade,
            'prazo': ordem.prazo.isoformat() if ordem.prazo else None,
            'prioridade': ordem.prioridade,
            'status': ordem.status,
            'status_descricao': ordem.get_status_display(),
            'emitida_em': ordem.emitida_em.isoformat(),
            'atualizado_em': ordem.updated_at.isoformat(),
        }


class OrdemProducaoDetalheSerializer(OrdemProducaoSerializer):
    def to_representation(self, ordem):
        data = super().to_representation(ordem)
        data['observacoes'] = ordem.observacoes
        data['grade'] = [
            {
                'tamanho_id': linha.tamanho_id,
                'tamanho': linha.tamanho.nome,
                'sigla': linha.tamanho.sigla,
                'quantidade': linha.quantidade,
            }
            for linha in ordem.grade
        ]
        data['personalizacoes_individuais'] = [
            {
                'id': pessoa.pk,
                'nome': pessoa.nome,
                'numero': pessoa.numero,
                'identificacao': pessoa.identificacao,
                'tamanho': pessoa.tamanho.sigla,
                'nome_calcao': pessoa.nome_calcao,
                'numero_calcao': pessoa.numero_calcao,
                'tamanho_calcao': pessoa.tamanho_calcao.sigla if pessoa.tamanho_calcao_id else None,
                'observacoes': pessoa.observacoes,
            }
            for pessoa in ordem.individuais
        ]
        return data
