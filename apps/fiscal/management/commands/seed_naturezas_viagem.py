"""
Cadastra as 5 naturezas de operação que a Viagem (venda fora do
estabelecimento) exige, cada uma com as regras de CFOP dentro e fora do
estado.

POR QUE ESTE COMANDO EXISTE
==========================

O fluxo de venda fora do estabelecimento só destrava quando existem, ativas,
as naturezas de espécie ``venda``, ``remessa_venda_fora``, ``venda_fora``,
``bonificacao`` e ``retorno_venda_fora`` -- e cada uma com ao menos uma regra,
porque é a regra que traz o CFOP. Fazer isso a mão são 5 cadastros + 10 regras
por filial; aqui é um comando.

O QUE É FATO E O QUE É SUGESTÃO
==============================

Os CFOP abaixo são os da legislação para cada operação e não dependem da
empresa. Já ``CSOSN``, ``CST PIS`` e ``CST COFINS`` são um ponto de partida
para Simples Nacional -- REVISE com a contabilidade antes de emitir. Todos os
campos ficam editáveis na tela Fiscal > Naturezas de operação depois.

SEGURO PARA RODAR DE NOVO
========================

Natureza e regra são localizadas por chave natural (código da natureza; CFOP +
âmbito da regra). O que já existe não é tocado, a menos que se passe
``--force``. Nada é apagado.

Uso:
    python manage.py seed_naturezas_viagem --list
    python manage.py seed_naturezas_viagem --filial 3
    python manage.py seed_naturezas_viagem --filial 3 --dry-run
    python manage.py seed_naturezas_viagem --filial 3 --force
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

# --- Valores fiscais (revisar com a contabilidade) --------------------------
# Simples Nacional. Para Lucro Presumido/Real, troque `csosn` por `cst_icms`
# nas regras e ajuste os CST de PIS/COFINS.
CST_PIS = "49"
CST_COFINS = "49"

# `csosn` por operação: 102 nas que são venda de fato, 400 (não tributada pelo
# Simples) nas remessas / bonificação / retorno.
CSOSN_VENDA = "102"
CSOSN_NAO_TRIBUTADA = "400"

# --- As 5 naturezas e suas regras ------------------------------------------
# `interestadual=False` -> regra sem UF (padrão / dentro do estado)
# `interestadual=True`  -> regra com "somente interestadual" marcado
NATUREZAS = [
    {
        "codigo": "venda",
        "descricao": "Venda",
        "especie": "venda",
        "movimenta_estoque": True,
        "gera_financeiro": True,
        "exige_destinatario": True,
        "entra_no_mdfe": True,
        "regras": [
            {"cfop": "5101", "interestadual": False, "csosn": CSOSN_VENDA},
            {"cfop": "6101", "interestadual": True, "csosn": CSOSN_VENDA},
        ],
    },
    {
        "codigo": "remessa_venda_fora",
        "descricao": "Remessa para venda fora do estabelecimento",
        "especie": "remessa_venda_fora",
        "movimenta_estoque": True,
        "gera_financeiro": False,
        "exige_destinatario": False,
        "entra_no_mdfe": True,
        "regras": [
            {"cfop": "5904", "interestadual": False, "csosn": CSOSN_NAO_TRIBUTADA},
            {"cfop": "6904", "interestadual": True, "csosn": CSOSN_NAO_TRIBUTADA},
        ],
    },
    {
        "codigo": "venda_fora",
        "descricao": "Venda fora do estabelecimento",
        "especie": "venda_fora",
        "movimenta_estoque": False,
        "gera_financeiro": True,
        "exige_destinatario": True,
        "entra_no_mdfe": True,
        "regras": [
            {"cfop": "5103", "interestadual": False, "csosn": CSOSN_VENDA},
            {"cfop": "6103", "interestadual": True, "csosn": CSOSN_VENDA},
        ],
    },
    {
        "codigo": "bonificacao",
        "descricao": "Bonificação",
        "especie": "bonificacao",
        "movimenta_estoque": True,
        "gera_financeiro": False,
        "exige_destinatario": True,
        "entra_no_mdfe": True,
        "regras": [
            {"cfop": "5910", "interestadual": False, "csosn": CSOSN_NAO_TRIBUTADA},
            {"cfop": "6910", "interestadual": True, "csosn": CSOSN_NAO_TRIBUTADA},
        ],
    },
    {
        "codigo": "retorno_venda_fora",
        "descricao": "Retorno de venda fora do estabelecimento",
        "especie": "retorno_venda_fora",
        "movimenta_estoque": False,
        "gera_financeiro": False,
        "exige_destinatario": False,
        "entra_no_mdfe": True,
        # Nota de ENTRADA. 1904 / 2904 = retorno de remessa para venda fora.
        "regras": [
            {"cfop": "1904", "interestadual": False, "csosn": CSOSN_NAO_TRIBUTADA},
            {"cfop": "2904", "interestadual": True, "csosn": CSOSN_NAO_TRIBUTADA},
        ],
    },
]


class Command(BaseCommand):
    help = "Cria as 5 naturezas de operação da viagem (venda fora) com CFOP."

    def add_arguments(self, parser):
        parser.add_argument(
            "--filial", type=int, default=None,
            help="ID da filial onde cadastrar. Use --list para ver os IDs.",
        )
        parser.add_argument(
            "--list", action="store_true",
            help="Lista as filiais e sai, sem cadastrar nada.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Mostra o que faria, sem gravar.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Atualiza natureza/regra já existentes em vez de preservá-las.",
        )

    def handle(self, *args, **options):
        from apps.core.models.empresa import Filial
        from apps.fiscal.models import NaturezaOperacao, RegraNaturezaOperacao

        if options["list"]:
            self.stdout.write("Filiais:")
            for f in Filial.objects.select_related("empresa").order_by("empresa__nome_fantasia", "id"):
                marca = " (matriz)" if f.is_matriz else ""
                self.stdout.write(
                    f"  [{f.id}] {f.empresa.nome_fantasia or f.empresa.razao_social}"
                    f" / {f.nome_fantasia or f.razao_social} — {f.cidade}/{f.uf}{marca}"
                )
            return

        filial_id = options["filial"]
        if not filial_id:
            raise CommandError(
                "Informe --filial <ID> (veja os IDs com --list)."
            )
        try:
            filial = Filial.objects.select_related("empresa").get(pk=filial_id)
        except Filial.DoesNotExist:
            raise CommandError(f"Filial {filial_id} não encontrada.")

        dry = options["dry_run"]
        force = options["force"]
        self.stdout.write(
            f"Filial: [{filial.id}] {filial.nome_fantasia or filial.razao_social} "
            f"— {filial.cidade}/{filial.uf}"
        )
        self.stdout.write(
            self.style.WARNING(
                "CSOSN e CST de PIS/COFINS são sugestão para Simples Nacional — "
                "revise na tela antes de emitir."
            )
        )
        if dry:
            self.stdout.write(self.style.WARNING("--dry-run: nada será gravado.\n"))

        criadas = atualizadas = mantidas = 0
        regras_criadas = regras_atualizadas = regras_mantidas = 0

        with transaction.atomic():
            for spec in NATUREZAS:
                defaults = {
                    "descricao": spec["descricao"],
                    "especie": spec["especie"],
                    "movimenta_estoque": spec["movimenta_estoque"],
                    "gera_financeiro": spec["gera_financeiro"],
                    "exige_destinatario": spec["exige_destinatario"],
                    "entra_no_mdfe": spec["entra_no_mdfe"],
                    "ativo": True,
                }
                natureza = NaturezaOperacao.objects.filter(
                    filial=filial, codigo=spec["codigo"]
                ).first()

                if natureza is None:
                    criadas += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"+ natureza  {spec['codigo']:<20} {spec['descricao']}"
                    ))
                    if not dry:
                        natureza = NaturezaOperacao.objects.create(
                            filial=filial, codigo=spec["codigo"], **defaults
                        )
                elif force:
                    atualizadas += 1
                    self.stdout.write(self.style.WARNING(
                        f"~ natureza  {spec['codigo']:<20} (atualizada)"
                    ))
                    if not dry:
                        for campo, valor in defaults.items():
                            setattr(natureza, campo, valor)
                        natureza.save(update_fields=list(defaults))
                else:
                    mantidas += 1
                    self.stdout.write(
                        f"= natureza  {spec['codigo']:<20} (já existia, preservada)"
                    )

                for regra in spec["regras"]:
                    rdef = {
                        "csosn": regra["csosn"],
                        "cst_pis": CST_PIS,
                        "cst_cofins": CST_COFINS,
                        "finalidade_nfe": 1,
                        "ativo": True,
                    }
                    ambito = "interestadual" if regra["interestadual"] else "no estado"
                    existente = None
                    if natureza is not None and not (dry and natureza.pk is None):
                        existente = RegraNaturezaOperacao.objects.filter(
                            natureza=natureza,
                            cfop=regra["cfop"],
                            somente_interestadual=regra["interestadual"],
                            uf_origem="",
                            uf_destino="",
                        ).first()

                    if existente is None:
                        regras_criadas += 1
                        self.stdout.write(self.style.SUCCESS(
                            f"  + regra   CFOP {regra['cfop']} ({ambito}), CSOSN {regra['csosn']}"
                        ))
                        if not dry and natureza is not None:
                            RegraNaturezaOperacao.objects.create(
                                natureza=natureza,
                                cfop=regra["cfop"],
                                somente_interestadual=regra["interestadual"],
                                uf_origem="",
                                uf_destino="",
                                **rdef,
                            )
                    elif force:
                        regras_atualizadas += 1
                        self.stdout.write(self.style.WARNING(
                            f"  ~ regra   CFOP {regra['cfop']} ({ambito}) (atualizada)"
                        ))
                        if not dry:
                            for campo, valor in rdef.items():
                                setattr(existente, campo, valor)
                            existente.save(update_fields=list(rdef))
                    else:
                        regras_mantidas += 1
                        self.stdout.write(
                            f"  = regra   CFOP {regra['cfop']} ({ambito}) (já existia)"
                        )

            if dry:
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Naturezas: {criadas} criadas, {atualizadas} atualizadas, {mantidas} preservadas. "
            f"Regras: {regras_criadas} criadas, {regras_atualizadas} atualizadas, "
            f"{regras_mantidas} preservadas."
        ))
        if not dry:
            self.stdout.write(
                "Confira em Fiscal > Naturezas de operação e ajuste a tributação "
                "com a contabilidade antes de emitir a primeira remessa."
            )
