    async def export(self, parent_id: int) -> WorkflowReceipt:
        """Use the selected workflow and its row bound."""
        return workflow_payload(await workflow_rows(parent_id, self.store))

    async def batch(self, selected: Effect, parent_id: int, children: list[ChildInputData]) -> list[ChildRow]:
        """Journal each row under the selected batch inverse."""
        return await self.store.batch(parent_id, children, (selected, self.record.effects))

    async def batch_create(self, effect: Effect, parent: ParentInputData, children: list[ChildInputData]) -> BatchRead:
        """Commit the declared parent and children in their outer transaction."""
        if not 1 <= len(children) <= 20: raise HTTPException(status_code=422, detail="expected 1..20 children")
        request = CrudRequest(role="parent", operation="create", data=model_json(parent))
        row = ParentRow.model_validate(await self.resource(request, effect))
        rows = await self.batch(effect, row.id, children)
        return BatchRead(parent=row, children=rows)

    async def put(self, effect: Effect, key: str, source: str) -> FlowObject:
        """Persist intent before the provider receives this write."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", key): raise HTTPException(status_code=422, detail="invalid object key")
        try:
            body = base64.b64decode(source, validate=True)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid base64 body") from error
        check_flow_body(body)
        effect.group = "external_"+effect.step
        effect.key = key
        effect.uncertain = True
        self.record.effects.append(effect)
        await self.persist()
        try:
            receipt = await run_in_threadpool(flow_object_put, key, body, self.record.run_id, effect.step)
        except ObjectExists:
            try:
                version = await run_in_threadpool(flow_object_version, key, self.record.run_id, effect.step)
            except ObjectUnavailable:
                pass
            else:
                effect.version, effect.owned, effect.uncertain = version, version is not None, False
            await self.persist()
            raise
        effect.owned, effect.version, effect.uncertain = receipt.owned, receipt.version, False
        self.record.values[effect.step] = model_json(receipt)
        await self.persist()
        return receipt
