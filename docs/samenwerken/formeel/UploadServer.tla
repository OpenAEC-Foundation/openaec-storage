---------------------------- MODULE UploadServer ----------------------------
(***************************************************************************)
(* De server bij een upload: upload_start, upload_klaar en _registreer,    *)
(* voor één plek (map + naam) in een ruimte.                               *)
(*                                                                         *)
(* Bronnen:                                                                *)
(*  - main e87b870, openaec_storage/api.py: upload_klaar() leest het token *)
(*    en roept _registreer() aan in de transactie van het verzoek;         *)
(*    _registreer() vergelijkt alleen basis_versie met huidige_versie,     *)
(*    zonder slot; het token gaat weg vóór de commit (aan het eind van het *)
(*    verzoek).                                                            *)
(*  - PR #9 (samenwerken/vergelijk-en-schrijf), api.py: upload_klaar() met *)
(*    een_afronding() (get_lock per token) en _rond_upload_af(): voltooi   *)
(*    in S3, dan _eigen_transactie() + _registreer() onder sloten; bij een *)
(*    fout _ruim_upload_op(sleutel) en het token weg; na de commit het     *)
(*    token weg. _registreer() vergelijkt met basis_versie_id en           *)
(*    basis_bestand als die meekomen, anders op het nummer.                *)
(*                                                                         *)
(* Clients zijn hier vrij: elke upload kiest een soort en een basis. Zo    *)
(* dekt het model oude clients, de gewone upload van de desktop-sync (op   *)
(* nummer, met conflictkopie) en de opslaan-lus (bij_conflict=weiger, met  *)
(* basis_bestand en basis_versie_id).                                      *)
(*                                                                         *)
(* Inhoud: elke upload heeft een eigen S3-sleutel; die sleutel is hier de  *)
(* inhoud.                                                                 *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Variant,          \* "main" of "pr9"
    Isolatie,         \* bij "main": "RR" of "RC" (zie ontwerp §2)
    Tokens,           \* ids van uploads (en dus van S3-sleutels)
    Bestanden,        \* ids van Opslagbestand (gewone bestanden en conflictkopieën)
    MaxKlaar,         \* zo vaak kan upload_klaar voor één token binnenkomen
    MaxUitval,        \* zo vaak valt een worker uit tussen commit en het wissen van het token
    MaxVerwijder,     \* zo vaak verwijdert iemand het bestand op de plek
    VoltooiHerhaalbaar, \* geeft complete_multipart_upload een tweede keer "gelukt"?
    Soorten,          \* welke soorten clients uploaden: deelverzameling van {"nummer", "lus", "geen"}
    Geen

ASSUME Variant \in {"main", "pr9"}
ASSUME Isolatie \in {"RR", "RC"}

VARIABLES
    opPlek,       \* de actieve (niet verwijderde) gewone bestanden met deze naam; hoort er hoogstens één te zijn
    versies,      \* [Bestanden -> Seq([sleutel, basis, conflict])]: de versieregels
    gebruikt,     \* bestand-ids die al bestaan
    kopie,        \* [Bestanden -> BOOLEAN]: dit bestand is een conflictkopie
    tok,          \* [Tokens -> [st, soort, bn, bb, bv]]; st: "nog" | "open" | "weg"
    afr,          \* [Tokens -> [n, fase, ...]]: lopende afrondingen (main: twee stappen)
    s3weg,        \* sleutels die de server in S3 wiste
    uitval,       \* aantal uitvallen tot nu
    verwijderd    \* aantal verwijderingen tot nu

vars == <<opPlek, versies, gebruikt, kopie, tok, afr, s3weg, uitval, verwijderd>>

Vid(b, n) == <<b, n>>
Huidig(b) == Len(versies[b])
AlleVids == UNION {{Vid(b, n) : n \in 1..Huidig(b)} : b \in gebruikt}
VrijBestand == Bestanden \ gebruikt

Init ==
    /\ opPlek = {}
    /\ versies = [b \in Bestanden |-> << >>]
    /\ gebruikt = {}
    /\ kopie = [b \in Bestanden |-> FALSE]
    /\ tok = [t \in Tokens |-> [st |-> "nog", soort |-> Geen, bn |-> Geen, bb |-> Geen, bv |-> Geen]]
    /\ afr = [t \in Tokens |-> [n |-> 0, fase |-> "rust", zag |-> Geen, nr |-> 0]]
    /\ s3weg = {}
    /\ uitval = 0
    /\ verwijderd = 0

(***************************************************************************)
(* upload_start: een client kiest een basis. "nummer": alleen basis_versie *)
(* (oude client, of de gewone upload van de desktop-sync); de client werkte*)
(* echt op versie bv, maar stuurt alleen haar nummer. "lus": de opslaan-lus *)
(* met bij_conflict=weiger, basis_bestand en basis_versie_id (alleen pr9).  *)
(* "geen": geen basis (een nieuw bestand, of een conflictkopie).          *)
(***************************************************************************)
Start(t) ==
    /\ tok[t].st = "nog"
    /\ \E soort \in Soorten \cap (IF Variant = "pr9" THEN {"nummer", "lus", "geen"} ELSE {"nummer", "geen"}) :
       \E bv \in (IF soort = "geen" THEN {Geen} ELSE AlleVids) :
          tok' = [tok EXCEPT ![t] = [st |-> "open", soort |-> soort,
                                     bn |-> IF bv = Geen THEN Geen ELSE bv[2],
                                     bb |-> IF soort = "lus" THEN bv[1] ELSE Geen,
                                     bv |-> bv]]
    /\ UNCHANGED <<opPlek, versies, gebruikt, kopie, afr, s3weg, uitval, verwijderd>>

