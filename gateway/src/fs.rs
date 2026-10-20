//! OpenAEC Storage als WebDAV-bestandssysteem: `/<ruimte>/<map>/…/<bestand>`. Elke handeling gaat via de
//! Storage-API met de inlog van de gebruiker; de gateway heeft zelf geen opslag of rechten.

use std::fmt;
use std::io::{Read, Seek, SeekFrom, Write};
use std::sync::Arc;
use std::time::SystemTime;

use bytes::Bytes;
use dav_server::davpath::DavPath;
use dav_server::fs::{
    DavDirEntry, DavFile, DavMetaData, FsError, FsFuture, FsResult, FsStream, GuardedFileSystem, OpenOptions,
    ReadDirMeta,
};
use futures_util::{stream, FutureExt};
use openaec_sync::api::{BestandItem, MapItem, Ruimte};
use openaec_sync::{download_bestand, upload_bestand, Fout};

use crate::sessie::Sessie;
use crate::tijd;

pub type Inlog = Arc<Sessie>;

/// Zoveel wordt per keer van de downloadlink gelezen; WebDAV-clients lezen in kleine stukken.
const LEESBLOK: u64 = 4 * 1024 * 1024;

fn fs_fout(f: Fout) -> FsError {
    match f {
        Fout::Server { status: 401 | 403, .. } => FsError::Forbidden,
        Fout::Server { status: 404, .. } => FsError::NotFound,
        Fout::Server { status: 417, tekst } if tekst.contains("quotum") || tekst.contains("Quotum") => {
            FsError::InsufficientStorage
        }
        Fout::Server { status: 417, .. } => FsError::Forbidden,
        _ => FsError::GeneralFailure,
    }
}

fn segmenten(pad: &DavPath) -> Vec<String> {
    String::from_utf8_lossy(pad.as_bytes()).split('/').filter(|s| !s.is_empty()).map(str::to_string).collect()
}

fn padsleutel(pad: &DavPath) -> String {
    segmenten(pad).join("/")
}

/// Is het doel net (als onderdeel van deze MOVE/COPY) verwijderd? Dan eerst herstellen.
async fn herstel_doel(s: &Sessie, naar: &DavPath) -> FsResult<bool> {
    let Some(id) = s.neem_verwijderd(&padsleutel(naar)) else { return Ok(false) };
    s.client.herstel(&id).await.map_err(fs_fout)?;
    Ok(true)
}

/// Exacte naam eerst, anders hoofdletterongevoelig (Windows).
fn kies<'a, T>(items: &'a [T], naam: &str, van: impl Fn(&T) -> &str) -> Option<&'a T> {
    items.iter().find(|i| van(i) == naam).or_else(|| items.iter().find(|i| van(i).eq_ignore_ascii_case(naam)))
}

#[derive(Debug, Clone)]
enum Knoop {
    Wortel,
    Ruimte(Ruimte),
    Map { ruimte: String, map: MapItem },
    Bestand { ruimte: String, bestand: BestandItem },
}

impl Knoop {
    fn meta(&self) -> Meta {
        match self {
            Knoop::Wortel | Knoop::Ruimte(_) => Meta::map(None),
            Knoop::Map { map, .. } => Meta::map(map.modified.as_deref()),
            Knoop::Bestand { bestand, .. } => Meta {
                grootte: bestand.grootte,
                gewijzigd: bestand.modified.as_deref().and_then(tijd::lees).unwrap_or(SystemTime::UNIX_EPOCH),
                map: false,
                etag: Some(format!("{}-{}", bestand.name, bestand.huidige_versie)),
            },
        }
    }

