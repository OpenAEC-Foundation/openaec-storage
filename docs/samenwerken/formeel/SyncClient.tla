----------------------------- MODULE SyncClient -----------------------------
(***************************************************************************)
(* De desktop-sync (crate openaec-sync, motor.rs) tegen de server, met     *)
(* gebruikers die bestanden wijzigen en verwijderen, en een netwerk dat    *)
(* verzoeken en antwoorden kwijtraakt.                                     *)
(*                                                                         *)
(* Clientvarianten (constante Client):                                     *)
(*  "main": openaec-cloud-desktop main a3aceac. Na een upload meet         *)
(*          onthoud() het kenmerk van NA de upload (ook voor een           *)
(*          conflictkopie: staat die hier niet, dan een fout); een         *)
(*          download haalt de nieuwste inhoud en zet die met rename over   *)
(*          het pad; VerwijderLokaal doet remove_file zonder controle.     *)
(*  "5a"  : PR #8 (samenwerken/5a-veilig-vervangen, f0d144c): het kenmerk  *)
(*          van vóór de upload (onthoud_als); elke lokale vervanging en    *)
(*          verwijdering via vervang.rs: alleen als het bestand gelijk is  *)
(*          aan de toestand naar een bron, dan plaatsen zonder te          *)
(*          vervangen; aan het eind van de ronde de bron controleren       *)
(*          (gelijk: weg; anders: lever_in naar de server).               *)
(*  "5b"  : 5a plus de opslaan-lus voor IFC-bestanden (desktop-PR #9,      *)
(*          samenwerken/5b-samenvoegen, 47eba36): momentopname, sla_op()   *)
(*          met bij_conflict=weiger, samenvoegen, "afgeleid van S"; een    *)
(*          download haalt precies de versie uit de lijst; na een verloren *)
(*          antwoord op een gewone upload kijkt de client of zijn inhoud   *)
(*          de huidige versie is (vastgelegd()).                           *)
(*                                                                         *)
(* De server is hier atomair (de binnenkant staat in UploadServer.tla):    *)
(*  - een gewone upload vergelijkt alleen het versienummer (basis_versie); *)
(*    anders een conflictkopie op een nieuw pad (of een nieuw bestand als  *)
(*    er niets op de plek staat);                                          *)
(*  - schrijf_als (alleen 5b) vergelijkt de versieregel en weigert bij een *)
(*    andere;                                                              *)
(*  - verwijder(bestand) zet het bestand in de prullenbak, zonder basis.   *)
(*                                                                         *)
(* Inhoud: een verzameling "bewerkingen" (atomen). Een gebruiker voegt bij *)
(* elke opslag een nieuw atoom toe. Samenvoegen zonder conflict geeft de   *)
(* vereniging; samenvoegen kan ook altijd een conflict geven (vrije        *)
(* keuze). Dit is een beperkte abstractie, geen bewezen contract voor elk  *)
(* IFC-register (zie samenvoegen-bewijs.md). Het kenmerk (grootte +        *)
(* wijzigingstijd) is hier de inhoud zelf.                                 *)
(*                                                                         *)
(* Conflictkopieën krijgen een eigen pad uit KopiePaden (elk pad één       *)
(* keer). Alleen de client die de kopie maakte, synct dat pad; andere      *)
(* clients laten het liggen (ze zouden het alleen ophalen). Gebruikers     *)
(* werken alleen op Paden. Zijn er geen vrije KopiePaden of Bestanden      *)
(* meer, dan gebeurt die upload in dit model niet (een grens van de run).  *)
(*                                                                         *)
(* Per client en pad loopt één ronde tegelijk; paden zijn onafhankelijk    *)
(* (geen verplaatsen). Een ronde: lijst, scan + beslissen, de taak, de     *)
(* bronnen afronden (5a/5b), de toestand bewaren.                          *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Clients, Paden,
    KopiePaden,     \* paden voor conflictkopieën (elk hoogstens één keer gebruikt)
    IfcPaden,       \* paden die de opslaan-lus nemen (alleen bij Client = "5b")
    Bestanden,      \* ids voor Opslagbestand
    Client,         \* "main", "5a" of "5b"
    MaxAtomen,      \* zoveel keer slaat een gebruiker in totaal op
    MaxVerwijder,   \* zoveel keer verwijdert een gebruiker in totaal een bestand
    MaxStoring,     \* zoveel verzoeken of antwoorden raakt het netwerk in totaal kwijt
    MaxPogingen,    \* sla_op: zo vaak schrijven (standaard 5)
    AppBuffer,      \* TRUE: een programma houdt de inhoud open en schrijft bij opslaan zijn eigen kopie terug
    OpenBestand,    \* TRUE: een programma schrijft via een open bestand nog in een bron (na de rename)
    Fixes,          \* voorgestelde reparaties die aan staan (zie het rapport), deelverzameling van FixNamen
    Geen

\* "verwijder_basis" : verwijder(bestand, basis): de server weigert als de huidige versie niet de basis is.
\* "upload_bestand"  : een gewone upload stuurt ook basis_bestand mee: een ander bestand op de plek geeft een
\*                     conflictkopie, geen nieuwe versie (PR #9 kent dit al; de motor stuurt het nog niet).
\* "kenmerk_voor"    : (alleen voor main) het kenmerk van vóór de upload, zoals PR #8 (5a).
\* "veilig_vervangen": (alleen voor main) vervangen en verwijderen via de bron, zoals PR #8 (5a).
FixNamen == {"verwijder_basis", "upload_bestand", "kenmerk_voor", "veilig_vervangen"}
ASSUME Fixes \subseteq FixNamen
ASSUME Paden \cap KopiePaden = {}

KenmerkVoor == Client \in {"5a", "5b"} \/ "kenmerk_voor" \in Fixes
VeiligVervangen == Client \in {"5a", "5b"} \/ "veilig_vervangen" \in Fixes

ASSUME Client \in {"main", "5a", "5b"}

AllePaden == Paden \cup KopiePaden

VARIABLES
    \* server
    actief,     \* [AllePaden -> Bestanden \cup {Geen}]
    versies,    \* [Bestanden -> Seq(inhoud)]
    gebruikt,   \* bestand-ids in gebruik
    prul,       \* bestanden in de prullenbak
    kopieVan,   \* [KopiePaden -> Clients \cup {Geen}]: wie de conflictkopie op dit pad maakte
    \* clients
    lok,        \* [Clients -> [AllePaden -> inhoud \cup {Geen}]]: het bestand op de schijf
    buf,        \* [Clients -> [AllePaden -> inhoud \cup {Geen}]]: wat het programma in het geheugen heeft (AppBuffer)
    bekend,     \* [Clients -> [AllePaden -> Geen \cup [b, v, k, afg]]]: de bewaarde toestand (staat.json)
    mem,        \* idem, de toestand in het geheugen tijdens de ronde
    bron,       \* [Clients -> [AllePaden -> Geen \cup [inhoud, soort, verwacht, basis, bb]]]: .openaec/bron
    r,          \* [Clients -> [AllePaden -> rondetoestand]]
    elders,     \* atomen in .openaec/eigen (5b: eigen invoer van een samenvoeging)
    \* gebruikers en netwerk
    volgend,    \* het volgende atoom
    gewist,     \* atomen die een gebruiker zelf wiste (bewust)
    nVerwijder, nStoring

vars == <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, r, elders,
          volgend, gewist, nVerwijder, nStoring>>

Atomen == 1..MaxAtomen
Inhoud == SUBSET Atomen

RustR == [pc |-> "rust", sv |-> Geen, pl |-> Geen, tmp |-> Geen, ons |-> Geen, inh |-> Geen,
          basis |-> Geen, bb |-> Geen, n |-> 0, van |-> 0]

Init ==
    /\ actief = [p \in AllePaden |-> Geen]
    /\ versies = [b \in Bestanden |-> << >>]
    /\ gebruikt = {}
    /\ prul = {}
    /\ kopieVan = [p \in KopiePaden |-> Geen]
    /\ lok = [c \in Clients |-> [p \in AllePaden |-> Geen]]
    /\ buf = [c \in Clients |-> [p \in AllePaden |-> Geen]]
    /\ bekend = [c \in Clients |-> [p \in AllePaden |-> Geen]]
    /\ mem = [c \in Clients |-> [p \in AllePaden |-> Geen]]
    /\ bron = [c \in Clients |-> [p \in AllePaden |-> Geen]]
    /\ r = [c \in Clients |-> [p \in AllePaden |-> RustR]]
    /\ elders = {}
    /\ volgend = 1
    /\ gewist = {}
    /\ nVerwijder = 0
    /\ nStoring = 0

(***************************************************************************)
(* Hulp                                                                    *)
(***************************************************************************)
Laatste(s) == s[Len(s)]
Huidig(b) == Len(versies[b])
InhoudVan(b) == Laatste(versies[b])

\* Wat lijst() over pad p geeft: het actieve bestand en zijn huidige versie.
Zicht(p) == IF actief[p] = Geen THEN Geen ELSE [b |-> actief[p], v |-> Huidig(actief[p])]

Zet(f, c, p, w) == [f EXCEPT ![c][p] = w]
ZetR(c, p, w) == r' = Zet(r, c, p, w)

\* Een netwerkverzoek: "ok", "verzoek_weg" (de server zag niets) of "antwoord_weg" (de server deed het wel).
Uitkomsten == IF nStoring < MaxStoring THEN {"ok", "verzoek_weg", "antwoord_weg"} ELSE {"ok"}
Storing(u) == nStoring' = IF u = "ok" THEN nStoring ELSE nStoring + 1

VrijId == Bestanden \ gebruikt
NieuwId == IF VrijId = {} THEN Geen ELSE CHOOSE b \in VrijId : TRUE
VrijeKopie == {q \in KopiePaden : kopieVan[q] = Geen}
NieuweKopie == IF VrijeKopie = {} THEN Geen ELSE CHOOSE q \in VrijeKopie : TRUE

\* Synct client c pad p? Gewone paden altijd; een kopiepad alleen bij wie de kopie maakte, en pas als de ronde
\* die haar maakte, na_upload achter de rug heeft (in de code is dat één ronde: het kopiepad komt in de volgende).
Mag(c, p) ==
    \/ p \in Paden
    \/ /\ kopieVan[p] = c
       /\ \A q \in Paden : ~(r[c][q].pc = "na_upload" /\ r[c][q].tmp.soort = "kopie" /\ r[c][q].tmp.kp = p)

(***************************************************************************)
(* Server                                                                  *)
(***************************************************************************)

\* Een gewone upload (bij_conflict=kopie) met basis op nummer.
\* "versie" (nieuwe versie van het bestand op de plek), "kopie" (conflictkopie), "nieuw" (nieuw bestand).
UploadUitkomst(p, basisNr, basisB) ==
    IF actief[p] # Geen
       THEN IF /\ basisNr # Geen /\ basisNr = Huidig(actief[p])
               /\ ("upload_bestand" \in Fixes => basisB = actief[p])
            THEN "versie" ELSE "kopie"
       ELSE "nieuw"

\* Kan de server deze upload in dit model uitvoeren (een vrij id en zo nodig een vrij kopiepad)?
Uitvoerbaar(p, basisNr, basisB) ==
    LET s == UploadUitkomst(p, basisNr, basisB) IN
    /\ (s \in {"nieuw", "kopie"} => NieuwId # Geen)
    /\ (s = "kopie" => NieuweKopie # Geen)

\* De servertoestand na een gewone upload van `inh` op pad p door client c.
ServerUpload(c, p, basisNr, basisB, inh) ==
    LET s == UploadUitkomst(p, basisNr, basisB)
        id == NieuwId
        kp == NieuweKopie
    IN CASE s = "versie" -> /\ versies' = [versies EXCEPT ![actief[p]] = Append(@, inh)]
                            /\ UNCHANGED <<actief, gebruikt, kopieVan>>
         [] s = "kopie"  -> /\ actief' = [actief EXCEPT ![kp] = id]
                            /\ versies' = [versies EXCEPT ![id] = <<inh>>]
                            /\ gebruikt' = gebruikt \cup {id}
                            /\ kopieVan' = [kopieVan EXCEPT ![kp] = c]
         [] s = "nieuw"  -> /\ actief' = [actief EXCEPT ![p] = id]
                            /\ versies' = [versies EXCEPT ![id] = <<inh>>]
                            /\ gebruikt' = gebruikt \cup {id}
                            /\ UNCHANGED kopieVan

\* Wat de client terugkrijgt: [soort, b, v, kp] (kp: het pad van de conflictkopie).
UploadResultaat(p, basisNr, basisB) ==
    LET s == UploadUitkomst(p, basisNr, basisB) IN
    CASE s = "versie" -> [soort |-> s, b |-> actief[p], v |-> Huidig(actief[p]) + 1, kp |-> Geen]
      [] s = "kopie"  -> [soort |-> s, b |-> NieuwId, v |-> 1, kp |-> NieuweKopie]
      [] s = "nieuw"  -> [soort |-> s, b |-> NieuwId, v |-> 1, kp |-> Geen]

\* schrijf_als (PR #9, bij_conflict=weiger, basis_bestand + basis_versie_id): "gelijk", "verouderd" of "onbekend".
SchrijfAlsUitkomst(p, basis) ==
    IF actief[p] = Geen \/ basis[1] # actief[p] THEN "onbekend"
    ELSE IF basis[2] = Huidig(actief[p]) THEN "gelijk" ELSE "verouderd"

(***************************************************************************)
(* Gebruikers (alleen op Paden)                                            *)
(***************************************************************************)

\* Opslaan in een programma. Zonder AppBuffer leest het programma eerst het bestand (zoals een editor die
\* herlaadt). Met AppBuffer schrijft het zijn eigen kopie terug, ook als de sync het bestand intussen verving.
Bewerk(c, p) ==
    /\ volgend <= MaxAtomen
    /\ LET basis == IF AppBuffer /\ buf[c][p] # Geen THEN buf[c][p]
                    ELSE IF lok[c][p] = Geen THEN {} ELSE lok[c][p]
           nieuw == basis \cup {volgend}
       IN /\ lok' = Zet(lok, c, p, nieuw)
          /\ IF AppBuffer THEN buf' = Zet(buf, c, p, nieuw) ELSE UNCHANGED buf
    /\ volgend' = volgend + 1
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, bekend, mem, bron, r, elders, gewist, nVerwijder, nStoring>>

\* Het programma opent het bestand opnieuw (leest de schijf).
Open(c, p) ==
    /\ AppBuffer
    /\ lok[c][p] # Geen
    /\ buf[c][p] # lok[c][p]
    /\ buf' = Zet(buf, c, p, lok[c][p])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, bekend, mem, bron, r, elders, volgend, gewist, nVerwijder, nStoring>>

\* Een programma had het bestand open toen de sync het naar de bron verplaatste, en schrijft nu via die open
\* handle: de wijziging komt in de bron (Linux en macOS; op Windows faalt de rename dan meestal).
BewerkBron(c, p) ==
    /\ OpenBestand
    /\ bron[c][p] # Geen
    /\ volgend <= MaxAtomen
    /\ bron' = [bron EXCEPT ![c][p].inhoud = @ \cup {volgend}]
    /\ volgend' = volgend + 1
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, r, elders, gewist, nVerwijder, nStoring>>

\* De gebruiker verwijdert het bestand bewust; wat erin stond, wist hij zelf.
VerwijderDoorGebruiker(c, p) ==
    /\ lok[c][p] # Geen
    /\ nVerwijder < MaxVerwijder
    /\ gewist' = gewist \cup lok[c][p]
    /\ lok' = Zet(lok, c, p, Geen)
    /\ IF AppBuffer THEN buf' = Zet(buf, c, p, Geen) ELSE UNCHANGED buf
    /\ nVerwijder' = nVerwijder + 1
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, bekend, mem, bron, r, elders, volgend, nStoring>>

(***************************************************************************)
(* De ronde                                                                *)
(***************************************************************************)

Basis(k) == IF k.afg = Geen THEN k.v ELSE k.afg.van     \* Bekend::basis() (5b)

\* lijst(): de server zoals hij nu is; de toestand van de vorige ronde gaat het geheugen in.
Lijst(c, p) ==
    /\ Mag(c, p)
    /\ r[c][p].pc = "rust"
    /\ ZetR(c, p, [RustR EXCEPT !.pc = "scan", !.sv = Zicht(p)])
    /\ mem' = Zet(mem, c, p, bekend[c][p])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* De lokale map aflopen en per pad beslissen (motor.rs: lokaal_gewijzigd, server_gewijzigd en de match).
Beslis(c, p) ==
    /\ r[c][p].pc = "scan"
    /\ LET sv == r[c][p].sv
           sk == lok[c][p]
           k == mem[c][p]
           lg == sk # Geen /\ (k = Geen \/ sk # k.k)
           sg == sv # Geen /\ (k = Geen \/ sv.v # k.v \/ sv.b # k.b \/ (Client = "5b" /\ k.afg # Geen))
           lus == Client = "5b" /\ p \in IfcPaden /\ k # Geen
           taak == CASE sk # Geen /\ sv # Geen ->
                          IF lg THEN (IF lus THEN "lus_start" ELSE "upload")
                          ELSE IF sg THEN "download"
                          ELSE IF k = Geen THEN "onthoud" ELSE "einde"
                     [] sk # Geen /\ sv = Geen ->
                          IF k # Geen /\ ~lg THEN "verwijder_lokaal" ELSE "upload"
                     [] sk = Geen /\ sv # Geen ->
                          IF k # Geen /\ ~sg THEN "verwijder_server" ELSE "download"
                     [] OTHER -> "vergeet"
           basisNr == IF sv = Geen \/ k = Geen THEN Geen ELSE Basis(k)
           basisB == IF sv = Geen \/ k = Geen THEN Geen ELSE k.b
       IN CASE taak = "onthoud" ->
                 /\ mem' = Zet(mem, c, p, [b |-> sv.b, v |-> sv.v, k |-> sk, afg |-> Geen])
                 /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
            [] taak = "vergeet" ->
                 /\ mem' = Zet(mem, c, p, Geen)
                 /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
            [] OTHER ->
                 /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = taak, !.basis = basisNr, !.bb = basisB])
                 /\ UNCHANGED mem
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Upload (gewoon, met conflictkopie)                                      *)
(***************************************************************************)

\* upload_poging(): de inhoud lezen (hashes en delen; hier één moment, zie de README).
UploadLees(c, p) ==
    /\ r[c][p].pc = "upload"
    /\ IF lok[c][p] = Geen
          THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])     \* bestand weg: fout, volgende ronde
          ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "upload_stuur", !.pl = lok[c][p]])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* upload_start + delen + upload_klaar als één verzoek aan de server.
