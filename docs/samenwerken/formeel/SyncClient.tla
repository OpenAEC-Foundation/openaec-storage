----------------------------- MODULE SyncClient -----------------------------
(***************************************************************************)
(* De desktop-sync (crate openaec-sync, motor.rs) tegen de server, met     *)
(* gebruikers die bestanden wijzigen en verwijderen, en een netwerk dat    *)
(* verzoeken en antwoorden kwijtraakt.                                     *)
(*                                                                         *)
(* Clientvarianten (constante Client):                                     *)
(*  "main": openaec-cloud-desktop main a3aceac. Na een upload onthoudt de  *)
(*          toestand het kenmerk van NA de upload (onthoud()); een         *)
(*          download zet het tijdelijke bestand met rename over het pad;   *)
(*          VerwijderLokaal doet remove_file zonder nieuwe controle.       *)
(*  "5a"  : PR #8 (samenwerken/5a-veilig-vervangen, f0d144c): het kenmerk  *)
(*          van vóór de upload; elke lokale vervanging en verwijdering     *)
(*          gaat via vervang.rs: het bestand eerst naar een bron, dan      *)
(*          plaatsen zonder te vervangen, en aan het eind van de ronde de  *)
(*          bron controleren (gelijk: weg; anders: naar de server).        *)
(*  "5b"  : 5a plus de opslaan-lus voor IFC-bestanden (branch              *)
(*          samenwerken/5b-samenvoegen, db66178; nog zonder PR):           *)
(*          momentopname, sla_op() met bij_conflict=weiger, samenvoegen,   *)
(*          "afgeleid van S" in de toestand.                               *)
(*                                                                         *)
(* De server is hier atomair (de binnenkant staat in UploadServer.tla):    *)
(*  - een gewone upload vergelijkt alleen het versienummer (basis_versie); *)
(*    is het geen gewone nieuwe versie, dan een conflictkopie (of een      *)
(*    nieuw bestand als er niets op de plek staat);                        *)
(*  - schrijf_als (alleen 5b, server van PR #9) vergelijkt de versieregel  *)
(*    (basis_bestand + basis_versie_id) en weigert bij een andere;         *)
(*  - verwijder(bestand) zet het bestand in de prullenbak, zonder basis.   *)
(*                                                                         *)
(* Inhoud: een verzameling "bewerkingen" (atomen). Een gebruiker voegt bij *)
(* elke opslag een nieuw atoom toe. Samenvoegen zonder conflict geeft de   *)
(* vereniging (dat is het contract dat de property-based tests van         *)
(* openaec-samenvoegen toetsen); samenvoegen kan ook altijd een conflict   *)
(* geven (vrije keuze). Het kenmerk (grootte + wijzigingstijd) is hier de  *)
(* inhoud zelf.                                                            *)
(*                                                                         *)
(* Per client en pad loopt één ronde tegelijk; paden zijn onafhankelijk    *)
(* (geen verplaatsen). Een ronde: lijst, scan + beslissen, de taak, de     *)
(* bronnen afronden (5a/5b), de toestand bewaren.                          *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Clients, Paden,
    IfcPaden,       \* paden die de opslaan-lus nemen (alleen bij Client = "5b")
    Bestanden,      \* ids voor Opslagbestand
    Client,         \* "main", "5a" of "5b"
    MaxAtomen,      \* zoveel keer slaat een gebruiker in totaal op
    MaxVerwijder,   \* zoveel keer verwijdert een gebruiker in totaal een bestand
    MaxStoring,     \* zoveel verzoeken of antwoorden raakt het netwerk in totaal kwijt
    MaxPogingen,    \* sla_op: zo vaak schrijven (standaard 5)
    AppBuffer,      \* TRUE: een programma houdt de inhoud open en schrijft bij opslaan zijn eigen kopie terug
    Fixes,          \* voorgestelde reparaties die aan staan (zie het rapport), deelverzameling van FixNamen
    Geen

\* "verwijder_basis": verwijder(bestand, basis): de server weigert als de huidige versie niet de basis is.
FixNamen == {"verwijder_basis"}
ASSUME Fixes \subseteq FixNamen

ASSUME Client \in {"main", "5a", "5b"}

VARIABLES
    \* server
    actief,     \* [Paden -> Bestanden \cup {Geen}]
    versies,    \* [Bestanden -> Seq(inhoud)]
    gebruikt,   \* bestand-ids in gebruik
    prul,       \* bestanden in de prullenbak
    opKopie,    \* atomen in conflictkopieën op de server
    \* clients
    lok,        \* [Clients -> [Paden -> inhoud \cup {Geen}]]: het bestand op de schijf
    buf,        \* [Clients -> [Paden -> inhoud \cup {Geen}]]: wat het programma in het geheugen heeft (AppBuffer)
    bekend,     \* [Clients -> [Paden -> Geen \cup [b, v, k, afg]]]: de bewaarde toestand (staat.json)
    mem,        \* idem, de toestand in het geheugen tijdens de ronde
    bron,       \* [Clients -> [Paden -> Geen \cup [inhoud, verwacht, basis]]]: .openaec/bron (5a/5b)
    r,          \* [Clients -> [Paden -> rondetoestand]]
    elders,     \* atomen die lokaal apart bewaard zijn (lokale conflictkopieën, .openaec/eigen)
    \* gebruikers en netwerk
    volgend,    \* het volgende atoom
    gewist,     \* atomen die een gebruiker zelf wiste (bewust)
    nVerwijder, nStoring

vars == <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, mem, bron, r, elders,
          volgend, gewist, nVerwijder, nStoring>>

Atomen == 1..MaxAtomen
Inhoud == SUBSET Atomen

RustR == [pc |-> "rust", sv |-> Geen, pl |-> Geen, tmp |-> Geen, ons |-> Geen, inh |-> Geen,
          basis |-> Geen, n |-> 0, van |-> 0]

Init ==
    /\ actief = [p \in Paden |-> Geen]
    /\ versies = [b \in Bestanden |-> << >>]
    /\ gebruikt = {}
    /\ prul = {}
    /\ opKopie = {}
    /\ lok = [c \in Clients |-> [p \in Paden |-> Geen]]
    /\ buf = [c \in Clients |-> [p \in Paden |-> Geen]]
    /\ bekend = [c \in Clients |-> [p \in Paden |-> Geen]]
    /\ mem = [c \in Clients |-> [p \in Paden |-> Geen]]
    /\ bron = [c \in Clients |-> [p \in Paden |-> Geen]]
    /\ r = [c \in Clients |-> [p \in Paden |-> RustR]]
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

(***************************************************************************)
(* Server                                                                  *)
(***************************************************************************)

\* Een gewone upload (bij_conflict=kopie) met basis op nummer. Resultaat: [soort, b, v].
\* soort: "versie" (nieuwe versie van het bestand op de plek), "kopie" (conflictkopie), "nieuw" (nieuw bestand).
UploadUitkomst(p, basisNr) ==
    IF actief[p] # Geen
       THEN IF basisNr # Geen /\ basisNr = Huidig(actief[p]) THEN "versie" ELSE "kopie"
       ELSE "nieuw"

\* De servertoestand na een gewone upload van `inh` op pad p; b is het nieuwe id als er een nieuw bestand komt.
ServerUpload(p, basisNr, inh, nieuwId) ==
    LET s == UploadUitkomst(p, basisNr) IN
    CASE s = "versie" -> /\ versies' = [versies EXCEPT ![actief[p]] = Append(@, inh)]
                         /\ UNCHANGED <<actief, gebruikt, opKopie>>
      [] s = "kopie"  -> /\ opKopie' = opKopie \cup inh
                         /\ UNCHANGED <<actief, versies, gebruikt>>
      [] s = "nieuw"  -> /\ actief' = [actief EXCEPT ![p] = nieuwId]
                         /\ versies' = [versies EXCEPT ![nieuwId] = <<inh>>]
                         /\ gebruikt' = gebruikt \cup {nieuwId}
                         /\ UNCHANGED opKopie

UploadResultaat(p, basisNr, nieuwId) ==
    LET s == UploadUitkomst(p, basisNr) IN
    CASE s = "versie" -> [soort |-> s, b |-> actief[p], v |-> Huidig(actief[p]) + 1]
      [] s = "kopie"  -> [soort |-> s, b |-> Geen, v |-> 0]
      [] s = "nieuw"  -> [soort |-> s, b |-> nieuwId, v |-> 1]

\* schrijf_als (PR #9, bij_conflict=weiger, basis_bestand + basis_versie_id): "gelijk", "verouderd" of "onbekend".
SchrijfAlsUitkomst(p, basis) ==
    IF actief[p] = Geen \/ basis[1] # actief[p] THEN "onbekend"
    ELSE IF basis[2] = Huidig(actief[p]) THEN "gelijk" ELSE "verouderd"

(***************************************************************************)
(* Gebruikers                                                              *)
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
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, bekend, mem, bron, r, elders, gewist, nVerwijder, nStoring>>

\* Het programma opent het bestand opnieuw (leest de schijf).
Open(c, p) ==
    /\ AppBuffer
    /\ lok[c][p] # Geen
    /\ buf[c][p] # lok[c][p]
    /\ buf' = Zet(buf, c, p, lok[c][p])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, bekend, mem, bron, r, elders, volgend, gewist, nVerwijder, nStoring>>

\* De gebruiker verwijdert het bestand bewust; wat erin stond, wist hij zelf.
VerwijderDoorGebruiker(c, p) ==
    /\ lok[c][p] # Geen
    /\ nVerwijder < MaxVerwijder
    /\ gewist' = gewist \cup lok[c][p]
    /\ lok' = Zet(lok, c, p, Geen)
    /\ IF AppBuffer THEN buf' = Zet(buf, c, p, Geen) ELSE UNCHANGED buf
    /\ nVerwijder' = nVerwijder + 1
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, bekend, mem, bron, r, elders, volgend, nStoring>>

(***************************************************************************)
(* De ronde                                                                *)
(***************************************************************************)

Basis(k) == IF k.afg = Geen THEN k.v ELSE k.afg.van     \* Bekend::basis() (5b)

\* lijst(): de server zoals hij nu is; de toestand van de vorige ronde gaat het geheugen in.
Lijst(c, p) ==
    /\ r[c][p].pc = "rust"
    /\ ZetR(c, p, [RustR EXCEPT !.pc = "scan", !.sv = Zicht(p)])
    /\ mem' = Zet(mem, c, p, bekend[c][p])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

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
       IN CASE taak = "onthoud" ->
                 /\ mem' = Zet(mem, c, p, [b |-> sv.b, v |-> sv.v, k |-> sk, afg |-> Geen])
                 /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
            [] taak = "vergeet" ->
                 /\ mem' = Zet(mem, c, p, Geen)
                 /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
            [] OTHER ->
                 /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = taak, !.basis = basisNr])
                 /\ UNCHANGED mem
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Upload (gewoon, met conflictkopie)                                      *)
(***************************************************************************)

\* upload_poging(): de inhoud lezen (hashes en delen; hier één moment).
UploadLees(c, p) ==
    /\ r[c][p].pc = "upload"
    /\ IF lok[c][p] = Geen
          THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])     \* bestand weg: fout, volgende ronde
          ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "upload_stuur", !.pl = lok[c][p]])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* upload_start + delen + upload_klaar als één verzoek aan de server.
