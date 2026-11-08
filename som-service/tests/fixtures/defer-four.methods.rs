    pub async fn read_task_status(
        &self,
        queue: &str,
        task_id: &str,
    ) -> Result<TaskStatusResponse, ClientError> {
        self.call_json(
            reqwest::Method::GET,
            200,
            &[("queue", queue), ("task_id", task_id)],
            &["v1", "queues", queue, "tasks", task_id],
            None,
        )
        .await
    }

    pub async fn create_task(
        &self,
        queue: &str,
        task: &CreateTask,
    ) -> Result<(), ClientError> {
        self.call_empty(
            reqwest::Method::POST,
            201,
            &[("queue", queue)],
            &["v1", "queues", queue, "tasks"],
            Some(serde_json::to_vec(task).map_err(|_| ClientError::InvalidInput { parameter: "task", reason: "The body cannot be serialized." })?),
        )
        .await
    }

    pub async fn put_queue(
        &self,
        queue: &str,
        policy: &QueuePolicy,
    ) -> Result<QueueSnapshot, ClientError> {
        self.call_json(
            reqwest::Method::PUT,
            200,
            &[("queue", queue)],
            &["v1", "queues", queue],
            Some(serde_json::to_vec(policy).map_err(|_| ClientError::InvalidInput { parameter: "policy", reason: "The body cannot be serialized." })?),
        )
        .await
    }

    pub async fn cancel_task(
        &self,
        queue: &str,
        task_id: &str,
    ) -> Result<(), ClientError> {
        self.call_empty(
            reqwest::Method::DELETE,
            204,
            &[("queue", queue), ("task_id", task_id)],
            &["v1", "queues", queue, "tasks", task_id],
            None,
        )
        .await
    }