\* Een nieuwe versieregel. `basis` is de versie waarop de client echt werkte (voor de controle).
NieuweVersie(b, t, basis, isKopie) ==
    versies' = [versies EXCEPT ![b] = Append(@, [sleutel |-> t, basis |-> basis, conflict |-> isKopie])]

\* Een nieuw Opslagbestand (nieuw op de plek, of een conflictkopie ernaast).
NieuwBestand(t, basis, hier) ==
    \E b \in VrijBestand :
       /\ gebruikt' = gebruikt \cup {b}
       /\ kopie' = [kopie EXCEPT ![b] = ~hier]
       /\ NieuweVersie(b, t, basis, ~hier)
       /\ opPlek' = IF hier THEN opPlek \cup {b} ELSE opPlek

(***************************************************************************)
(* PR #9: _registreer onder sloten, atomair (de sloten op de bestandsregel  *)
(* en op de plek maken het één stap tegenover andere afrondingen).          *)
(* Uitkomst: "versie", "kopie", "nieuw" of "weiger".                        *)
(***************************************************************************)
Vergelijk(i, actief) ==   \* _vergelijk_basis; actief is het bestand dat _actief_bestand() vindt, of Geen
    IF actief = Geen \/ i.bv[1] # actief \/ (i.bb # Geen /\ i.bb # actief) THEN "onbekend"
    ELSE IF i.bv = Vid(actief, Huidig(actief)) THEN "gelijk" ELSE "verouderd"

Uitkomst9(i, actief) ==
    LET weiger == i.soort = "lus"
        heeftBasis == i.bn # Geen
        basis == IF i.soort = "lus" THEN Vergelijk(i, actief) ELSE Geen
    IN IF actief # Geen
       THEN LET zelfde == IF basis # Geen THEN basis # "onbekend" ELSE (i.bb = Geen \/ i.bb = actief)
                gelijk == IF basis # Geen THEN basis = "gelijk"
                          ELSE zelfde /\ heeftBasis /\ i.bn = Huidig(actief)
            IN IF gelijk THEN "versie" ELSE IF weiger THEN "weiger" ELSE "kopie"
       ELSE IF weiger THEN "weiger" ELSE "nieuw"

Registreer9(t, actief) ==
    LET i == tok[t] IN
    CASE Uitkomst9(i, actief) = "versie" -> NieuweVersie(actief, t, i.bv, FALSE) /\ UNCHANGED <<opPlek, gebruikt, kopie, s3weg>>
      [] Uitkomst9(i, actief) = "kopie"  -> NieuwBestand(t, i.bv, FALSE) /\ UNCHANGED s3weg
      [] Uitkomst9(i, actief) = "nieuw"  -> NieuwBestand(t, i.bv, TRUE) /\ UNCHANGED s3weg
      [] Uitkomst9(i, actief) = "weiger" -> s3weg' = s3weg \cup {t} /\ UNCHANGED <<opPlek, versies, gebruikt, kopie>>

\* _actief_bestand(): een van de actieve bestanden op de plek (onder pr9 is dat er hoogstens één).
Gevonden == IF opPlek = {} THEN {Geen} ELSE opPlek