UploadStuur(c, p) ==
    /\ r[c][p].pc = "upload_stuur"
    /\ \E u \in Uitkomsten : LET id == NieuwId IN
         /\ (UploadUitkomst(p, r[c][p].basis) = "nieuw" => id # Geen)
         /\ Storing(u)
         /\ IF u = "verzoek_weg"
               THEN UNCHANGED <<actief, versies, gebruikt, opKopie>>
               ELSE ServerUpload(p, r[c][p].basis, r[c][p].pl, id)
         /\ IF u = "ok"
               THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "na_upload", !.tmp = UploadResultaat(p, r[c][p].basis, id)])
               ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<prul, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* na_upload(): opnemen, of bij een conflict het lokale bestand opzij en de serverversie ophalen.
NaUpload(c, p) ==
    /\ r[c][p].pc = "na_upload"
    /\ LET res == r[c][p].tmp IN
       IF res.soort = "kopie"
          THEN \* hernoemen naar de conflictkopie (een nieuw pad: altijd vrij); de inhoud blijft lokaal bewaard
               /\ elders' = elders \cup (IF lok[c][p] = Geen THEN {} ELSE lok[c][p])
               /\ lok' = Zet(lok, c, p, Geen)
               /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = IF r[c][p].sv = Geen THEN "einde" ELSE "download", !.tmp = Geen])
               /\ UNCHANGED mem
          ELSE \* main: het kenmerk van nu; 5a/5b: het kenmerk van vlak vóór de upload (verstuurd)
               /\ mem' = Zet(mem, c, p, [b |-> res.b, v |-> res.v, afg |-> Geen,
                                         k |-> IF Client = "main" THEN lok[c][p] ELSE r[c][p].pl])
               /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
               /\ UNCHANGED <<lok, elders>>
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, bron, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Download                                                                *)
(***************************************************************************)

