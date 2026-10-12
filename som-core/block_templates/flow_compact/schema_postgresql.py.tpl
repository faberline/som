SCHEMA = sa.MetaData()


def resource_table(
    name: str,
    columns: tuple[SchemaItem, ...],
    filters: tuple[str | None, ...],
    foreign_key: str | None = None,
    target: str = "",
) -> sa.Table:
    """Freeze one resource and its managed columns in schema version 1."""
    table = sa.Table(
        name,
        SCHEMA,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("_som_revision", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=database_now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=database_now()),
        *columns,
    )
    prefix: list[str] = []
    if foreign_key:
        table.append_column(sa.Column(foreign_key, sa.Integer, sa.ForeignKey(target, ondelete="RESTRICT"), nullable=False))
        prefix.append(foreign_key)
        sa.Index("ix_" + name + "_parent_id", *[table.c[key] for key in [*prefix, "id"]])
    for field in filters:
        if field:
            index = "ix_" + hashlib.sha256((name + ":" + field).encode()).hexdigest()[:24]
            sa.Index(index, *[table.c[key] for key in [*prefix, field, "id"]])
    return table