    /// Ruimte en map-id als dit een plek is waar bestanden in kunnen.
    fn plek(&self) -> Option<(String, Option<String>)> {
        match self {
            Knoop::Ruimte(r) => Some((r.name.clone(), None)),
            Knoop::Map { ruimte, map } => Some((ruimte.clone(), Some(map.name.clone()))),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
struct Meta {
    grootte: u64,
    gewijzigd: SystemTime,
    map: bool,
    etag: Option<String>,
}

impl Meta {
    fn map(gewijzigd: Option<&str>) -> Self {
        Meta {
            grootte: 0,
            gewijzigd: gewijzigd.and_then(tijd::lees).unwrap_or_else(SystemTime::now),
            map: true,
            etag: None,
        }
    }
}

impl DavMetaData for Meta {
    fn len(&self) -> u64 {
        self.grootte
    }
    fn modified(&self) -> FsResult<SystemTime> {
        Ok(self.gewijzigd)
    }
    fn is_dir(&self) -> bool {
        self.map
    }
    fn etag(&self) -> Option<String> {
        self.etag.clone().or_else(|| {
            let t = self.gewijzigd.duration_since(SystemTime::UNIX_EPOCH).ok()?;
            Some(format!("{:x}", t.as_micros()))
        })
    }
}

struct Item {
    naam: String,
    meta: Meta,
}

impl DavDirEntry for Item {
    fn name(&self) -> Vec<u8> {
        self.naam.as_bytes().to_vec()
    }
    fn metadata(&self) -> FsFuture<'_, Box<dyn DavMetaData>> {
        let m = self.meta.clone();
        async move { Ok(Box::new(m) as Box<dyn DavMetaData>) }.boxed()
    }
}

async fn zoek(s: &Sessie, delen: &[String]) -> FsResult<Knoop> {
    let Some((eerste, rest)) = delen.split_first() else { return Ok(Knoop::Wortel) };
    let ruimtes = s.ruimtes().await.map_err(fs_fout)?;
    let ruimte = kies(&ruimtes, eerste, |r| &r.naam).ok_or(FsError::NotFound)?.clone();
    let mut knoop = Knoop::Ruimte(ruimte.clone());
    let mut map: Option<String> = None;
    for (i, deel) in rest.iter().enumerate() {
        let inhoud = s.inhoud(&ruimte.name, map.as_deref()).await.map_err(fs_fout)?;
        if let Some(m) = kies(&inhoud.mappen, deel, |m| &m.naam) {
            map = Some(m.name.clone());
            knoop = Knoop::Map { ruimte: ruimte.name.clone(), map: m.clone() };
        } else if let Some(b) = kies(&inhoud.bestanden, deel, |b| &b.naam) {
            if i != rest.len() - 1 {
                return Err(FsError::NotFound);
            }
            knoop = Knoop::Bestand { ruimte: ruimte.name.clone(), bestand: b.clone() };
        } else {
            return Err(FsError::NotFound);
        }
    }
    Ok(knoop)
}

/// Bovenliggende plek en de laatste naam van een pad.
async fn ouder(s: &Sessie, pad: &DavPath) -> FsResult<(String, Option<String>, String)> {
    let mut delen = segmenten(pad);
    let naam = delen.pop().ok_or(FsError::Forbidden)?;
    let (ruimte, map) = zoek(s, &delen).await?.plek().ok_or(FsError::Forbidden)?;
    Ok((ruimte, map, naam))
}

#[derive(Clone, Default)]
pub struct OpslagFs;

impl GuardedFileSystem<Inlog> for OpslagFs {
    fn open<'a>(&'a self, pad: &'a DavPath, opties: OpenOptions, s: &'a Inlog) -> FsFuture<'a, Box<dyn DavFile>> {
        async move {
            let bestaand = match zoek(s, &segmenten(pad)).await {
                Ok(Knoop::Bestand { bestand, .. }) => Some(bestand),
                Ok(_) => return Err(FsError::Forbidden),
                Err(FsError::NotFound) => None,
                Err(e) => return Err(e),
            };
            if !opties.write {
                let b = bestaand.ok_or(FsError::NotFound)?;
                return Ok(Box::new(Lezer::new(s.clone(), b)) as Box<dyn DavFile>);
            }
            if bestaand.is_some() && opties.create_new {
                return Err(FsError::Exists);
            }
            if bestaand.is_none() && !opties.create && !opties.create_new {
                return Err(FsError::NotFound);
            }
            let (ruimte, map, naam) = ouder(s, pad).await?;
            let tijdelijk = tempfile::NamedTempFile::new().map_err(|_| FsError::GeneralFailure)?;
            if let Some(b) = &bestaand {
                if !opties.truncate && b.grootte > 0 {
                    download_bestand(&s.client, &b.name, tijdelijk.path()).await.map_err(fs_fout)?;
                }
            }
            let schrijver = Schrijver {
                sessie: s.clone(),
                ruimte,
                map,
                naam,
                basis: bestaand.as_ref().map(|b| b.huidige_versie),
                bestand: tijdelijk,
                vuil: bestaand.is_none(),
            };
            Ok(Box::new(schrijver) as Box<dyn DavFile>)
        }
        .boxed()
    }