\* upload_klaar(t) onder PR #9: get_lock per token, het token vers lezen, voltooien in S3, vastleggen,
\* en daarna het token wissen. Valt de worker uit na de commit, dan blijft het token staan.
Klaar9(t) ==
    /\ Variant = "pr9"
    /\ afr[t].n < MaxKlaar
    /\ tok[t].st \in {"open", "weg"}
    /\ afr' = [afr EXCEPT ![t].n = @ + 1]
    /\ IF tok[t].st = "weg" \/ (afr[t].n > 0 /\ ~VoltooiHerhaalbaar)
          THEN \* "Upload onbekend of verlopen", of S3 weigert een tweede voltooiing: niets gebeurt
               UNCHANGED <<opPlek, versies, gebruikt, kopie, tok, s3weg, uitval>>
          ELSE \E a \in Gevonden :
               /\ Registreer9(t, a)
               /\ \/ /\ tok' = [tok EXCEPT ![t].st = "weg"] /\ UNCHANGED uitval
                  \/ /\ Uitkomst9(tok[t], a) # "weiger"        \* uitval tussen commit en het wissen van het token
                     /\ uitval < MaxUitval
                     /\ uitval' = uitval + 1
                     /\ UNCHANGED tok
    /\ UNCHANGED <<verwijderd>>

(***************************************************************************)
(* main: _registreer in de transactie van het verzoek, zonder slot.        *)
(* Stap 1 leest het bestand op de plek en zijn huidige versie; stap 2      *)
(* schrijft. Onder RR weigert doc.save() met check_if_latest() als de      *)
(* bestandsregel intussen veranderde (TimestampMismatchError); een insert  *)
(* van een nieuw bestand wordt niet gecontroleerd.                         *)
(* Het token gaat weg vóór de commit, dus een tweede afronding die al las, *)
(* gaat gewoon door.                                                       *)
(***************************************************************************)
KlaarLees(t) ==
    /\ Variant = "main"
    /\ afr[t].fase = "rust"
    /\ afr[t].n < MaxKlaar
    /\ tok[t].st = "open"
    /\ \E a \in Gevonden :
          afr' = [afr EXCEPT ![t] = [n |-> @.n + 1, fase |-> "gelezen", zag |-> a,
                                     nr |-> IF a = Geen THEN 0 ELSE Huidig(a)]]
    /\ UNCHANGED <<opPlek, versies, gebruikt, kopie, tok, s3weg, uitval, verwijderd>>

KlaarSchrijf(t) ==
    /\ Variant = "main"
    /\ afr[t].fase = "gelezen"
    /\ LET i == tok[t]
           b == afr[t].zag
           versieVan == b # Geen /\ i.bn # Geen /\ i.bn = afr[t].nr
           veranderd == b # Geen /\ (b \notin opPlek \/ Huidig(b) # afr[t].nr)
       IN IF versieVan
             THEN IF Isolatie = "RR" /\ veranderd
                     THEN UNCHANGED <<opPlek, versies, gebruikt, kopie>>     \* TimestampMismatchError
                     ELSE NieuweVersie(b, t, i.bv, FALSE) /\ UNCHANGED <<opPlek, gebruikt, kopie>>
             ELSE NieuwBestand(t, i.bv, b = Geen)
    /\ tok' = [tok EXCEPT ![t].st = "weg"]
    /\ afr' = [afr EXCEPT ![t].fase = "rust"]
    /\ UNCHANGED <<s3weg, uitval, verwijderd>>

(***************************************************************************)
(* Iemand verwijdert het bestand op de plek (naar de prullenbak).          *)
(***************************************************************************)
Verwijder ==
    /\ verwijderd < MaxVerwijder
    /\ \E b \in opPlek : opPlek' = opPlek \ {b}
    /\ verwijderd' = verwijderd + 1
    /\ UNCHANGED <<versies, gebruikt, kopie, tok, afr, s3weg, uitval>>

Next ==
    \/ \E t \in Tokens : Start(t) \/ Klaar9(t) \/ KlaarLees(t) \/ KlaarSchrijf(t)
    \/ Verwijder

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Eigenschappen                                                           *)
(***************************************************************************)

Regels == UNION {{<<b, n>> : n \in 1..Huidig(b)} : b \in gebruikt}

\* S1. Geen versieregel wijst naar een S3-object dat de server zelf wiste.
InhoudBlijft == \A r \in Regels : versies[r[1]][r[2]].sleutel \notin s3weg

\* S2. Eén S3-object hoort bij hoogstens één versieregel (anders wist het opruimen van de ene de andere).
SleutelEenmaal ==
    \A r1, r2 \in Regels : (r1 # r2) => versies[r1[1]][r1[2]].sleutel # versies[r2[1]][r2[2]].sleutel

\* S3. Geen stille overschrijving: een nieuwe versie (geen conflictkopie) van bestand b is gemaakt op de
\* versie die toen de huidige van b was. De client werkte echt op `basis`.
GeenStilleOverschrijving ==
    \A r \in Regels :
        (r[2] >= 2) => versies[r[1]][r[2]].basis = Vid(r[1], r[2] - 1)

\* S4. Nooit twee actieve bestanden met dezelfde naam op de plek.
EenActief == Cardinality(opPlek) <= 1

=============================================================================
