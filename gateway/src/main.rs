//! WebDAV-toegang tot OpenAEC Storage (ontwerp §7): koppel een ruimte als netwerkschijf of open bestanden vanuit
//! programma's die WebDAV spreken.
//!
//! Instellingen (omgeving):
//! - `OPENAEC_BASIS`: adres van de klantomgeving, bijv. `https://bedrijf.cloud.open-aec.eu`
//! - `OPENAEC_DAV_ADRES`: luisteradres, standaard `0.0.0.0:8310`
//!
//! Inloggen met Basic-auth: gebruikersnaam = API-sleutel, wachtwoord = API-geheim (later: app-wachtwoord uit de
//! beheeromgeving). Een `Authorization: Bearer …` wordt ongewijzigd doorgegeven.

mod fs;
mod sessie;
mod tijd;

use std::convert::Infallible;
use std::sync::Arc;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use dav_server::body::Body;
use dav_server::memls::MemLs;
use dav_server::DavHandler;
use http::{Request, Response, StatusCode};
use hyper::body::Incoming;
use hyper::server::conn::http1;
use hyper::service::service_fn;
use hyper_util::rt::TokioIo;
use tokio::net::TcpListener;

use fs::{Inlog, OpslagFs};
use sessie::Sessies;

const UITDAGING: &str = "Basic realm=\"OpenAEC Storage\", charset=\"UTF-8\"";

/// Waarde voor de Storage-API op basis van de Authorization-header van de WebDAV-client.
pub fn autorisatie(header: Option<&str>) -> Option<String> {
    let h = header?.trim();
    if let Some(b) = h.strip_prefix("Bearer ") {
        return Some(format!("Bearer {}", b.trim()));
    }
    let gedecodeerd = STANDARD.decode(h.strip_prefix("Basic ")?.trim()).ok()?;
    let tekst = String::from_utf8(gedecodeerd).ok()?;
    let (sleutel, geheim) = tekst.split_once(':')?;
    (!sleutel.is_empty() && !geheim.is_empty()).then(|| format!("token {sleutel}:{geheim}"))
}

fn weiger() -> Response<Body> {
    Response::builder()
        .status(StatusCode::UNAUTHORIZED)
        .header("WWW-Authenticate", UITDAGING)
        .body(Body::from("Inloggen vereist"))
        .unwrap()
}

async fn behandel(
    verzoek: Request<Incoming>,
    dav: DavHandler<Inlog>,
    sessies: Arc<Sessies>,
    basis: Arc<String>,
) -> Result<Response<Body>, Infallible> {
    let header = verzoek.headers().get("authorization").and_then(|w| w.to_str().ok());
    let Some(auth) = autorisatie(header) else { return Ok(weiger()) };
    let sessie = match sessies.voor(&basis, &auth).await {
        Ok(s) => s,
        Err(fout) => {
            tracing::info!("inloggen geweigerd: {fout}");
            return Ok(weiger());
        }
    };
    Ok(dav.handle_guarded(verzoek, "/principals/gebruiker".into(), sessie).await)
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();
    let basis = Arc::new(
        std::env::var("OPENAEC_BASIS").expect("OPENAEC_BASIS ontbreekt").trim_end_matches('/').to_string(),
    );
    let adres = std::env::var("OPENAEC_DAV_ADRES").unwrap_or_else(|_| "0.0.0.0:8310".into());
    let dav: DavHandler<Inlog> = DavHandler::builder()
        .filesystem(Box::new(OpslagFs))
        .locksystem(MemLs::new())
        .build_handler();
    let sessies = Arc::new(Sessies::default());
    let luisteraar = TcpListener::bind(&adres).await.expect("kan niet luisteren");
    tracing::info!("OpenAEC Storage WebDAV op {adres}, omgeving {basis}");
    loop {
        let Ok((verbinding, _)) = luisteraar.accept().await else { continue };
        let (dav, sessies, basis) = (dav.clone(), sessies.clone(), basis.clone());
        tokio::spawn(async move {
            let dienst = service_fn(move |v| behandel(v, dav.clone(), sessies.clone(), basis.clone()));
            if let Err(fout) = http1::Builder::new().serve_connection(TokioIo::new(verbinding), dienst).await {
                tracing::debug!("verbinding: {fout}");
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn basic_wordt_api_sleutel_en_bearer_blijft() {
        let basic = format!("Basic {}", STANDARD.encode("sleutel:geheim"));
        assert_eq!(autorisatie(Some(&basic)).as_deref(), Some("token sleutel:geheim"));
        assert_eq!(autorisatie(Some("Bearer abc")).as_deref(), Some("Bearer abc"));
        assert_eq!(autorisatie(Some(&format!("Basic {}", STANDARD.encode("zonder-geheim:")))), None);
        assert_eq!(autorisatie(None), None);
    }
}
