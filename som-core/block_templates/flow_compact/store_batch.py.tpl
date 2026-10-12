    async def batch(self, parent_id: int, children: list[ChildInputData], log: tuple[Effect, list[Effect]] | None = None) -> list[ChildRow]:
        """Share child creation and preserve the selected compensation log."""
        rows: list[ChildRow] = []
        for data in children:
            effect = log[0].model_copy(deep=True) if log else None
            if effect is not None:
                effect.role = "child"
            request = CrudRequest(role="child", operation="create", parent_id=parent_id, data=json_object(data.model_dump(mode="json")))
            rows.append(ChildRow.model_validate(await self.mutate(request, effect)))
            if effect is not None and effect.owned and log is not None:
                log[1].append(effect)
        return rows
