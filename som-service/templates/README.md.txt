# {{PACKAGE}}

This Rust library reads task status from Defer.
It uses `defer.http.task_status.v1` at commit
`5f7adac1fe1333b33e3ec69a92b5ca4a634a97d0`.

```rust,no_run
use {{PACKAGE}}::{ClientError, DeferClient, TaskStatusResponse};

async fn read(base_url: &str, bearer: Option<&str>) -> Result<TaskStatusResponse, ClientError> {
    let client = DeferClient::new(base_url, bearer)?;
    client.{{ALIAS}}("jobs", "task-1").await
}
```

Run the method in a Tokio runtime. Supply the origin and optional bearer token at
runtime. The origin must use HTTP or HTTPS. A root slash is allowed. Credentials,
path prefixes, queries and fragments are refused. Redirects are not followed.
Each request has a 30 second timeout and sends no body.

The arguments `{{QUEUE_ARGUMENT}}` and `{{TASK_ARGUMENT}}` each form one encoded
path segment. Empty strings, `.` and `..` are refused. Other characters, including
slashes, percent signs and Unicode, are encoded without changing their value.

The result has `task_id` and `status` fields. Status is Scheduled, Leased,
Succeeded, DeadLettered or Canceled. Leased includes `attempt_id`, `executor_node`,
`epoch` and `expires_at`. The last field is a UTC timestamp.

`ClientError::Http` preserves the non-200 status, raw body and optional native
error envelope. `ClientError::InvalidResponse` preserves a malformed 200 body.
Configuration, input and network failures have separate variants. Debug and
Display output omit credentials and response bodies. Read raw error fields only
when needed; they can contain private server data.
Network failures retain any received HTTP status and body prefix.

Use the checked-in lock file for repeat builds:

```sh
cargo build --locked
```

Once these locked dependencies are cached, `cargo build --locked --offline`
builds without a network connection.