\* download_poging(): de huidige inhoud van het bestand uit de lijst (op id; ook uit de prullenbak).
DownloadHaal(c, p) ==
    /\ r[c][p].pc = "download"
    /\ \E u \in Uitkomsten :
         /\ Storing(u)
         /\ IF u = "ok"
               THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "download_plaats", !.tmp = InhoudVan(r[c][p].sv.b)])
               ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

\* main: rename van het tijdelijke bestand over het pad; wat er nu staat, is weg.
DownloadPlaatsMain(c, p) ==
    /\ Client = "main"
    /\ r[c][p].pc = "download_plaats"
    /\ lok' = Zet(lok, c, p, r[c][p].tmp)
    /\ mem' = Zet(mem, c, p, [b |-> r[c][p].sv.b, v |-> r[c][p].sv.v, k |-> r[c][p].tmp, afg |-> Geen])
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* 5a/5b, vervang.rs stap 1: het lokale bestand naar de bron (één rename). Verwacht: wat de toestand kent.
\* soort: "vervang" of "verwijder". Ligt er nog een bron van een eerdere ronde voor dit pad, dan wacht deze
\* vervanging tot een volgende ronde (vereenvoudiging: in de code kunnen meer bronnen naast elkaar staan).
VerplaatsNaarBron(c, p, soort, volgende) ==
    IF bron[c][p] # Geen
       THEN /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
            /\ UNCHANGED <<lok, bron>>
       ELSE /\ IF lok[c][p] = Geen
                  THEN UNCHANGED <<lok, bron>>
                  ELSE /\ bron' = Zet(bron, c, p, [inhoud |-> lok[c][p], soort |-> soort,
                                                   verwacht |-> IF mem[c][p] = Geen THEN Geen ELSE mem[c][p].k,
                                                   basis |-> IF mem[c][p] = Geen THEN Geen ELSE Basis(mem[c][p])])
                       /\ lok' = Zet(lok, c, p, Geen)
            /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = volgende])

