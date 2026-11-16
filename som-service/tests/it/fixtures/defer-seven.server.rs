    fn read_task_status(
        &self,
        queue: String,
        task_id: String,
    ) -> impl Future<Output = Result<TaskStatusResponse, ServerError>> + Send;

    fn create_task(
        &self,
        queue: String,
        task: CreateTask,
    ) -> impl Future<Output = Result<(), ServerError>> + Send;

    fn put_queue(
        &self,
        queue: String,
        policy: QueuePolicy,
    ) -> impl Future<Output = Result<QueueSnapshot, ServerError>> + Send;

    fn cancel_task(
        &self,
        queue: String,
        task_id: String,
    ) -> impl Future<Output = Result<(), ServerError>> + Send;

    fn list_queues(
        &self,
    ) -> impl Future<Output = Result<QueueListResponse, ServerError>> + Send;

    fn get_queue(
        &self,
        queue: String,
    ) -> impl Future<Output = Result<QueueSnapshot, ServerError>> + Send;

    fn list_tasks(
        &self,
        queue: String,
    ) -> impl Future<Output = Result<TaskListResponse, ServerError>> + Send;
//---
        .route("/v1/queues/{queue}/tasks/{task_id}", axum::routing::get(handle_read_task_status::<H>))
        .route("/v1/queues/{queue}/tasks", axum::routing::post(handle_create_task::<H>))
        .route("/v1/queues/{queue}", axum::routing::put(handle_put_queue::<H>))
        .route("/v1/queues/{queue}/tasks/{task_id}", axum::routing::delete(handle_cancel_task::<H>))
        .route("/v1/queues", axum::routing::get(handle_list_queues::<H>))
        .route("/v1/queues/{queue}", axum::routing::get(handle_get_queue::<H>))
        .route("/v1/queues/{queue}/tasks", axum::routing::get(handle_list_tasks::<H>))
//---
async fn handle_read_task_status<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
    axum::extract::Path(__path): axum::extract::Path<(String, String)>,
) -> Result<axum::response::Response, ServerError> {
    let (queue, task_id) = __path;
    let __reply = __handler.read_task_status(queue, task_id).await?;
    Ok(axum::Json(__reply).into_response())
}

async fn handle_create_task<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
    axum::extract::Path(__path): axum::extract::Path<String>,
    __bytes: axum::body::Bytes,
) -> Result<axum::response::Response, ServerError> {
    let queue = __path;
    let task: CreateTask = serde_json::from_slice(&__bytes).map_err(|_| ServerError::new(400, "invalid_request", "The request body is not valid for this operation."))?;
    __handler.create_task(queue, task).await?;
    Ok(axum::http::StatusCode::CREATED.into_response())
}

async fn handle_put_queue<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
    axum::extract::Path(__path): axum::extract::Path<String>,
    __bytes: axum::body::Bytes,
) -> Result<axum::response::Response, ServerError> {
    let queue = __path;
    let policy: QueuePolicy = serde_json::from_slice(&__bytes).map_err(|_| ServerError::new(400, "invalid_request", "The request body is not valid for this operation."))?;
    let __reply = __handler.put_queue(queue, policy).await?;
    Ok(axum::Json(__reply).into_response())
}

async fn handle_cancel_task<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
    axum::extract::Path(__path): axum::extract::Path<(String, String)>,
) -> Result<axum::response::Response, ServerError> {
    let (queue, task_id) = __path;
    __handler.cancel_task(queue, task_id).await?;
    Ok(axum::http::StatusCode::NO_CONTENT.into_response())
}

async fn handle_list_queues<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
) -> Result<axum::response::Response, ServerError> {
    let __reply = __handler.list_queues().await?;
    Ok(axum::Json(__reply).into_response())
}

async fn handle_get_queue<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
    axum::extract::Path(__path): axum::extract::Path<String>,
) -> Result<axum::response::Response, ServerError> {
    let queue = __path;
    let __reply = __handler.get_queue(queue).await?;
    Ok(axum::Json(__reply).into_response())
}

async fn handle_list_tasks<H: Handler>(
    axum::extract::State(__handler): axum::extract::State<Arc<H>>,
    axum::extract::Path(__path): axum::extract::Path<String>,
) -> Result<axum::response::Response, ServerError> {
    let queue = __path;
    let __reply = __handler.list_tasks(queue).await?;
    Ok(axum::Json(__reply).into_response())
}
