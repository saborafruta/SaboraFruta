"""
Comando único para limpar TODAS as vendas PDV do sistema.
Apagar após execução no Railway.
"""
from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Apaga TODAS as vendas PDV do sistema (irreversível)'

    def handle(self, *args, **options):
        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM vendas_pdv")
            total = cur.fetchone()[0]

        if total == 0:
            self.stdout.write('Nenhuma venda encontrada — nada a fazer.')
            return

        self.stdout.write(f'{total} venda(s) encontrada(s). Apagando tudo...')

        with connection.cursor() as cur:
            cur.execute("DELETE FROM itens_devolucao_pdv")
            self.stdout.write(f'  itens_devolucao_pdv: {cur.rowcount}')

            cur.execute("DELETE FROM devolucoes_pdv")
            self.stdout.write(f'  devolucoes_pdv: {cur.rowcount}')

            cur.execute("DELETE FROM pesagens_pdv")
            self.stdout.write(f'  pesagens_pdv: {cur.rowcount}')

            cur.execute("DELETE FROM pagamentos_venda_pdv")
            self.stdout.write(f'  pagamentos_venda_pdv: {cur.rowcount}')

            cur.execute("DELETE FROM itens_venda_pdv")
            self.stdout.write(f'  itens_venda_pdv: {cur.rowcount}')

            cur.execute("DELETE FROM vendas_pdv")
            self.stdout.write(f'  vendas_pdv: {cur.rowcount}')

        self.stdout.write(
            self.style.SUCCESS(f'Concluído. {total} venda(s) apagada(s).')
        )
