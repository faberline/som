use clap::{Parser, Subcommand};
use serde_json::{Value, json};
use std::{fs, path::PathBuf};

#[derive(Parser)]
#[command(
    name = "som-service",
    about = "Compile a fixed service contract into a Rust client"
)]
struct Args {
    #[command(subcommand)]
    command: Command,
}
#[derive(Subcommand)]
enum Command {
    Serve {
        #[arg(long, default_value_t = 8767)]
        port: u16,
    },
    Compile {
        source: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8767")]
        url: String,
    },
    Generate {
        prompt: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8767")]
        url: String,
    },
    Assemble {
        bundle: PathBuf,
        #[arg(long, default_value = "http://127.0.0.1:8767")]
        url: String,
    },
    /// Print the source manifest used for the service asset identity.
    Identity,
}
/// Wraps a source file in the compile envelope. JSON is sent as raw text, so
/// duplicate keys are still caught by the server.
fn compile_payload(text: &str) -> String {
    if text.trim_start().starts_with('{') {
        format!(
            "{{\"format\":\"{}\",\"prompt\":{text}}}",
            som_service::model::JSON_FORMAT_V2
        )
    } else {
        json!({"format":som_service::model::CNL_FORMAT,"source":text}).to_string()
    }
}
async fn post(
    url: &str,
    path: &str,
    body: String,
    key: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    let reply = reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .build()?
        .post(format!("{}{path}", url.trim_end_matches('/')))
        .header("content-type", "application/json")
        .body(body)
        .send()
        .await?;
    let status = reply.status();
    let body = reply.bytes().await?;
    let value: Value = serde_json::from_slice(&body)?;
    if !status.is_success() {
        eprintln!("{}", serde_json::to_string_pretty(&value)?);
        return Err(format!("Service returned HTTP {status}.").into());
    }
    println!("{}", serde_json::to_string_pretty(&value[key])?);
    Ok(())
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    match Args::parse().command {
        Command::Serve { port } => {
            let listener =
                tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, port)).await?;
            eprintln!("som-service listening on {}", listener.local_addr()?);
            axum::serve(listener, som_service::server::router()).await?;
        }
        Command::Compile { source, url } => {
            post(
                &url,
                "/compile",
                compile_payload(&fs::read_to_string(source)?),
                "bundle",
            )
            .await?
        }
        Command::Generate { prompt, url } => {
            let raw = fs::read_to_string(prompt)?;
            // Preserve duplicate-key checks: send the typed JSON without decoding/re-encoding it.
            let payload = format!(
                "{{\"format\":\"{}\",\"prompt\":{raw}}}",
                som_service::model::JSON_FORMAT
            );
            let reply = reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()?
                .post(format!("{}/compile", url.trim_end_matches('/')))
                .header("content-type", "application/json")
                .body(payload)
                .send()
                .await?;
            let status = reply.status();
            let value: Value = reply.json().await?;
            if !status.is_success() {
                eprintln!("{}", serde_json::to_string_pretty(&value)?);
                return Err(format!("Service returned HTTP {status}.").into());
            }
            println!("{}", serde_json::to_string_pretty(&value["bundle"])?);
        }
        Command::Assemble { bundle, url } => {
            let raw = fs::read_to_string(bundle)?;
            let reply = reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .build()?
                .post(format!("{}/assemble", url.trim_end_matches('/')))
                .header("content-type", "application/json")
                .body(format!("{{\"bundle\":{raw}}}"))
                .send()
                .await?;
            let status = reply.status();
            let value: Value = reply.json().await?;
            if !status.is_success() {
                eprintln!("{}", serde_json::to_string_pretty(&value)?);
                return Err(format!("Service returned HTTP {status}.").into());
            }
            println!("{}", serde_json::to_string_pretty(&value["files"])?);
        }
        Command::Identity => println!(
            "{}",
            serde_json::to_string_pretty(
                &json!({"assets":som_service::bundle::identity(),"manifest":som_service::bundle::source_manifest()})
            )?
        ),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::compile_payload;
    #[test]
    fn json_file_is_sent_as_prompt_json_v2_and_other_text_as_cnl() {
        assert_eq!(
            compile_payload("  {\"a\":1}"),
            "{\"format\":\"som.prompt-json.service.v2\",\"prompt\":  {\"a\":1}}"
        );
        let cnl = compile_payload("Package x.");
        assert_eq!(
            cnl,
            "{\"format\":\"som.cnl.service.v1\",\"source\":\"Package x.\"}"
        );
    }
}
