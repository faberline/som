# pylint: disable-next=too-few-public-methods
class _SomRun(Base):
    """Store the owner-bound journal independently of its lease."""
    __tablename__ = "_som_flow_runs"
    __table_args__ = (sa.UniqueConstraint("owner", "flow", "key"),)
    run_id: Mapped[str] = mapped_column(sa.String(32), primary_key=True)
    owner: Mapped[str] = mapped_column(sa.String(255))
    flow: Mapped[str] = mapped_column(sa.String(80))
    key: Mapped[str] = mapped_column(sa.String(128))
    request_hash: Mapped[str] = mapped_column(sa.String(64))
    epoch: Mapped[int]
    record: Mapped[JsonObject] = mapped_column(JSONB)


# pylint: disable-next=too-few-public-methods
class _SomLease(Base):
    """Fence execution without changing the journal during heartbeat."""
    __tablename__ = "_som_flow_leases"
    run_id: Mapped[str] = mapped_column(sa.ForeignKey("_som_flow_runs.run_id", ondelete="CASCADE"), primary_key=True)
    epoch: Mapped[int]
    until: Mapped[float]
