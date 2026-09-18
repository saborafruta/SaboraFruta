from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("pdv", "0025_monitoramento_instalacao_offline")]

    operations = [
        migrations.AddField(
            model_name="instalacaopdvoffline",
            name="fila_resumo",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name="TesteContingenciaPDV",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cenario", models.CharField(choices=[
                    ("queda_carrinho", "Queda durante a montagem do carrinho"),
                    ("queda_antes_finalizar", "Queda imediatamente antes de finalizar"),
                    ("resposta_perdida", "Venda concluída com resposta perdida"),
                    ("duas_vendas", "Duas vendas offline e reconexão"),
                    ("reabertura_navegador", "Fechar e reabrir com venda na fila"),
                    ("queda_energia", "Desligamento abrupto com carrinho aberto"),
                    ("catalogo_vencido", "Catálogo local vencido"),
                    ("pagamento_bloqueado", "Pagamento/cliente dependente de internet"),
                    ("erro_estoque", "Erro de estoque na sincronização"),
                    ("caixa_fechado", "Caixa fechado antes da sincronização"),
                    ("queda_nfce", "Queda durante emissão normal de NFC-e"),
                    ("contingencia_fiscal", "Entrada e saída da contingência fiscal"),
                    ("troca_maquina", "Formatação ou substituição da máquina"),
                    ("dois_caixas", "Dois caixas simultâneos na filial"),
                ], db_index=True, max_length=40)),
                ("resultado", models.CharField(choices=[
                    ("aprovado", "Aprovado"), ("falhou", "Falhou"), ("bloqueado", "Bloqueado"),
                ], db_index=True, max_length=20)),
                ("resultado_esperado", models.TextField()),
                ("resultado_obtido", models.TextField()),
                ("local_id", models.CharField(blank=True, max_length=40)),
                ("responsavel_id", models.BigIntegerField(blank=True, null=True)),
                ("responsavel_nome", models.CharField(max_length=160)),
                ("executado_em", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("instalacao", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="testes_contingencia", to="pdv.instalacaopdvoffline")),
            ],
            options={"db_table": "pdv_testes_contingencia", "ordering": ["-executado_em", "-pk"]},
        ),
        migrations.AddIndex(
            model_name="testecontingenciapdv",
            index=models.Index(fields=["instalacao", "cenario", "-executado_em"], name="pdv_teste_inst_cen_idx"),
        ),
        migrations.AddIndex(
            model_name="testecontingenciapdv",
            index=models.Index(fields=["resultado", "-executado_em"], name="pdv_teste_result_idx"),
        ),
    ]