UploadStuur(c, p) ==
    /\ r[c][p].pc = "upload_stuur"
    /\ LET rr == r[c][p] IN
       /\ Uitvoerbaar(p, rr.basis, rr.bb)
       /\ \E u \in Uitkomsten :
            /\ Storing(u)
            /\ IF u = "verzoek_weg"
                  THEN UNCHANGED <<actief, versies, gebruikt, kopieVan>>
                  ELSE ServerUpload(c, p, rr.basis, rr.bb, rr.pl)
            /\ CASE u = "ok" ->
                      ZetR(c, p, [rr EXCEPT !.pc = "na_upload", !.tmp = UploadResultaat(p, rr.basis, rr.bb)])
                 [] u = "antwoord_weg" /\ Client = "5b" ->       \* vastgelegd(): staat mijn inhoud er nu?
                      ZetR(c, p, [rr EXCEPT !.pc = "upload_herstel"])
                 [] OTHER -> ZetR(c, p, [rr EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<prul, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* 5b (47eba36, lib.rs vastgelegd): na een verloren antwoord bladeren en blokken opvragen; is precies deze inhoud
\* de huidige versie op de plek, dan telt de upload als gelukt (geen conflict).
UploadHerstel(c, p) ==
    /\ r[c][p].pc = "upload_herstel"
    /\ \E u \in Uitkomsten :
         /\ Storing(u)
         /\ LET rr == r[c][p]
                b == actief[p]
            IN IF u = "ok" /\ b # Geen /\ InhoudVan(b) = rr.pl
                  THEN ZetR(c, p, [rr EXCEPT !.pc = "na_upload",
                                             !.tmp = [soort |-> "versie", b |-> b, v |-> Huidig(b), kp |-> Geen]])
                  ELSE ZetR(c, p, [rr EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* na_upload() met de drie takken van de code:
\*  - geen conflict: de toestand van het pad (main: het kenmerk van nu; 5a/5b: van vlak vóór de upload);
\*  - conflict en het bestand stond in de lijst (Some(server)): het lokale bestand naar het pad van de kopie
\*    (main: rename; 5a/5b: plaatsen zonder vervangen), de kopie in de toestand, dan de serverversie ophalen;
\*  - conflict en het bestand stond niet in de lijst (None): de kopie in de toestand (main: onthoud() meet het
\*    kenmerk van een bestand dat hier niet staat, en faalt; 5a/5b: met het kenmerk van vóór de upload); het
\*    lokale bestand blijft op zijn pad.
NaUpload(c, p) ==
    /\ r[c][p].pc = "na_upload"
    /\ LET rr == r[c][p]
           res == rr.tmp
           kp == res.kp
           kopie == [b |-> res.b, v |-> res.v, afg |-> Geen, k |-> IF KenmerkVoor THEN rr.pl ELSE lok[c][p]]
       IN IF res.soort # "kopie"
          THEN /\ mem' = Zet(mem, c, p, [b |-> res.b, v |-> res.v, afg |-> Geen,
                                         k |-> IF KenmerkVoor THEN rr.pl ELSE lok[c][p]])
               /\ ZetR(c, p, [rr EXCEPT !.pc = "einde", !.tmp = Geen])
               /\ UNCHANGED <<lok, bekend>>
          ELSE IF rr.sv # Geen
          THEN IF lok[c][p] = Geen
               THEN /\ ZetR(c, p, [rr EXCEPT !.pc = "einde", !.tmp = Geen])     \* rename faalt: fout
                    /\ UNCHANGED <<lok, mem, bekend>>
               ELSE /\ lok' = [lok EXCEPT ![c][kp] = lok[c][p], ![c][p] = Geen]
                    /\ mem' = Zet(mem, c, kp, kopie)
                    /\ bekend' = Zet(bekend, c, kp, kopie)
                    /\ ZetR(c, p, [rr EXCEPT !.pc = "download", !.tmp = Geen])
          ELSE IF KenmerkVoor
          THEN /\ mem' = Zet(mem, c, kp, kopie)
               /\ bekend' = Zet(bekend, c, kp, kopie)
               /\ ZetR(c, p, [rr EXCEPT !.pc = "einde", !.tmp = Geen])
               /\ UNCHANGED lok
          ELSE /\ ZetR(c, p, [rr EXCEPT !.pc = "einde", !.tmp = Geen])
               /\ UNCHANGED <<lok, mem, bekend>>
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Download                                                                *)
(***************************************************************************)

\* download_poging(): main en 5a halen de huidige inhoud van het bestand uit de lijst (op id, ook uit de
\* prullenbak); 5b haalt precies de versie uit de lijst (versie_id).
DownloadHaal(c, p) ==
    /\ r[c][p].pc = "download"
    /\ \E u \in Uitkomsten :
         /\ Storing(u)
         /\ LET sv == r[c][p].sv
                inh == IF Client = "5b" THEN versies[sv.b][sv.v] ELSE InhoudVan(sv.b)
            IN IF u = "ok"
                  THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "download_plaats", !.tmp = inh])
                  ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* main: rename van het tijdelijke bestand over het pad; wat er nu staat, is weg.
