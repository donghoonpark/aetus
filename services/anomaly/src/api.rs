use crate::config::Settings;
use crate::models::{CreateJobRequest, WebhookEndpointRequest};
use crate::repository::Repository;
use crate::worker::run_detection_once;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::json;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;

#[derive(Clone)]
pub struct ApiState {
    repo: Repository,
    admin_token: String,
}

#[derive(Debug, Serialize)]
struct ErrorBody {
    error: String,
}

#[derive(Debug)]
struct ApiError {
    status: StatusCode,
    message: String,
}

impl ApiError {
    fn unauthorized() -> Self {
        Self {
            status: StatusCode::UNAUTHORIZED,
            message: "missing or invalid anomaly admin token".to_string(),
        }
    }

    fn internal(exc: anyhow::Error) -> Self {
        Self {
            status: StatusCode::INTERNAL_SERVER_ERROR,
            message: exc.to_string(),
        }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(ErrorBody {
                error: self.message,
            }),
        )
            .into_response()
    }
}

type ApiResult<T> = Result<Json<T>, ApiError>;

pub fn router(repo: Repository, settings: Settings) -> Router {
    let state = ApiState {
        repo,
        admin_token: settings.admin_token,
    };
    Router::new()
        .route("/v1/healthz", get(healthz))
        .route("/v1/readyz", get(readyz))
        .route("/v1/anomaly/jobs", get(list_jobs).post(create_job))
        .route("/v1/anomaly/jobs/:job_id/run", post(run_job))
        .route("/v1/anomaly/events", get(list_events))
        .route(
            "/v1/anomaly/webhooks/endpoints",
            get(list_webhook_endpoints).post(create_webhook_endpoint),
        )
        .with_state(state)
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
}

async fn healthz() -> Json<serde_json::Value> {
    Json(json!({ "status": "ok" }))
}

async fn readyz(State(state): State<ApiState>) -> Result<Json<serde_json::Value>, ApiError> {
    state.repo.ready().await.map_err(ApiError::internal)?;
    Ok(Json(json!({ "status": "ready" })))
}

fn require_admin(headers: &HeaderMap, state: &ApiState) -> Result<(), ApiError> {
    let Some(raw) = headers.get("x-aetus-admin-token") else {
        return Err(ApiError::unauthorized());
    };
    if raw.to_str().ok() == Some(state.admin_token.as_str()) {
        Ok(())
    } else {
        Err(ApiError::unauthorized())
    }
}

async fn create_job(
    State(state): State<ApiState>,
    headers: HeaderMap,
    Json(request): Json<CreateJobRequest>,
) -> ApiResult<crate::models::JobResponse> {
    require_admin(&headers, &state)?;
    let response = state
        .repo
        .create_job(request)
        .await
        .map_err(ApiError::internal)?;
    Ok(Json(response))
}

async fn list_jobs(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> ApiResult<Vec<crate::models::JobResponse>> {
    require_admin(&headers, &state)?;
    let response = state.repo.list_jobs().await.map_err(ApiError::internal)?;
    Ok(Json(response))
}

async fn run_job(
    State(state): State<ApiState>,
    headers: HeaderMap,
    Path(job_id): Path<i64>,
) -> ApiResult<crate::worker::RunSummary> {
    require_admin(&headers, &state)?;
    let response = run_detection_once(&state.repo, Some(job_id))
        .await
        .map_err(ApiError::internal)?;
    Ok(Json(response))
}

#[derive(Debug, Deserialize)]
struct EventQuery {
    limit: Option<i64>,
}

async fn list_events(
    State(state): State<ApiState>,
    headers: HeaderMap,
    Query(query): Query<EventQuery>,
) -> ApiResult<Vec<crate::models::EventResponse>> {
    require_admin(&headers, &state)?;
    let response = state
        .repo
        .list_events(query.limit.unwrap_or(100).clamp(1, 1000))
        .await
        .map_err(ApiError::internal)?;
    Ok(Json(response))
}

async fn create_webhook_endpoint(
    State(state): State<ApiState>,
    headers: HeaderMap,
    Json(request): Json<WebhookEndpointRequest>,
) -> ApiResult<crate::models::WebhookEndpointResponse> {
    require_admin(&headers, &state)?;
    let response = state
        .repo
        .create_webhook_endpoint(request)
        .await
        .map_err(ApiError::internal)?;
    Ok(Json(response))
}

async fn list_webhook_endpoints(
    State(state): State<ApiState>,
    headers: HeaderMap,
) -> ApiResult<Vec<crate::models::WebhookEndpointResponse>> {
    require_admin(&headers, &state)?;
    let response = state
        .repo
        .list_webhook_endpoints()
        .await
        .map_err(ApiError::internal)?;
    Ok(Json(response))
}