    fn read_dir<'a>(
        &'a self,
        pad: &'a DavPath,
        _meta: ReadDirMeta,
        s: &'a Inlog,
    ) -> FsFuture<'a, FsStream<Box<dyn DavDirEntry>>> {
        async move {
            let items: Vec<Item> = match zoek(s, &segmenten(pad)).await? {
                Knoop::Wortel => s
                    .ruimtes()
                    .await
                    .map_err(fs_fout)?
                    .into_iter()
                    .map(|r| Item { naam: r.naam, meta: Meta::map(None) })
                    .collect(),
                knoop @ (Knoop::Ruimte(_) | Knoop::Map { .. }) => {
                    let (ruimte, map) = knoop.plek().unwrap();
                    let inhoud = s.inhoud(&ruimte, map.as_deref()).await.map_err(fs_fout)?;
                    let mappen = inhoud.mappen.into_iter().map(|m| Item {
                        meta: Meta::map(m.modified.as_deref()),
                        naam: m.naam,
                    });
                    let bestanden = inhoud.bestanden.into_iter().map(|b| {
                        let meta = Knoop::Bestand { ruimte: ruimte.clone(), bestand: b.clone() }.meta();
                        Item { naam: b.naam, meta }
                    });
                    mappen.chain(bestanden).collect()
                }
                Knoop::Bestand { .. } => return Err(FsError::Forbidden),
            };
            let stroom = stream::iter(items.into_iter().map(|i| Ok(Box::new(i) as Box<dyn DavDirEntry>)));
            Ok(Box::pin(stroom) as FsStream<Box<dyn DavDirEntry>>)
        }
        .boxed()
    }

    fn metadata<'a>(&'a self, pad: &'a DavPath, s: &'a Inlog) -> FsFuture<'a, Box<dyn DavMetaData>> {
        async move { Ok(Box::new(zoek(s, &segmenten(pad)).await?.meta()) as Box<dyn DavMetaData>) }.boxed()
    }

    fn create_dir<'a>(&'a self, pad: &'a DavPath, s: &'a Inlog) -> FsFuture<'a, ()> {
        async move {
            let (ruimte, map, naam) = ouder(s, pad).await?;
            let r = s.client.maak_map(&ruimte, &naam, map.as_deref()).await.map_err(fs_fout)?;
            s.vergeet(&ruimte);
            if r.nieuw { Ok(()) } else { Err(FsError::Exists) }
        }
        .boxed()
    }

    fn remove_dir<'a>(&'a self, pad: &'a DavPath, s: &'a Inlog) -> FsFuture<'a, ()> {
        async move {
            match zoek(s, &segmenten(pad)).await? {
                Knoop::Map { ruimte, map } => {
                    s.client.verwijder_map(&map.name).await.map_err(fs_fout)?;
                    s.vergeet(&ruimte);
                    Ok(())
                }
                _ => Err(FsError::Forbidden),
            }
        }
        .boxed()
    }

    fn remove_file<'a>(&'a self, pad: &'a DavPath, s: &'a Inlog) -> FsFuture<'a, ()> {
        async move {
            match zoek(s, &segmenten(pad)).await? {
                Knoop::Bestand { ruimte, bestand, .. } => {
                    s.client.verwijder(&bestand.name).await.map_err(fs_fout)?;
                    s.onthoud_verwijderd(&padsleutel(pad), &bestand.name);
                    s.vergeet(&ruimte);
                    Ok(())
                }
                _ => Err(FsError::Forbidden),
            }
        }
        .boxed()
    }

    fn rename<'a>(&'a self, van: &'a DavPath, naar: &'a DavPath, s: &'a Inlog) -> FsFuture<'a, ()> {
        async move {
            let bron = zoek(s, &segmenten(van)).await?;
            let (ruimte, map, naam) = ouder(s, naar).await?;
            if matches!(bron, Knoop::Bestand { .. }) && herstel_doel(s, naar).await? {
                s.vergeet(&ruimte);
            }
            match bron {
                Knoop::Bestand { ruimte: r, bestand, .. } if r == ruimte => {
                    s.client.verplaats(&bestand.name, &naam, map.as_deref(), true).await.map_err(fs_fout)?;
                }
                Knoop::Map { ruimte: r, map: m } if r == ruimte => {
                    s.client.verplaats_map(&m.name, &naam, map.as_deref()).await.map_err(fs_fout)?;
                }
                Knoop::Bestand { .. } | Knoop::Map { .. } => return Err(FsError::IsRemote),
                _ => return Err(FsError::Forbidden),
            }
            s.vergeet(&ruimte);
            Ok(())
        }
        .boxed()
    }

    fn copy<'a>(&'a self, van: &'a DavPath, naar: &'a DavPath, s: &'a Inlog) -> FsFuture<'a, ()> {
        async move {
            let Knoop::Bestand { bestand, .. } = zoek(s, &segmenten(van)).await? else {
                return Err(FsError::NotImplemented);
            };
            let (ruimte, map, naam) = ouder(s, naar).await?;
            if herstel_doel(s, naar).await? {
                s.vergeet(&ruimte);
            }
            let basis = match zoek(s, &segmenten(naar)).await {
                Ok(Knoop::Bestand { bestand, .. }) => Some(bestand.huidige_versie),
                Ok(_) => return Err(FsError::Forbidden),
                Err(_) => None,
            };
            let tijdelijk = tempfile::NamedTempFile::new().map_err(|_| FsError::GeneralFailure)?;
            download_bestand(&s.client, &bestand.name, tijdelijk.path()).await.map_err(fs_fout)?;
            upload_bestand(&s.client, &ruimte, map.as_deref(), &naam, tijdelijk.path(), basis)
                .await
                .map_err(fs_fout)?;
            s.vergeet(&ruimte);
            Ok(())
        }
        .boxed()
    }

    fn get_quota<'a>(&'a self, s: &'a Inlog) -> FsFuture<'a, (u64, Option<u64>)> {
        async move {
            let q = s.client.quotum().await.map_err(fs_fout)?;
            Ok((q["gebruikt_bytes"].as_u64().unwrap_or(0), q["limiet_bytes"].as_u64()))
        }
        .boxed()
    }
}

