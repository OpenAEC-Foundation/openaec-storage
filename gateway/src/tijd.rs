//! Frappe-tijdstempels (`JJJJ-MM-DD uu:mm:ss[.ffffff]`) naar `SystemTime`. De tijdzone van de site wordt als UTC
//! gelezen: voor WebDAV telt alleen dat de tijd verandert als het bestand verandert.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn dagen_sinds_epoch(jaar: i64, maand: i64, dag: i64) -> i64 {
    // Algoritme van Howard Hinnant (days_from_civil).
    let y = if maand <= 2 { jaar - 1 } else { jaar };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (maand + 9) % 12;
    let doy = (153 * mp + 2) / 5 + dag - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

pub fn lees(tekst: &str) -> Option<SystemTime> {
    let (datum, tijd) = tekst.trim().split_once([' ', 'T'])?;
    let mut d = datum.split('-').map(|x| x.parse::<i64>().ok());
    let (jaar, maand, dag) = (d.next()??, d.next()??, d.next()??);
    let (hms, fractie) = tijd.split_once('.').unwrap_or((tijd, "0"));
    let mut t = hms.split(':').map(|x| x.parse::<i64>().ok());
    let (uur, min, sec) = (t.next()??, t.next()??, t.next()?.unwrap_or(0));
    let micro: u64 = format!("{fractie:0<6}")[..6].parse().ok()?;
    let seconden = dagen_sinds_epoch(jaar, maand, dag) * 86400 + uur * 3600 + min * 60 + sec;
    let seconden = u64::try_from(seconden).ok()?;
    Some(UNIX_EPOCH + Duration::from_secs(seconden) + Duration::from_micros(micro))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn leest_frappe_tijdstempels() {
        let t = lees("2026-10-20 13:45:07.250000").unwrap();
        assert_eq!(t.duration_since(UNIX_EPOCH).unwrap().as_millis(), 1_792_503_907_250);
        assert_eq!(lees("1970-01-01 00:00:00"), Some(UNIX_EPOCH));
        assert_eq!(lees("2024-02-29 00:00:01").unwrap().duration_since(UNIX_EPOCH).unwrap().as_secs(), 1_709_164_801);
        assert!(lees("geen datum").is_none());
    }
}
