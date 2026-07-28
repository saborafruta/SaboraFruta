from django.db import models
from django.db.models.lookups import IContains


class AccentInsensitiveIContains(IContains):
    """Faz buscas textuais ignorarem acentos no PostgreSQL."""

    lookup_name = 'icontains'

    def as_sql(self, compiler, connection):
        if connection.vendor != 'postgresql':
            return super().as_sql(compiler, connection)

        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return (
            f'UNACCENT({lhs}) ILIKE UNACCENT({rhs})',
            [*lhs_params, *rhs_params],
        )


models.CharField.register_lookup(AccentInsensitiveIContains)
models.TextField.register_lookup(AccentInsensitiveIContains)