DownloadVerplaats(c, p) ==
    /\ Client \in {"5a", "5b"}
    /\ r[c][p].pc = "download_plaats"
    /\ VerplaatsNaarBron(c, p, "vervang", "download_zet")
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, mem, elders, volgend, gewist, nVerwijder, nStoring>>

\* vervang.rs stap 2: plaatsen zonder te vervangen. Staat er intussen een bestand, dan blijft dat staan.
DownloadZet(c, p) ==
    /\ r[c][p].pc = "download_zet"
    /\ IF lok[c][p] = Geen
          THEN /\ lok' = Zet(lok, c, p, r[c][p].tmp)
               /\ mem' = Zet(mem, c, p, [b |-> r[c][p].sv.b, v |-> r[c][p].sv.v, k |-> r[c][p].tmp, afg |-> Geen])
          ELSE UNCHANGED <<lok, mem>>
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Verwijderen                                                             *)
(***************************************************************************)

\* Op de server verwijderd en lokaal ongewijzigd. main: remove_file zonder nieuwe controle.
VerwijderLokaalMain(c, p) ==
    /\ Client = "main"
    /\ r[c][p].pc = "verwijder_lokaal"
    /\ lok' = Zet(lok, c, p, Geen)
    /\ mem' = Zet(mem, c, p, Geen)
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, bron, elders, volgend, gewist, nVerwijder, nStoring>>