DownloadPlaatsMain(c, p) ==
    /\ ~VeiligVervangen
    /\ r[c][p].pc = "download_plaats"
    /\ lok' = Zet(lok, c, p, r[c][p].tmp)
    /\ mem' = Zet(mem, c, p, [b |-> r[c][p].sv.b, v |-> r[c][p].sv.v, k |-> r[c][p].tmp, afg |-> Geen])
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* 5a/5b, vervang.rs vervang_binnen(), stap 1:
\*  - staat er hier niets: niets te beschermen (vervangen: meteen plaatsen; verwijderen: klaar);
\*  - kent de toestand het bestand niet, of wijkt het kenmerk af: het bestand is van de gebruiker en blijft
\*    staan (PadBezet of LokaalGewijzigd), er gebeurt niets;
\*  - anders: het bestand naar de bron (één rename); verwacht = de inhoud die de toestand kent.
\* Ligt er nog een bron van een eerdere ronde voor dit pad, dan wacht deze vervanging tot een volgende ronde
\* (vereenvoudiging: in de code kunnen meer bronnen naast elkaar staan).
Verplaatsbaar(c, p) == lok[c][p] # Geen /\ mem[c][p] # Geen /\ lok[c][p] = mem[c][p].k

VerplaatsNaarBron(c, p, soort, volgende) ==
    LET k == mem[c][p] IN
    CASE bron[c][p] # Geen ->
              /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
              /\ UNCHANGED <<lok, bron>>
      [] lok[c][p] = Geen ->
              /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = volgende])
              /\ UNCHANGED <<lok, bron>>
      [] ~Verplaatsbaar(c, p) ->
              /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
              /\ UNCHANGED <<lok, bron>>
      [] OTHER ->
              /\ bron' = Zet(bron, c, p, [inhoud |-> lok[c][p], soort |-> soort, verwacht |-> lok[c][p],
                                          basis |-> Basis(k), bb |-> k.b])
              /\ lok' = Zet(lok, c, p, Geen)
              /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = volgende])

