//! Eén sessie per inlog (waarde van de Authorization-header): een API-client plus een korte cache van ruimtes en
//! mapinhoud, zodat een PROPFIND met diepte 1 niet voor elk item opnieuw de server vraagt.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use openaec_sync::api::{Inhoud, Ruimte};
use openaec_sync::{Client, Fout};

const INHOUD_GELDIG: Duration = Duration::from_secs(3);
const RUIMTES_GELDIG: Duration = Duration::from_secs(30);
const SESSIE_GELDIG: Duration = Duration::from_secs(3600);
/// Zo lang onthoudt de gateway een verwijderd bestand voor een volgende MOVE/COPY naar hetzelfde pad.
const NET_VERWIJDERD: Duration = Duration::from_secs(10);

pub struct Sessie {
    pub client: Client,
    pub http: reqwest::Client,
    ruimtes: Mutex<Option<(Instant, Vec<Ruimte>)>>,
    inhoud: Mutex<HashMap<String, (Instant, Inhoud)>>,
    net_verwijderd: Mutex<HashMap<String, (Instant, String)>>,
    laatst: Mutex<Instant>,
}

fn sleutel(ruimte: &str, map: Option<&str>) -> String {
    format!("{ruimte}|{}", map.unwrap_or(""))
}

impl Sessie {
    pub fn new(client: Client) -> Self {
        Self {
            client,
            http: reqwest::Client::new(),
            ruimtes: Mutex::new(None),
            inhoud: Mutex::new(HashMap::new()),
            net_verwijderd: Mutex::new(HashMap::new()),
            laatst: Mutex::new(Instant::now()),
        }
    }

    pub async fn ruimtes(&self) -> Result<Vec<Ruimte>, Fout> {
        if let Some((t, r)) = self.ruimtes.lock().unwrap().as_ref() {
            if t.elapsed() < RUIMTES_GELDIG {
                return Ok(r.clone());
            }
        }
        let r = self.client.ruimtes().await?;
        *self.ruimtes.lock().unwrap() = Some((Instant::now(), r.clone()));
        Ok(r)
    }

    pub async fn inhoud(&self, ruimte: &str, map: Option<&str>) -> Result<Inhoud, Fout> {
        let k = sleutel(ruimte, map);
        if let Some((t, i)) = self.inhoud.lock().unwrap().get(&k) {
            if t.elapsed() < INHOUD_GELDIG {
                return Ok(i.clone());
            }
        }
        let i = self.client.bladeren(ruimte, map).await?;
        self.inhoud.lock().unwrap().insert(k, (Instant::now(), i.clone()));
        Ok(i)
    }

    /// WebDAV verwijdert bij MOVE/COPY met overschrijven eerst het doel. De gateway onthoudt dat kort, zodat het
    /// doel daarna hersteld kan worden en de nieuwe inhoud een versie van hetzelfde bestand wordt.
    pub fn onthoud_verwijderd(&self, pad: &str, bestand: &str) {
        let mut m = self.net_verwijderd.lock().unwrap();
        m.retain(|_, (t, _)| t.elapsed() < NET_VERWIJDERD);
        m.insert(pad.to_lowercase(), (Instant::now(), bestand.to_string()));
    }

    pub fn neem_verwijderd(&self, pad: &str) -> Option<String> {
        let (t, id) = self.net_verwijderd.lock().unwrap().remove(&pad.to_lowercase())?;
        (t.elapsed() < NET_VERWIJDERD).then_some(id)
    }

    /// Na een wijziging: de cache van de hele ruimte vergeten (verplaatsen raakt twee mappen).
    pub fn vergeet(&self, ruimte: &str) {
        let prefix = format!("{ruimte}|");
        self.inhoud.lock().unwrap().retain(|k, _| !k.starts_with(&prefix));
    }
}

/// Alle actieve sessies; ongebruikte vervallen na een uur.
#[derive(Default)]
pub struct Sessies(Mutex<HashMap<String, Arc<Sessie>>>);

impl Sessies {
    /// Sessie voor deze autorisatie; een nieuwe wordt eerst bij de server gecontroleerd.
    pub async fn voor(&self, basis: &str, autorisatie: &str) -> Result<Arc<Sessie>, Fout> {
        if let Some(s) = self.0.lock().unwrap().get(autorisatie).cloned() {
            *s.laatst.lock().unwrap() = Instant::now();
            return Ok(s);
        }
        let sessie = Arc::new(Sessie::new(Client::new(basis, autorisatie)));
        sessie.ruimtes().await?;
        let mut alle = self.0.lock().unwrap();
        alle.retain(|_, s| s.laatst.lock().unwrap().elapsed() < SESSIE_GELDIG);
        alle.insert(autorisatie.to_string(), sessie.clone());
        Ok(sessie)
    }
}