/// Leest een bestand via de downloadlink, per blok van 4 MiB.
struct Lezer {
    sessie: Inlog,
    bestand: BestandItem,
    positie: u64,
    url: Option<String>,
    buffer: Bytes,
    buffer_begin: u64,
}

impl fmt::Debug for Lezer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Lezer({})", self.bestand.name)
    }
}

impl Lezer {
    fn new(sessie: Inlog, bestand: BestandItem) -> Self {
        Self { sessie, bestand, positie: 0, url: None, buffer: Bytes::new(), buffer_begin: 0 }
    }

    async fn vul(&mut self) -> FsResult<()> {
        if self.url.is_none() {
            let link = self.sessie.client.download(&self.bestand.name).await.map_err(fs_fout)?;
            self.url = Some(link.url);
        }
        let eind = (self.positie + LEESBLOK).min(self.bestand.grootte) - 1;
        let antwoord = self
            .sessie
            .http
            .get(self.url.as_ref().unwrap())
            .header("Range", format!("bytes={}-{}", self.positie, eind))
            .send()
            .await
            .map_err(|_| FsError::GeneralFailure)?;
        if !antwoord.status().is_success() {
            return Err(FsError::GeneralFailure);
        }
        self.buffer = antwoord.bytes().await.map_err(|_| FsError::GeneralFailure)?;
        self.buffer_begin = self.positie;
        Ok(())
    }
}