DownloadVerplaats(c, p) ==
    /\ VeiligVervangen
    /\ r[c][p].pc = "download_plaats"
    /\ VerplaatsNaarBron(c, p, "vervang", "download_zet")
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bekend, mem, elders, volgend, gewist, nVerwijder, nStoring>>

\* vervang.rs stap 2: plaatsen zonder te vervangen. Staat er intussen een bestand, dan blijft dat staan.
DownloadZet(c, p) ==
    /\ r[c][p].pc = "download_zet"
    /\ IF lok[c][p] = Geen
          THEN /\ lok' = Zet(lok, c, p, r[c][p].tmp)
               /\ mem' = Zet(mem, c, p, [b |-> r[c][p].sv.b, v |-> r[c][p].sv.v, k |-> r[c][p].tmp, afg |-> Geen])
          ELSE UNCHANGED <<lok, mem>>
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Verwijderen                                                             *)
(***************************************************************************)

\* Op de server verwijderd en lokaal ongewijzigd. main: remove_file zonder nieuwe controle.
VerwijderLokaalMain(c, p) ==
    /\ ~VeiligVervangen
    /\ r[c][p].pc = "verwijder_lokaal"
    /\ lok' = Zet(lok, c, p, Geen)
    /\ mem' = Zet(mem, c, p, Geen)
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* 5a/5b: via de bron. Alleen bij "Verwijderd" (er stond niets, of het ging naar de bron) vergeet de toestand
\* het pad; anders blijft alles zoals het was.
VerwijderLokaalVeilig(c, p) ==
    /\ VeiligVervangen
    /\ r[c][p].pc = "verwijder_lokaal"
    /\ VerplaatsNaarBron(c, p, "verwijder", "einde")
    /\ IF bron[c][p] = Geen /\ (lok[c][p] = Geen \/ Verplaatsbaar(c, p))
          THEN mem' = Zet(mem, c, p, Geen)
          ELSE UNCHANGED mem
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bekend, elders, volgend, gewist, nVerwijder, nStoring>>

