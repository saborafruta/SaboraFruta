"""Replicacao de produtos entre filiais conforme politica administrativa."""
from django.core.exceptions import ObjectDoesNotExist
from django.db import connection

from apps.core.tenant_context import tenant_atomic
from apps.core.models import Filial
from apps.produtos.models import (
    CategoriaProduto, CategoriaProdutoFilial, ClasseFiscal, ClasseFiscalFilial,
    ItemTabelaPreco, MarcaProduto, MarcaProdutoFilial, NaturezaOperacao, NaturezaOperacaoFilial, Produto, ProdutoFilial,
    TabelaPreco, TabelaPrecoFilial, UnidadeMedida, UnidadeMedidaFilial,
)


class ReplicacaoProdutoService:
    """Sincroniza cadastros de produtos, tabelas e fichas entre filiais."""

    CAMPOS_BASICOS = [
        'categoria', 'subcategoria', 'linha_producao', 'unidade_medida',
        'unidade_medida_compra', 'marca', 'fornecedor',
        'fator_conversao_compra', 'codigo', 'codigo_barras',
        'codigos_barras_extras', 'descricao',
        'descricao_curta', 'descricao_completa', 'descricao_pdv',
        'tipo_produto', 'observacao', 'ativo', 'foto_url',
        'permite_venda_sem_estoque',
    ]
    CAMPOS_FISCAL = [
        'ncm', 'cest', 'cfop_venda_interna', 'cfop_venda_interestadual',
        'cfop_venda_exportacao', 'cfop_devolucao', 'cfop_devolucao_compra',
        'cfop_compra', 'origem_produto', 'classe_fiscal', 'cst_csosn',
        'codigo_beneficio_fiscal_icms', 'modalidade_bc_icms', 'aliquota_icms',
        'reducao_bc_icms', 'aliquota_fcp', 'modalidade_bc_icms_st',
        'mva_icms_st', 'reducao_bc_icms_st', 'aliquota_icms_st',
        'aliquota_fcp_st', 'cst_pis', 'aliquota_pis', 'cst_cofins',
        'aliquota_cofins', 'natureza_receita_pis_cofins', 'cst_ipi',
        'codigo_enquadramento_ipi', 'aliquota_ipi', 'ex_tipi', 'cst_cbs',
        'classificacao_tributaria_cbs', 'aliquota_cbs', 'reducao_cbs',
        'cst_ibs', 'classificacao_tributaria_ibs', 'aliquota_ibs_uf',
        'aliquota_ibs_municipal', 'reducao_ibs', 'cst_is',
        'classificacao_tributaria_is', 'aliquota_is',
        'informacoes_complementares_fiscais', 'beneficios_fiscais_observacoes',
    ]
    CAMPOS_PRECO_VENDA = [
        'preco_venda', 'margem_lucro', 'moeda', 'margem_desejada',
        'markup', 'preco_sugerido', 'preco_minimo',
    ]
    CAMPOS_CUSTO = ['preco_custo', 'preco_custo_medio']
    CAMPOS_FICHA_TECNICA = [
        'codigo_balanca', 'tara_padrao', 'variacao_peso_permitida',
        'fracionavel', 'vendido_por_peso_granel', 'peso_minimo_venda',
        'unidade_pesagem', 'gera_etiqueta_balanca', 'estoque_minimo',
        'estoque_maximo', 'ponto_reposicao', 'estoque_seguranca',
        'lead_time_reposicao_dias', 'metodo_saida', 'controla_lote',
        'controla_validade', 'dias_aviso_vencimento', 'saida_fefo',
        'peso_bruto', 'peso_liquido', 'largura', 'altura', 'profundidade',
        'unidade_peso', 'unidade_dimensao', 'tipo_embalagem',
        'quantidade_por_embalagem', 'empilhamento_maximo',
        'localizacao_estoque', 'condicao_armazenamento',
        'temperatura_minima', 'temperatura_maxima', 'umidade_relativa',
        'especificacoes_tecnicas',
    ]
    CAMPOS_TABELA_PRECO = [
        'descricao', 'tipo', 'data_inicio', 'data_fim', 'permite_desconto',
        'desconto_maximo_geral', 'acrescimo_percentual', 'ativo',
    ]
    CAMPOS_ITEM_TABELA = [
        'preco_unitario', 'desconto_maximo', 'quantidade_minima',
    ]
    CAMPOS_FICHA = [
        'codigo', 'descricao', 'versao', 'quantidade_produzida',
        'tempo_producao_minutos', 'custo_mao_obra_padrao',
        'custo_indireto_padrao', 'status', 'observacao',
    ]
    CAMPOS_ITEM_FICHA = ['quantidade', 'perda_prevista', 'observacao']
    CAMPOS_CATEGORIA = ['nome', 'descricao', 'ativo']
    CAMPOS_MARCA = ['nome', 'descricao', 'ativo']
    CAMPOS_QUALIDADE_PRODUTO = [
        'etapa', 'nome_parametro', 'tipo_valor', 'unidade_medida',
        'valor_minimo', 'valor_maximo', 'valor_ideal',
        'valor_texto_ideal', 'opcoes', 'obrigatorio', 'ativo',
    ]
    CAMPOS_QUALIDADE_CATEGORIA = CAMPOS_QUALIDADE_PRODUTO

    @classmethod
    def _politica(cls, filial):
        if not filial or not getattr(filial, 'empresa_id', None):
            return None
        try:
            politica = filial.politica_replicacao
        except ObjectDoesNotExist:
            try:
                politica = filial.empresa.politica_replicacao
            except ObjectDoesNotExist:
                return None
        if not politica or not politica.ativo:
            return None
        return politica

    @classmethod
    def _tabelas_existem(cls, *nomes):
        tabelas = set(connection.introspection.table_names())
        return all(nome in tabelas for nome in nomes)

    @classmethod
    def _filiais_destino(cls, filial, campo_politica=None):
        if not filial or not filial.participa_replicacao:
            return Filial.objects.none()
        filiais = Filial.objects.filter(
            empresa=filial.empresa,
            ativo=True,
            participa_replicacao=True,
        ).exclude(pk=filial.pk)
        if not campo_politica:
            return filiais
        filiais_permitidas = [
            destino.pk
            for destino in filiais
            if getattr(cls._politica(destino), campo_politica, False)
        ]
        return filiais.filter(pk__in=filiais_permitidas)

    @classmethod
    def _shared_id(cls, obj, prefixo):
        if getattr(obj, 'id_externo', ''):
            return obj.id_externo
        obj.id_externo = f'{prefixo}:{obj.pk}'
        update_fields = ['id_externo']
        if any(field.name == 'updated_at' for field in obj._meta.fields):
            update_fields.append('updated_at')
        obj.save(update_fields=update_fields)
        return obj.id_externo

    @classmethod
    def _vincular_produto(cls, produto, filial):
        if not produto or not filial:
            return None
        vinculo, _ = ProdutoFilial.objects.get_or_create(
            produto=produto,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _vincular_categoria(cls, categoria, filial):
        if not categoria or not filial:
            return None
        vinculo, _ = CategoriaProdutoFilial.objects.update_or_create(
            categoria=categoria,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _vincular_marca(cls, marca, filial):
        if not marca or not filial:
            return None
        vinculo, _ = MarcaProdutoFilial.objects.update_or_create(
            marca=marca,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _vincular_tabela_preco(cls, tabela, filial):
        if not tabela or not filial:
            return None
        vinculo, _ = TabelaPrecoFilial.objects.update_or_create(
            tabela=tabela,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _vincular_unidade(cls, unidade, filial):
        if not unidade or not filial:
            return None
        vinculo, _ = UnidadeMedidaFilial.objects.update_or_create(
            unidade=unidade,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _vincular_classe_fiscal(cls, classe_fiscal, filial):
        if not classe_fiscal or not filial:
            return None
        vinculo, _ = ClasseFiscalFilial.objects.update_or_create(
            classe_fiscal=classe_fiscal,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _vincular_natureza_operacao(cls, natureza, filial):
        if not natureza or not filial:
            return None
        vinculo, _ = NaturezaOperacaoFilial.objects.update_or_create(
            natureza=natureza,
            filial=filial,
            defaults={'ativo': True},
        )
        return vinculo

    @classmethod
    def _categoria_destino(cls, categoria, filial_destino):
        if not categoria:
            return None
        qs = CategoriaProduto.objects.for_filial(filial_destino).filter(empresa=categoria.empresa)
        if categoria.id_externo:
            encontrada = qs.filter(id_externo=categoria.id_externo).first()
            if encontrada:
                return encontrada
        parent_destino = cls._categoria_destino(categoria.categoria_pai, filial_destino)
        return qs.filter(categoria_pai=parent_destino, nome=categoria.nome).first()

    @classmethod
    def _marca_destino(cls, marca, filial_destino):
        if not marca:
            return None
        qs = MarcaProduto.objects.for_filial(filial_destino).filter(empresa=marca.empresa)
        if marca.id_externo:
            encontrada = qs.filter(id_externo=marca.id_externo).first()
            if encontrada:
                return encontrada
        return qs.filter(nome=marca.nome).first()

    @classmethod
    @tenant_atomic
    def sincronizar_categoria(cls, categoria):
        politica = cls._politica(categoria.filial)
        if not politica or not politica.replicar_categorias:
            cls._vincular_categoria(categoria, categoria.filial)
            return []

        if categoria.categoria_pai_id:
            cls.sincronizar_categoria(categoria.categoria_pai)
        cls._shared_id(categoria, 'categoria')
        cls._vincular_categoria(categoria, categoria.filial)
        sincronizadas = []
        for filial_destino in cls._filiais_destino(categoria.filial, 'replicar_categorias'):
            cls._vincular_categoria(categoria, filial_destino)
            sincronizadas.append(categoria)
        return sincronizadas

    @classmethod
    @tenant_atomic
    def sincronizar_marca(cls, marca):
        politica = cls._politica(marca.filial)
        if not politica or not getattr(politica, 'replicar_marcas', False):
            cls._vincular_marca(marca, marca.filial)
            return []

        cls._shared_id(marca, 'marca')
        cls._vincular_marca(marca, marca.filial)
        sincronizadas = []
        for filial_destino in cls._filiais_destino(marca.filial, 'replicar_marcas'):
            cls._vincular_marca(marca, filial_destino)
            sincronizadas.append(marca)
        return sincronizadas

    @classmethod
    @tenant_atomic
    def sincronizar_unidade(cls, unidade, filial_origem=None):
        filial_origem = filial_origem or unidade.filiais_vinculo.filter(ativo=True).select_related('filial').first()
        filial_origem = getattr(filial_origem, 'filial', filial_origem)
        politica = cls._politica(filial_origem)
        cls._vincular_unidade(unidade, filial_origem)
        if not politica or not politica.replicar_unidades:
            return []

        cls._shared_id(unidade, 'unidade')
        sincronizadas = []
        for filial_destino in cls._filiais_destino(filial_origem, 'replicar_unidades'):
            cls._vincular_unidade(unidade, filial_destino)
            sincronizadas.append(unidade)
        return sincronizadas

    @classmethod
    @tenant_atomic
    def sincronizar_classe_fiscal(cls, classe_fiscal, filial_origem=None):
        filial_origem = filial_origem or classe_fiscal.filiais_vinculo.filter(ativo=True).select_related('filial').first()
        filial_origem = getattr(filial_origem, 'filial', filial_origem)
        politica = cls._politica(filial_origem)
        cls._vincular_classe_fiscal(classe_fiscal, filial_origem)
        if not politica or not politica.replicar_fiscal_basico:
            return []

        cls._shared_id(classe_fiscal, 'classe_fiscal')
        sincronizadas = []
        for filial_destino in cls._filiais_destino(filial_origem, 'replicar_fiscal_basico'):
            cls._vincular_classe_fiscal(classe_fiscal, filial_destino)
            sincronizadas.append(classe_fiscal)
        return sincronizadas

    @classmethod
    @tenant_atomic
    def sincronizar_natureza_operacao(cls, natureza, filial_origem=None):
        filial_origem = filial_origem or natureza.filiais_vinculo.filter(ativo=True).select_related('filial').first()
        filial_origem = getattr(filial_origem, 'filial', filial_origem)
        politica = cls._politica(filial_origem)
        cls._vincular_natureza_operacao(natureza, filial_origem)
        if not politica or not politica.replicar_fiscal_basico:
            return []

        cls._shared_id(natureza, 'natureza_operacao')
        sincronizadas = []
        for filial_destino in cls._filiais_destino(filial_origem, 'replicar_fiscal_basico'):
            cls._vincular_natureza_operacao(natureza, filial_destino)
            sincronizadas.append(natureza)
        return sincronizadas

    @classmethod
    def _campos_produto(cls, politica):
        campos = list(cls.CAMPOS_BASICOS)
        if politica.replicar_fiscal_basico:
            campos += cls.CAMPOS_FISCAL
        if politica.replicar_preco_venda:
            campos += cls.CAMPOS_PRECO_VENDA
        if politica.replicar_custo_base:
            campos += cls.CAMPOS_CUSTO
        if politica.replicar_ficha_tecnica:
            campos += cls.CAMPOS_FICHA_TECNICA
        return campos

    @classmethod
    def _dados_produto(cls, produto, politica):
        dados = {campo: getattr(produto, campo) for campo in cls._campos_produto(politica)}
        dados['id_externo'] = produto.id_externo
        return dados

    @classmethod
    def _dados_produto_destino(cls, produto, politica, filial_destino):
        dados = cls._dados_produto(produto, politica)
        if produto.categoria_id:
            if politica.replicar_categorias:
                cls.sincronizar_categoria(produto.categoria)
                dados['categoria'] = cls._categoria_destino(produto.categoria, filial_destino)
            else:
                dados.pop('categoria', None)
        if produto.subcategoria_id:
            if politica.replicar_categorias:
                cls.sincronizar_categoria(produto.subcategoria)
                dados['subcategoria'] = cls._categoria_destino(produto.subcategoria, filial_destino)
            else:
                dados.pop('subcategoria', None)
        if produto.marca_id:
            if getattr(politica, 'replicar_marcas', False):
                cls.sincronizar_marca(produto.marca)
                dados['marca'] = cls._marca_destino(produto.marca, filial_destino)
            else:
                dados.pop('marca', None)
        return dados

    @classmethod
    def _produto_destino(cls, produto, filial_destino):
        qs = Produto.objects.for_filial(filial_destino)
        if produto.id_externo:
            encontrado = qs.filter(id_externo=produto.id_externo).first()
            if encontrado:
                return encontrado
        if produto.codigo:
            encontrado = qs.filter(codigo=produto.codigo).first()
            if encontrado:
                return encontrado
        if produto.codigo_barras:
            encontrado = qs.filter(codigo_barras=produto.codigo_barras).first()
            if encontrado:
                return encontrado
        return None

    @classmethod
    @tenant_atomic
    def sincronizar_produto(cls, produto):
        politica = cls._politica(produto.filial)
        if not politica or not politica.replicar_produtos_basicos:
            cls._vincular_produto(produto, produto.filial)
            return []

        cls._shared_id(produto, 'produto')
        cls._vincular_produto(produto, produto.filial)
        sincronizados = []
        for filial_destino in cls._filiais_destino(produto.filial, 'replicar_produtos_basicos'):
            cls._vincular_produto(produto, filial_destino)
            sincronizados.append(produto)
        return sincronizados

    @classmethod
    @tenant_atomic
    def sincronizar_tabela_preco(cls, tabela):
        politica = cls._politica(tabela.filial)
        if not politica or not politica.replicar_tabelas_preco:
            cls._vincular_tabela_preco(tabela, tabela.filial)
            return []

        cls._vincular_tabela_preco(tabela, tabela.filial)
        sincronizadas = []
        for filial_destino in cls._filiais_destino(tabela.filial, 'replicar_tabelas_preco'):
            cls._vincular_tabela_preco(tabela, filial_destino)
            sincronizadas.append(tabela)
        return sincronizadas

    @classmethod
    def _sincronizar_itens_tabela(cls, origem, destino):
        for item in origem.itens.select_related('produto'):
            produto_destino = cls._produto_destino(item.produto, destino.filial)
            if not produto_destino:
                continue
            dados = {campo: getattr(item, campo) for campo in cls.CAMPOS_ITEM_TABELA}
            dados['produto'] = produto_destino
            ItemTabelaPreco.objects.update_or_create(
                tabela=destino,
                produto=produto_destino,
                quantidade_minima=item.quantidade_minima,
                defaults=dados,
            )

    @classmethod
    @tenant_atomic
    def sincronizar_ficha_tecnica(cls, ficha):
        politica = cls._politica(ficha.filial)
        if not politica or not politica.replicar_ficha_tecnica:
            return []

        from apps.producao.models import FichaTecnica

        produto_destinos = cls.sincronizar_produto(ficha.produto_acabado)
        sincronizadas = []
        for produto_destino in produto_destinos:
            dados = {campo: getattr(ficha, campo) for campo in cls.CAMPOS_FICHA}
            dados['produto_acabado'] = produto_destino
            destino, _ = FichaTecnica.objects.update_or_create(
                filial=produto_destino.filial,
                produto_acabado=produto_destino,
                versao=ficha.versao,
                defaults=dados,
            )
            cls._sincronizar_itens_ficha(ficha, destino)
            sincronizadas.append(destino)
        return sincronizadas

    @classmethod
    def _sincronizar_itens_ficha(cls, origem, destino):
        from apps.producao.models import ItemFichaTecnica

        for item in origem.itens.select_related('materia_prima'):
            cls.sincronizar_produto(item.materia_prima)
            materia_prima_destino = cls._produto_destino(item.materia_prima, destino.filial)
            if not materia_prima_destino:
                continue
            dados = {campo: getattr(item, campo) for campo in cls.CAMPOS_ITEM_FICHA}
            dados['materia_prima'] = materia_prima_destino
            ItemFichaTecnica.objects.update_or_create(
                ficha=destino,
                materia_prima=materia_prima_destino,
                defaults=dados,
            )

    @classmethod
    @tenant_atomic
    def sincronizar_parametro_qualidade_produto(cls, parametro):
        politica = cls._politica(parametro.filial)
        if not politica or not getattr(politica, 'replicar_qualidade', False):
            return []
        if not cls._tabelas_existem('parametros_qualidade_produtos'):
            return []

        from apps.qualidade.models import ParametroQualidadeProduto

        sincronizados = []
        if getattr(politica, 'replicar_produtos_basicos', False):
            cls.sincronizar_produto(parametro.produto)
        for filial_destino in cls._filiais_destino(parametro.filial, 'replicar_qualidade'):
            produto_destino = cls._produto_destino(parametro.produto, filial_destino)
            if not produto_destino:
                continue
            dados = {campo: getattr(parametro, campo) for campo in cls.CAMPOS_QUALIDADE_PRODUTO}
            dados['produto'] = produto_destino
            destino, _ = ParametroQualidadeProduto.objects.update_or_create(
                filial=filial_destino,
                produto=produto_destino,
                etapa=parametro.etapa,
                nome_parametro=parametro.nome_parametro,
                defaults=dados,
            )
            sincronizados.append(destino)
        return sincronizados

    @classmethod
    @tenant_atomic
    def sincronizar_parametro_qualidade_categoria(cls, parametro):
        politica = cls._politica(parametro.filial)
        if not politica or not getattr(politica, 'replicar_qualidade', False):
            return []
        if not cls._tabelas_existem('parametros_qualidade_categorias'):
            return []

        from apps.qualidade.models import ParametroQualidadeCategoria

        if getattr(politica, 'replicar_categorias', False):
            cls.sincronizar_categoria(parametro.categoria)

        sincronizados = []
        for filial_destino in cls._filiais_destino(parametro.filial, 'replicar_qualidade'):
            categoria_destino = cls._categoria_destino(parametro.categoria, filial_destino)
            if not categoria_destino:
                continue
            dados = {campo: getattr(parametro, campo) for campo in cls.CAMPOS_QUALIDADE_CATEGORIA}
            dados['categoria'] = categoria_destino
            destino, _ = ParametroQualidadeCategoria.objects.update_or_create(
                filial=filial_destino,
                categoria=categoria_destino,
                etapa=parametro.etapa,
                nome_parametro=parametro.nome_parametro,
                defaults=dados,
            )
            sincronizados.append(destino)
        return sincronizados

    @classmethod
    def sincronizar_produtos_da_filial(cls, filial):
        politica = cls._politica(filial)
        if not politica:
            return {
                'categorias': 0,
                'marcas': 0,
                'unidades': 0,
                'fiscal': 0,
                'produtos': 0,
                'tabelas': 0,
                'fichas': 0,
                'qualidade': 0,
                'erros': [],
            }
        total_categorias = 0
        total_marcas = 0
        total_unidades = 0
        total_fiscal = 0
        total_produtos = 0
        total_tabelas = 0
        total_fichas = 0
        total_qualidade = 0
        erros = []

        def executar_bloco(nome, func):
            try:
                return func()
            except Exception as exc:
                erros.append(f'{nome}: {exc}')
                return 0

        if politica.replicar_categorias:
            def _categorias():
                total = 0
                categorias = list(CategoriaProduto.objects.for_filial(filial).filter(
                    empresa=filial.empresa,
                ).order_by('categoria_pai_id', 'nome'))
                for categoria in categorias:
                    total += len(cls.sincronizar_categoria(categoria))
                return total
            total_categorias = executar_bloco('categorias', _categorias)
        if politica.replicar_unidades:
            def _unidades():
                total = 0
                unidades = list(UnidadeMedida.objects.for_filial(filial).filter(
                    empresa=filial.empresa,
                ).order_by('sigla'))
                for unidade in unidades:
                    total += len(cls.sincronizar_unidade(unidade, filial))
                return total
            total_unidades = executar_bloco('unidades', _unidades)
        if getattr(politica, 'replicar_marcas', False):
            def _marcas():
                total = 0
                marcas = list(MarcaProduto.objects.for_filial(filial).filter(
                    empresa=filial.empresa,
                ).order_by('nome'))
                for marca in marcas:
                    total += len(cls.sincronizar_marca(marca))
                return total
            total_marcas = executar_bloco('fabricantes', _marcas)
        if politica.replicar_fiscal_basico:
            def _fiscal():
                total = 0
                classes = list(ClasseFiscal.objects.for_filial(filial).filter(
                    empresa=filial.empresa,
                ).order_by('codigo'))
                for classe in classes:
                    total += len(cls.sincronizar_classe_fiscal(classe, filial))
                naturezas = list(NaturezaOperacao.objects.for_filial(filial).filter(
                    empresa=filial.empresa,
                ).order_by('descricao'))
                for natureza in naturezas:
                    total += len(cls.sincronizar_natureza_operacao(natureza, filial))
                return total
            total_fiscal = executar_bloco('fiscal', _fiscal)
        if politica.replicar_produtos_basicos:
            def _produtos():
                total = 0
                produtos = list(Produto.objects.for_filial(filial))
                for produto in produtos:
                    total += len(cls.sincronizar_produto(produto))
                return total
            total_produtos = executar_bloco('produtos', _produtos)
        if politica.replicar_tabelas_preco:
            def _tabelas():
                total = 0
                tabelas = list(TabelaPreco.objects.for_filial(filial))
                for tabela in tabelas:
                    total += len(cls.sincronizar_tabela_preco(tabela))
                return total
            total_tabelas = executar_bloco('tabelas', _tabelas)
        if politica.replicar_ficha_tecnica:
            def _fichas():
                from apps.producao.models import FichaTecnica

                if not cls._tabelas_existem('producao_fichas_tecnicas', 'producao_itens_ficha_tecnica'):
                    return 0
                total = 0
                fichas = list(FichaTecnica.objects.for_filial(filial))
                for ficha in fichas:
                    total += len(cls.sincronizar_ficha_tecnica(ficha))
                return total
            total_fichas = executar_bloco('fichas', _fichas)
        if getattr(politica, 'replicar_qualidade', False):
            def _qualidade():
                from apps.qualidade.models import ParametroQualidadeCategoria, ParametroQualidadeProduto

                total = 0
                if cls._tabelas_existem('parametros_qualidade_categorias'):
                    padroes = list(ParametroQualidadeCategoria.objects.for_filial(filial))
                    for padrao in padroes:
                        total += len(cls.sincronizar_parametro_qualidade_categoria(padrao))
                if cls._tabelas_existem('parametros_qualidade_produtos'):
                    parametros = list(ParametroQualidadeProduto.objects.for_filial(filial))
                    for parametro in parametros:
                        total += len(cls.sincronizar_parametro_qualidade_produto(parametro))
                return total
            total_qualidade = executar_bloco('qualidade', _qualidade)
        return {
            'categorias': total_categorias,
            'marcas': total_marcas,
            'unidades': total_unidades,
            'fiscal': total_fiscal,
            'produtos': total_produtos,
            'tabelas': total_tabelas,
            'fichas': total_fichas,
            'qualidade': total_qualidade,
            'erros': erros,
        }
