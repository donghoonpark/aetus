mod api;
mod config;
mod detectors;
mod dispatcher;
mod models;
mod repository;
mod webhook;
mod worker;

use clap::{Parser, Subcommand};
use config::Settings;
use repository::Repository;
use tracing::info;
use tracing_subscriber::EnvFilter;

#[derive(Debug, Parser)]
#[command(name = "aetus-anomaly")]
#[command(about = "AETUS anomaly detection API, worker, and webhook dispatcher")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Api,
    Worker,
    Dispatcher,
    RunOnce {
        #[arg(long)]
        job_id: Option<i64>,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env().add_directive("aetus_anomaly=info".parse()?))
        .init();

    let cli = Cli::parse();
    let settings = Settings::from_env()?;
    let repo = Repository::connect(&settings.postgres_dsn).await?;

    match cli.command {
        Command::Api => {
            let bind_addr = settings.bind_addr;
            let app = api::router(repo, settings);
            let listener = tokio::net::TcpListener::bind(bind_addr).await?;
            info!(%bind_addr, "starting anomaly API");
            axum::serve(listener, app).await?;
        }
        Command::Worker => {
            info!(worker_id = %settings.worker_id, "starting anomaly worker");
            worker::run_worker_loop(repo, settings.poll_interval).await?;
        }
        Command::Dispatcher => {
            info!(
                max_attempts = settings.webhook_max_attempts,
                "starting anomaly webhook dispatcher"
            );
            dispatcher::run_dispatcher_loop(repo, settings.poll_interval).await?;
        }
        Command::RunOnce { job_id } => {
            let summary = worker::run_detection_once(&repo, job_id).await?;
            println!("{}", serde_json::to_string_pretty(&summary)?);
        }
    }
    Ok(())
}