\* 5a/5b: via de bron; de toestand vergeet het pad.
VerwijderLokaalVeilig(c, p) ==
    /\ Client \in {"5a", "5b"}
    /\ r[c][p].pc = "verwijder_lokaal"
    /\ VerplaatsNaarBron(c, p, "verwijder", "einde")
    /\ mem' = Zet(mem, c, p, Geen)
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, elders, volgend, gewist, nVerwijder, nStoring>>

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
                          /\ actief' = [q \in Paden |-> IF actief[q] = b THEN Geen ELSE actief[q]]
               /\ IF u = "ok" /\ ~geweigerd THEN mem' = Zet(mem, c, p, Geen) ELSE UNCHANGED mem
         /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
    /\ UNCHANGED <<versies, gebruikt, opKopie, lok, buf, bekend, bron, elders, volgend, gewist, nVerwijder>>

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
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

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
    /\ UNCHANGED <<actief, gebruikt, prul, opKopie, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder>>

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
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bron, volgend, gewist, nVerwijder, nStoring>>

\* vervang_hier(pad, resultaat): eerst naar de bron, dan plaatsen zonder vervangen (zoals DownloadZet).
LusPlaats(c, p) ==
    /\ r[c][p].pc = "lus_plaats"
    /\ VerplaatsNaarBron(c, p, "vervang", "download_zet")
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, buf, bekend, mem, elders, volgend, gewist, nVerwijder, nStoring>>

\* als_kopie(): het lokale bestand van nu als conflictkopie (upload zonder basis), dan na_upload.
AlsKopie(c, p) ==
    /\ r[c][p].pc = "als_kopie"
    /\ IF lok[c][p] = Geen
          THEN ZetR(c, p, [r[c][p] EXCEPT !.pc = "einde"])
          ELSE ZetR(c, p, [r[c][p] EXCEPT !.pc = "upload_stuur", !.pl = lok[c][p], !.basis = Geen])
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, bekend, mem, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Einde van de ronde: bronnen afronden (5a/5b), toestand bewaren          *)
(***************************************************************************)