\* Lokaal verwijderd en op de server ongewijzigd: verwijder(bestand) op de server, zonder basis.
VerwijderServer(c, p) ==
    /\ r[c][p].pc = "verwijder_server"
    /\ \E u \in Uitkomsten :
         /\ Storing(u)
         /\ LET b == r[c][p].sv.b
                geweigerd == "verwijder_basis" \in Fixes /\ Huidig(b) # r[c][p].sv.v
            IN /\ IF u = "verzoek_weg" \/ geweigerd
                     THEN UNCHANGED <<actief, prul>>
                     ELSE /\ prul' = prul \cup {b}
                          /\ actief' = [q \in AllePaden |-> IF actief[q] = b THEN Geen ELSE actief[q]]
               /\ IF u = "ok" /\ ~geweigerd THEN mem' = Zet(mem, c, p, Geen) ELSE UNCHANGED mem
         /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<versies, gebruikt, kopieVan, lok, buf, bekend, bron, elders, volgend, gewist, nVerwijder>>

(***************************************************************************)
(* 5b: de opslaan-lus voor een IFC-bestand (voeg_samen_op, sla_op)         *)
(***************************************************************************)

\* De momentopname (kenmerk vóór en na het lezen gelijk: hier één moment) en de beginbasis.
\* Afgeleid van S: eerst S, ons en de huidige versie samenvoegen.
LusStart(c, p) ==
    /\ r[c][p].pc = "lus_start"
    /\ LET k == mem[c][p]
           sv == r[c][p].sv
           ons == lok[c][p]
       IN IF ons = Geen
             THEN /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"]) /\ UNCHANGED nStoring
          ELSE IF k.afg = Geen
             THEN /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "lus_schrijf", !.ons = ons, !.inh = ons,
                                               !.basis = <<k.b, k.v>>, !.n = 1, !.van = k.v])
                  /\ UNCHANGED nStoring
          ELSE IF sv.b # k.b
             THEN /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "als_kopie", !.ons = ons]) /\ UNCHANGED nStoring
          ELSE \E u \in Uitkomsten, gelukt \in BOOLEAN :     \* lees_versie(huidig), dan voeg_samen(S, ons, hun)
                 /\ Storing(u)
                 /\ IF u # "ok" THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
                    ELSE IF gelukt
                         THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "lus_schrijf", !.ons = ons,
                                          !.inh = ons \cup versies[sv.b][sv.v],
                                          !.basis = <<sv.b, sv.v>>, !.n = 1, !.van = k.afg.van])
                         ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "als_kopie", !.ons = ons])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* sla_op(): schrijf_als(basis, inhoud). Verouderd: basis en huidige lezen, samenvoegen, opnieuw.
