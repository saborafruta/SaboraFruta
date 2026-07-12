"""
Comando único para limpar vendas PDV de um cliente pelo CPF/CNPJ.
Apagar após execução no Railway.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Apaga todas as vendas PDV de um cliente pelo CPF (irreversível)'

    def handle(self, *args, **options):
        CPF = '04651988482'

        with connection.cursor() as cur:
            # Encontra o cliente
            cur.execute(
                "SELECT id, razao_social FROM clientes WHERE cpf_cnpj = %s",
                [CPF],
            )
            row = cur.fetchone()

        if not row:
            self.stdout.write(f'Cliente CPF {CPF} não encontrado — nada a fazer.')
            return

        cliente_id, nome = row
        self.stdout.write(f'Cliente: {nome} (id={cliente_id})')

        with connection.cursor() as cur:
            cur.execute(
                "SELECT id FROM vendas_pdv WHERE cliente_id = %s",
                [cliente_id],
            )
            venda_ids = [r[0] for r in cur.fetchall()]

        if not venda_ids:
            self.stdout.write('Nenhuma venda encontrada para este cliente.')
            return

        self.stdout.write(f'{len(venda_ids)} venda(s) encontrada(s). Apagando...')

        with connection.cursor() as cur:
            cur.execute(
                """DELETE FROM itens_devolucao_pdv
                   WHERE devolucao_id IN (
                       SELECT id FROM devolucoes_pdv WHERE venda_pdv_id = ANY(%s)
                   )""",
                [venda_ids],
            )
            self.stdout.write(f'  itens_devolucao_pdv: {cur.rowcount}')

            cur.execute(
                "DELETE FROM devolucoes_pdv WHERE venda_pdv_id = ANY(%s)",
                [venda_ids],
            )
            self.stdout.write(f'  devolucoes_pdv: {cur.rowcount}')

            cur.execute(
                "DELETE FROM pesagens_pdv WHERE venda_pdv_id = ANY(%s)",
                [venda_ids],
            )
            self.stdout.write(f'  pesagens_pdv: {cur.rowcount}')

            cur.execute(
                "DELETE FROM pagamentos_venda_pdv WHERE venda_pdv_id = ANY(%s)",
                [venda_ids],
            )
            self.stdout.write(f'  pagamentos_venda_pdv: {cur.rowcount}')

            cur.execute(
                "DELETE FROM itens_venda_pdv WHERE venda_pdv_id = ANY(%s)",
                [venda_ids],
            )
            self.stdout.write(f'  itens_venda_pdv: {cur.rowcount}')

            cur.execute(
                "DELETE FROM vendas_pdv WHERE id = ANY(%s)",
                [venda_ids],
            )
            self.stdout.write(f'  vendas_pdv: {cur.rowcount}')

        self.stdout.write(
            self.style.SUCCESS(
                f'Concluído. {len(venda_ids)} venda(s) de {nome} apagada(s).'
            )
        )