\* rond_af(): gelijk aan wat verwerkt werd: weg. Anders: lever_in() (een upload met de verwerkte versie
\* als basis: meestal een conflictkopie), of bij een verwijdering terug op het pad als dat vrij is.
\* Lukt de upload niet (netwerk), dan blijft de bron staan voor een volgende ronde.
Einde(c, p) ==
    /\ r[c][p].pc = "einde"
    /\ LET bb == bron[c][p]
           id == NieuwId
       IN CASE bb = Geen ->
                 UNCHANGED <<actief, versies, gebruikt, opKopie, lok, bron, elders, nStoring>>
            [] bb.inhoud = bb.verwacht ->                      \* onverwerkt niets: de bron mag weg
                 /\ bron' = Zet(bron, c, p, Geen)
                 /\ UNCHANGED <<actief, versies, gebruikt, opKopie, lok, elders, nStoring>>
            [] bb.soort = "verwijder" /\ lok[c][p] = Geen ->   \* terug op het pad, ook offline
                 /\ lok' = Zet(lok, c, p, bb.inhoud)
                 /\ bron' = Zet(bron, c, p, Geen)
                 /\ UNCHANGED <<actief, versies, gebruikt, opKopie, elders, nStoring>>
            [] OTHER ->                                         \* lever_in(): naar de server
                 LET basis == IF bb.soort = "verwijder" THEN Geen ELSE bb.basis IN
                 \E u \in Uitkomsten :
                    /\ (UploadUitkomst(p, basis) = "nieuw" => id # Geen)
                    /\ Storing(u)
                    /\ IF u = "verzoek_weg"
                          THEN UNCHANGED <<actief, versies, gebruikt, opKopie>>
                          ELSE ServerUpload(p, basis, bb.inhoud, id)
                    /\ IF u = "ok"
                          THEN /\ bron' = Zet(bron, c, p, Geen)
                               /\ elders' = elders \cup bb.inhoud     \* en lokaal naast het bestand
                          ELSE UNCHANGED <<bron, elders>>
                    /\ UNCHANGED lok
    /\ ZetR(c, p, [r[c][p] EXCEPT !.pc = "bewaar"])
    /\ UNCHANGED <<prul, buf, bekend, mem, volgend, gewist, nVerwijder>>

Bewaar(c, p) ==
    /\ r[c][p].pc = "bewaar"
    /\ bekend' = Zet(bekend, c, p, mem[c][p])
    /\ ZetR(c, p, RustR)
    /\ UNCHANGED <<actief, versies, gebruikt, prul, opKopie, lok, buf, mem, bron, elders, volgend, gewist, nVerwijder, nStoring>>

(***************************************************************************)
(* Alles samen                                                             *)
(***************************************************************************)

Stap(c, p) ==
    \/ Lijst(c, p) \/ Beslis(c, p)
    \/ UploadLees(c, p) \/ UploadStuur(c, p) \/ NaUpload(c, p)
    \/ DownloadHaal(c, p) \/ DownloadPlaatsMain(c, p) \/ DownloadVerplaats(c, p) \/ DownloadZet(c, p)
    \/ VerwijderLokaalMain(c, p) \/ VerwijderLokaalVeilig(c, p) \/ VerwijderServer(c, p)
    \/ LusStart(c, p) \/ LusSchrijf(c, p) \/ NaSamenvoegen(c, p) \/ LusPlaats(c, p) \/ AlsKopie(c, p)
    \/ Einde(c, p) \/ Bewaar(c, p)

Gebruiker(c, p) == Bewerk(c, p) \/ Open(c, p) \/ VerwijderDoorGebruiker(c, p)

Next == \E c \in Clients, p \in Paden : Stap(c, p) \/ Gebruiker(c, p)

Spec == Init /\ [][Next]_vars
FairSpec == Spec /\ \A c \in Clients, p \in Paden : WF_vars(Stap(c, p))

(***************************************************************************)
(* Eigenschappen                                                           *)
(***************************************************************************)

Gemaakt == 1..(volgend - 1)
Unie(S) == UNION S

LokaalAlles == Unie({IF lok[c][p] = Geen THEN {} ELSE lok[c][p] : c \in Clients, p \in Paden})
          \cup Unie({IF bron[c][p] = Geen THEN {} ELSE bron[c][p].inhoud : c \in Clients, p \in Paden})
          \cup elders
ServerHuidig == Unie({IF actief[p] = Geen THEN {} ELSE InhoudVan(actief[p]) : p \in Paden}) \cup opKopie
ServerAlles == Unie({Unie({versies[b][i] : i \in 1..Huidig(b)}) : b \in gebruikt}) \cup opKopie

\* P1. Geen opgeslagen bewerking verdwijnt helemaal: ze staat op de server (ook in een oude versie of de
\* prullenbak), lokaal, of de gebruiker wiste haar zelf.
GeenVernietiging == \A a \in Gemaakt : a \in ServerAlles \cup LokaalAlles \cup gewist

\* P2. Geen stil verlies: elke bewerking staat in de huidige versie van een actief bestand, in een
\* conflictkopie, of lokaal (ook in een bron of .openaec/eigen), of de gebruiker wiste haar zelf. Alleen in een
\* oude versie of in de prullenbak telt als verloren zonder melding.
GeenStilVerlies == \A a \in Gemaakt : a \in ServerHuidig \cup LokaalAlles \cup gewist

\* P3. Een nieuwe versie van een bestand verliest geen bewerking van de vorige versie (geen lost update),
\* tenzij een gebruiker die bewerking zelf wiste (bijv. het bestand verwijderd en opnieuw gemaakt).
GeenLostUpdate ==
    [][\A b \in gebruikt : (Huidig(b) > 0 /\ Huidig(b)' > Huidig(b)) => (InhoudVan(b) \ gewist) \subseteq InhoudVan(b)']_vars

\* P4 (levendheid). Als het netwerk weer werkt en niemand meer iets doet, komen alle clients op de stand van
\* de server, en staat elke bewerking op de server (huidig of in een conflictkopie), tenzij bewust gewist.
Rustig == volgend > MaxAtomen
GelijkeStand ==
    \A c \in Clients, p \in Paden :
        /\ bron[c][p] = Geen
        /\ lok[c][p] = (IF actief[p] = Geen THEN Geen ELSE InhoudVan(actief[p]))
Convergentie == <>[](GelijkeStand /\ \A a \in Gemaakt : a \in ServerHuidig \cup gewist)

=============================================================================