LusSchrijf(c, p) ==
    /\ r[c][p].pc = "lus_schrijf"
    /\ LET rr == r[c][p]
           s == SchrijfAlsUitkomst(p, rr.basis)
       IN \E u \in Uitkomsten, gelukt \in BOOLEAN :
            /\ Storing(u)
            /\ IF u # "verzoek_weg" /\ s = "gelijk"
                  THEN versies' = [versies EXCEPT ![actief[p]] = Append(@, rr.inh)]
                  ELSE UNCHANGED versies
            /\ IF u # "ok"
                  THEN ZetR(c, p, [rr EXCEPT !.pc = "einde"])       \* LusFout::Opslag: volgende ronde opnieuw
               ELSE CASE s = "gelijk" ->
                          ZetR(c, p, [rr EXCEPT !.pc = "na_samenvoegen", !.basis = <<actief[p], Huidig(actief[p]) + 1>>])
                      [] s = "onbekend" -> ZetR(c, p, [rr EXCEPT !.pc = "als_kopie"])
                      [] s = "verouderd" ->
                          IF rr.n >= MaxPogingen \/ ~gelukt
                             THEN ZetR(c, p, [rr EXCEPT !.pc = "als_kopie"])     \* te vaak verouderd, of conflict
                             ELSE ZetR(c, p, [rr EXCEPT !.pc = "lus_schrijf",
                                              !.inh = rr.inh \cup InhoudVan(actief[p]),
                                              !.basis = <<actief[p], Huidig(actief[p])>>,
                                              !.n = rr.n + 1])
    /\ UNCHANGED <<actief, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* na_samenvoegen(): eigen inhoud gelijk aan het resultaat: gewoon opnemen. Anders: ons naar .openaec/eigen,
