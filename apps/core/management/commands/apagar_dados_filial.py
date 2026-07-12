from django.core.management.base import BaseCommand
from django.db import connection, transaction


class Command(BaseCommand):
    help = 'Apaga TODOS os dados de uma filial pelo CNPJ (irreversível)'

    def add_arguments(self, parser):
        parser.add_argument('cnpj', type=str, help='CNPJ da filial (apenas dígitos ou formatado)')

    def handle(self, *args, **options):
        cnpj = ''.join(filter(str.isdigit, options['cnpj']))
        if len(cnpj) != 14:
            self.stdout.write(self.style.ERROR(f'CNPJ inválido: {cnpj} (precisa ter 14 dígitos)'))
            return

        with connection.cursor() as cursor:
            cursor.execute("SELECT id, nome FROM core_filial WHERE cnpj = %s", [cnpj])
            row = cursor.fetchone()

        if not row:
            self.stdout.write(self.style.ERROR(f'Filial com CNPJ {cnpj} não encontrada.'))
            return

        filial_id, nome = row
        self.stdout.write(f'Filial encontrada: {nome} (id={filial_id}, CNPJ={cnpj})')

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT table_name FROM information_schema.columns
                WHERE column_name = 'filial_id' AND table_schema = 'public'
                ORDER BY table_name
            """)
            tabelas_com_filial = [r[0] for r in cursor.fetchall()]

        self.stdout.write(f'Tabelas a limpar: {len(tabelas_com_filial)}')

        with transaction.atomic():
            with connection.cursor() as cursor:
                # Desabilita triggers/FKs para esta sessão (requer privilégio de replicação ou superuser)
                try:
                    cursor.execute("SET session_replication_role = replica")
                    self._apagar_via_sql(cursor, tabelas_com_filial, filial_id)
                    cursor.execute("SET session_replication_role = DEFAULT")
                    self.stdout.write(self.style.SUCCESS('Dados apagados com sucesso (modo replication).'))
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f'Modo replication não disponível ({e}), tentando ordem explícita...'))
                    cursor.execute("ROLLBACK TO SAVEPOINT s1") if False else None
                    raise

    def _apagar_via_sql(self, cursor, tabelas, filial_id):
        # Ordem de deleção: tabelas mais dependentes primeiro
        ORDEM = [
            # PDV - sub-itens antes dos pais
            'pdv_itemdevolucaopdv',
            'pdv_devolucaopdv',
            'pdv_pesagempdv',
            'pdv_pagamentovendapdv',
            'pdv_itemvendapdv',
            'pdv_impressaolog',
            'pdv_vendapdv',
            'pdv_movimentacaocaixa',
            'pdv_sessaopdv',
            'pdv_impressoraconfig',
            'pdv_dispositivopdv',
            'pdv_caixa',
            'pdv_pdvcache',
            # Financeiro
            'financeiro_contareceberrecebimento',
            'financeiro_contareceber',
            'financeiro_contapagarstatus',
            'financeiro_contapagar',
            'financeiro_conciliacaobancaria',
            'financeiro_extratobancario',
            'financeiro_agendapagamento',
            'financeiro_pixcobrancabaixa',
            'financeiro_pixcobranca',
            'financeiro_boletostatus',
            'financeiro_boleto',
            'financeiro_tefconfiguracao',
            'financeiro_tefoperacao',
            'financeiro_documentofiscalitem',
            'financeiro_documentofiscal',
            'financeiro_dre',
            'financeiro_contabancaria',
            # Estoque
            'estoque_movimentacaoestoque',
            'estoque_loteproduto',
            'estoque_alertavencimento',
            'estoque_inventarioproduto',
            'estoque_inventario',
            'estoque_estoque',
            # Compras
            'compras_entradanfrateiofinanceiro',
            'compras_entradanfimpostocalculado',
            'compras_entradanfitem',
            'compras_entradanfparcela',
            'compras_entradanf',
            'compras_pedidocompraitem',
            'compras_pedidocompra',
            'compras_avaliacaofornecedor',
            # Vendas
            'vendas_separacaoitem',
            'vendas_separacaopedido',
            'vendas_pedidovendaitem',
            'vendas_pedidovenda',
            'vendas_devolucaovenda',
            # Logistica
            'logistica_cteitem',
            'logistica_cte',
            'logistica_mdfeitem',
            'logistica_mdfe',
            'logistica_manifestoitem',
            'logistica_manifestocarga',
            'logistica_ordemcoletaitem',
            'logistica_ordemcoleta',
            'logistica_pedidoexpedicaoitem',
            'logistica_pedidoexpedicao',
            'logistica_romaneioitem',
            'logistica_romaneiocarga',
            # Producao
            'producao_ordemproducaoitem',
            'producao_ordemproducao',
            'producao_fichatecnicaitem',
            'producao_fichatecnica',
            # Fiscal
            'fiscal_manifestofiscaldocumento',
            'fiscal_manifestofiscalconfig',
            # Qualidade
            'qualidade_analisequalidadeitem',
            'qualidade_analisequalidate',
            'qualidade_parametroqualidadeproduto',
            'qualidade_parametroqualidadecategoria',
            # Analytics
            'analytics_travaperidoo',
            'analytics_logvenda',
            'analytics_lognfe',
            'analytics_logacesso',
            'analytics_logsistema',
            'analytics_configuracaomodulo',
            'analytics_moduloativo',
            'analytics_integracao',
            'analytics_webhook',
            'analytics_automacao',
            # Produtos (vinculo com filial)
            'produtos_atualizacaoprecolomte',
            'produtos_kitcategoriaitem',
            'produtos_kitcategoria',
            'produtos_kitprodutoitem',
            'produtos_kitproduto',
            'produtos_brindeprodutoitem',
            'produtos_brindeproduto',
            'produtos_promocaoquantidadefaixa',
            'produtos_promocaoquantidade',
            'produtos_tabelaprecoitem',
            'produtos_tabelapreco',
            'produtos_produtofilial',
            'produtos_produto',
            # Cadastros
            'cadastros_representante',
            'cadastros_veiculo',
            'cadastros_motorista',
            'cadastros_transportadorafilial',
            'cadastros_transportadora',
            'cadastros_rota',
            'cadastros_praca',
            'cadastros_fornecedorfilial',
            'cadastros_fornecedor',
            'cadastros_clientefilial',
            'cadastros_cliente',
            # Core
            'core_usuariofilialacesso',
            'core_registroauditoria',
            'core_parametrossistema',
        ]

        tabelas_set = set(tabelas)
        total = 0

        # Deletar na ordem explícita primeiro
        for tabela in ORDEM:
            if tabela in tabelas_set:
                cursor.execute(f'DELETE FROM "{tabela}" WHERE filial_id = %s', [filial_id])
                n = cursor.rowcount
                if n:
                    self.stdout.write(f'  {tabela}: {n} registros removidos')
                    total += n
                tabelas_set.discard(tabela)

        # Qualquer tabela restante não prevista na ordem
        for tabela in tabelas_set:
            cursor.execute(f'DELETE FROM "{tabela}" WHERE filial_id = %s', [filial_id])
            n = cursor.rowcount
            if n:
                self.stdout.write(f'  {tabela} (restante): {n} registros removidos')
                total += n

        # Por último, apaga a própria filial
        cursor.execute('DELETE FROM core_filial WHERE id = %s', [filial_id])
        self.stdout.write(f'  core_filial: 1 registro removido')
        total += 1

        self.stdout.write(f'Total: {total} registros apagados.')
