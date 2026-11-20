from alembic import op

revision = "0008_provider_presets"
down_revision = "0007_session_plans"
branch_labels = None
depends_on = None


PROVIDER_PRESET_TABLES = ("provider_presets", "provider_presets_meta")


def upgrade() -> None:
    from storage import schema as storage_schema

    storage_schema.metadata.create_all(
        bind=op.get_bind(),
        tables=[storage_schema.metadata.tables[t] for t in PROVIDER_PRESET_TABLES],
    )


def downgrade() -> None:
    from storage import schema as storage_schema

    storage_schema.metadata.drop_all(
        bind=op.get_bind(),
        tables=[storage_schema.metadata.tables[t] for t in PROVIDER_PRESET_TABLES],
    )