\* de toestand "afgeleid van S" meteen bewaren, en het resultaat via de veilige routine plaatsen.
NaSamenvoegen(c, p) ==
    /\ r[c][p].pc = "na_samenvoegen"
    /\ LET rr == r[c][p]
           nieuw == [b |-> rr.basis[1], v |-> rr.basis[2], k |-> rr.ons, afg |-> Geen]
       IN IF rr.inh = rr.ons
          THEN /\ mem' = Zet(mem, c, p, nieuw)
               /\ ZetR(c, p, [rr EXCEPT !.pc = "einde"])
               /\ UNCHANGED <<bekend, elders>>
          ELSE LET afgeleid == [nieuw EXCEPT !.afg = [s |-> rr.ons, van |-> rr.van]] IN
               /\ elders' = elders \cup rr.ons
               /\ mem' = Zet(mem, c, p, afgeleid)
               /\ bekend' = Zet(bekend, c, p, afgeleid)
               /\ ZetR(c, p, [rr EXCEPT !.pc = "lus_plaats", !.tmp = rr.inh, !.sv = [b |-> rr.basis[1], v |-> rr.basis[2]]])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bron, volgend, gewist, nVerwijder, nStoring>>

\* vervang_hier(pad, resultaat): eerst naar de bron, dan plaatsen zonder vervangen (zoals DownloadZet).
LusPlaats(c, p) ==
    /\ r[c][p].pc = "lus_plaats"
    /\ VerplaatsNaarBron(c, p, "vervang", "download_zet")
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, buf, bekend, mem, elders, volgend, gewist, nVerwijder, nStoring>>

\* als_kopie(): het lokale bestand van nu als conflictkopie (upload zonder basis), dan na_upload.
AlsKopie(c, p) ==
    /\ r[c][p].pc = "als_kopie"
    /\ IF lok[c][p] = Geen
          THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
          ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "upload_stuur", !.pl = lok[c][p], !.basis = Geen, !.bb = Geen])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Einde van de ronde: bronnen afronden (5a/5b), toestand bewaren          *)
(***************************************************************************)

\* rond_af(): gelijk aan wat verwerkt werd: weg. Een verwijdering die afwijkt: terug op het pad als dat vrij is.
\* Anders lever_in(): een upload (vervanging: met de verwerkte versie als basis; verwijdering: zonder basis).
\* Gelukt: de bron gaat weg, en alleen bij een conflict of een verwijdering komt de inhoud hier op het pad van het
\* resultaat, als daar niets staat (dan ook in de toestand). Niet gelukt (netwerk): de bron blijft staan.
Einde(c, p) ==
    /\ r[c][p].pc = "einde"
    /\ LET bb == bron[c][p] IN
       CASE bb = Geen ->
                 UNCHANGED <<actief, versies, gebruikt, kopieVan, lok, bron, mem, bekend, nStoring>>
         [] bb.inhoud = bb.verwacht ->
                 /\ bron' = Zet(bron, c, p, Geen)
                 /\ UNCHANGED <<actief, versies, gebruikt, kopieVan, lok, mem, bekend, nStoring>>
         [] bb.soort = "verwijder" /\ lok[c][p] = Geen ->
                 /\ lok' = Zet(lok, c, p, bb.inhoud)
                 /\ bron' = Zet(bron, c, p, Geen)
                 /\ UNCHANGED <<actief, versies, gebruikt, kopieVan, mem, bekend, nStoring>>
         [] OTHER ->
                 LET basis == IF bb.soort = "verwijder" THEN Geen ELSE bb.basis
                     basisB == IF bb.soort = "verwijder" THEN Geen ELSE bb.bb
                     res == UploadResultaat(p, basis, basisB)
                     doel == IF res.soort = "kopie" THEN res.kp ELSE p
                     plaatsen == res.soort = "kopie" \/ bb.soort = "verwijder"
                     n == [b |-> res.b, v |-> res.v, k |-> bb.inhoud, afg |-> Geen]
                 IN /\ Uitvoerbaar(p, basis, basisB)
                    /\ \E u \in Uitkomsten :
                         /\ Storing(u)
                         /\ IF u = "verzoek_weg"
                               THEN UNCHANGED <<actief, versies, gebruikt, kopieVan>>
                               ELSE ServerUpload(c, p, basis, basisB, bb.inhoud)
                         /\ IF u = "ok"
                               THEN /\ bron' = Zet(bron, c, p, Geen)
                                    /\ IF plaatsen /\ lok[c][doel] = Geen
                                          THEN /\ lok' = Zet(lok, c, doel, bb.inhoud)
                                               /\ mem' = Zet(mem, c, doel, n)
                                               /\ bekend' = IF doel = p THEN bekend ELSE Zet(bekend, c, doel, n)
                                          ELSE UNCHANGED <<lok, mem, bekend>>
                               ELSE UNCHANGED <<bron, lok, mem, bekend>>
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "bewaar"])
    /\ UNCHANGED <<prul, buf, elders, volgend, gewist, nVerwijder>>