impl DavFile for Lezer {
    fn metadata(&mut self) -> FsFuture<'_, Box<dyn DavMetaData>> {
        let meta = Knoop::Bestand { ruimte: String::new(), bestand: self.bestand.clone() }.meta();
        async move { Ok(Box::new(meta) as Box<dyn DavMetaData>) }.boxed()
    }
    fn write_buf(&mut self, _buf: Box<dyn bytes::Buf + Send>) -> FsFuture<'_, ()> {
        async { Err(FsError::Forbidden) }.boxed()
    }
    fn write_bytes(&mut self, _buf: Bytes) -> FsFuture<'_, ()> {
        async { Err(FsError::Forbidden) }.boxed()
    }
    fn read_bytes(&mut self, aantal: usize) -> FsFuture<'_, Bytes> {
        async move {
            if self.positie >= self.bestand.grootte || aantal == 0 {
                return Ok(Bytes::new());
            }
            let in_buffer = self.positie >= self.buffer_begin
                && self.positie < self.buffer_begin + self.buffer.len() as u64;
            if !in_buffer {
                self.vul().await?;
            }
            let begin = (self.positie - self.buffer_begin) as usize;
            let eind = (begin + aantal).min(self.buffer.len());
            let stuk = self.buffer.slice(begin..eind);
            self.positie += stuk.len() as u64;
            Ok(stuk)
        }
        .boxed()
    }
    fn seek(&mut self, pos: SeekFrom) -> FsFuture<'_, u64> {
        let nieuw = match pos {
            SeekFrom::Start(n) => Some(n),
            SeekFrom::Current(d) => self.positie.checked_add_signed(d),
            SeekFrom::End(d) => self.bestand.grootte.checked_add_signed(d),
        };
        async move {
            self.positie = nieuw.ok_or(FsError::GeneralFailure)?;
            Ok(self.positie)
        }
        .boxed()
    }
    fn flush(&mut self) -> FsFuture<'_, ()> {
        async { Ok(()) }.boxed()
    }
}

/// Schrijft naar een tijdelijk bestand; bij `flush` gaat het als (delta-)upload naar de server, met de huidige
/// versie als basis: overschrijven wordt zo een gewone nieuwe versie, of een conflictkopie als intussen een
/// ander het bestand wijzigde of vergrendelde.
struct Schrijver {
    sessie: Inlog,
    ruimte: String,
    map: Option<String>,
    naam: String,
    basis: Option<u64>,
    bestand: tempfile::NamedTempFile,
    vuil: bool,
}

impl fmt::Debug for Schrijver {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Schrijver({}/{})", self.ruimte, self.naam)
    }
}

impl Schrijver {
    fn grootte(&self) -> u64 {
        self.bestand.as_file().metadata().map(|m| m.len()).unwrap_or(0)
    }
}

impl DavFile for Schrijver {
    fn metadata(&mut self) -> FsFuture<'_, Box<dyn DavMetaData>> {
        let meta = Meta { grootte: self.grootte(), gewijzigd: SystemTime::now(), map: false, etag: None };
        async move { Ok(Box::new(meta) as Box<dyn DavMetaData>) }.boxed()
    }
    fn write_buf(&mut self, mut buf: Box<dyn bytes::Buf + Send>) -> FsFuture<'_, ()> {
        let data = buf.copy_to_bytes(buf.remaining());
        self.write_bytes(data)
    }
    fn write_bytes(&mut self, buf: Bytes) -> FsFuture<'_, ()> {
        let uitkomst = self.bestand.as_file_mut().write_all(&buf);
        self.vuil = true;
        async move { uitkomst.map_err(|_| FsError::GeneralFailure) }.boxed()
    }
    fn read_bytes(&mut self, aantal: usize) -> FsFuture<'_, Bytes> {
        let mut buffer = vec![0u8; aantal];
        let uitkomst = self.bestand.as_file_mut().read(&mut buffer).map(|n| {
            buffer.truncate(n);
            Bytes::from(buffer)
        });
        async move { uitkomst.map_err(|_| FsError::GeneralFailure) }.boxed()
    }
    fn seek(&mut self, pos: SeekFrom) -> FsFuture<'_, u64> {
        let uitkomst = self.bestand.as_file_mut().seek(pos);
        async move { uitkomst.map_err(|_| FsError::GeneralFailure) }.boxed()
    }
    fn flush(&mut self) -> FsFuture<'_, ()> {
        async move {
            if !self.vuil {
                return Ok(());
            }
            self.bestand.as_file_mut().flush().map_err(|_| FsError::GeneralFailure)?;
            let r = upload_bestand(
                &self.sessie.client,
                &self.ruimte,
                self.map.as_deref(),
                &self.naam,
                self.bestand.path(),
                self.basis,
            )
            .await
            .map_err(fs_fout)?;
            self.sessie.vergeet(&self.ruimte);
            self.basis = Some(r.versie);
            self.vuil = false;
            Ok(())
        }
        .boxed()
    }
}