Bewaar(c, p) ==
    /\ r[c][p].pc = "bewaar"
    /\ bekend' = Zet(bekend, c, p, mem[c][p])
    /\ ZetR(c, p, RustR)
    /\ UNCHANGED <<actief, versies, gebruikt, prul, kopieVan, lok, buf, mem, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Alles samen                                                             *)
(***************************************************************************)

Stap(c, p) ==
    \/ Lijst(c, p) \/ Beslis(c, p)
    \/ UploadLees(c, p) \/ UploadStuur(c, p) \/ UploadHerstel(c, p) \/ NaUpload(c, p)
    \/ DownloadHaal(c, p) \/ DownloadPlaatsMain(c, p) \/ DownloadVerplaats(c, p) \/ DownloadZet(c, p)
    \/ VerwijderLokaalMain(c, p) \/ VerwijderLokaalVeilig(c, p) \/ VerwijderServer(c, p)
    \/ LusStart(c, p) \/ LusSchrijf(c, p) \/ NaSamenvoegen(c, p) \/ LusPlaats(c, p) \/ AlsKopie(c, p)
    \/ Einde(c, p) \/ Bewaar(c, p)

Gebruiker(c, p) == Bewerk(c, p) \/ Open(c, p) \/ BewerkBron(c, p) \/ VerwijderDoorGebruiker(c, p)

Next ==
    \/ \E c \in Clients, p \in AllePaden : Stap(c, p)
    \/ \E c \in Clients, p \in Paden : Gebruiker(c, p)

Spec == Init /\ [][Next]_vars
FairSpec == Spec /\ \A c \in Clients, p \in AllePaden : WF_vars(Stap(c, p))

(***************************************************************************)
(* Eigenschappen                                                           *)
(***************************************************************************)

Gemaakt == 1..(volgend - 1)
Unie(S) == UNION S

LokaalAlles == Unie({IF lok[c][p] = Geen THEN {} ELSE lok[c][p] : c \in Clients, p \in AllePaden})
          \cup Unie({IF bron[c][p] = Geen THEN {} ELSE bron[c][p].inhoud : c \in Clients, p \in AllePaden})
          \cup elders
ServerHuidig == Unie({IF actief[p] = Geen THEN {} ELSE InhoudVan(actief[p]) : p \in AllePaden})
ServerAlles == Unie({Unie({versies[b][i] : i \in 1..Huidig(b)}) : b \in gebruikt})

\* P1. Geen opgeslagen bewerking verdwijnt helemaal: ze staat op de server (ook in een oude versie of de
\* prullenbak), lokaal, of de gebruiker wiste haar zelf.
GeenVernietiging == \A a \in Gemaakt : a \in ServerAlles \cup LokaalAlles \cup gewist

\* P2. Geen stil verlies: elke bewerking staat in de huidige versie van een actief bestand (ook een
\* conflictkopie), of lokaal (ook in een bron of .openaec/eigen), of de gebruiker wiste haar zelf. Alleen in een
\* oude versie of in de prullenbak telt als verloren zonder melding.
GeenStilVerlies == \A a \in Gemaakt : a \in ServerHuidig \cup LokaalAlles \cup gewist

\* P3. Een nieuwe versie van een bestand verliest geen bewerking van de vorige versie (geen lost update),
\* tenzij een gebruiker die bewerking zelf wiste (bijv. het bestand verwijderd en opnieuw gemaakt).
GeenLostUpdate ==
    [][\A b \in gebruikt : (Huidig(b) > 0 /\ Huidig(b)' > Huidig(b)) => (InhoudVan(b) \ gewist) \subseteq InhoudVan(b)']_vars

\* P4 (levendheid). Als het netwerk weer werkt en niemand meer iets doet, komen alle clients op de stand van
\* de server (op een kopiepad: de client die haar maakte), en staat elke bewerking op de server (huidig of in
\* een conflictkopie), tenzij bewust gewist.
GelijkeStand ==
    \A c \in Clients, p \in AllePaden :
        /\ bron[c][p] = Geen
        /\ (p \in Paden \/ kopieVan[p] = c) =>
              lok[c][p] = (IF actief[p] = Geen THEN Geen ELSE InhoudVan(actief[p]))
Convergentie == <>[](GelijkeStand /\ \A a \in Gemaakt : a \in ServerHuidig \cup gewist)

=============================================================================
