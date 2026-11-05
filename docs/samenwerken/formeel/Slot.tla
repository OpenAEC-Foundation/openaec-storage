-------------------------------- MODULE Slot --------------------------------
(***************************************************************************)
(* Uitchecken van één bestand: vergrendel, ontgrendel, forceer_ontgrendel, *)
(* verlopen, en een upload van een ander (_registreer).                    *)
(*                                                                         *)
(* Bronnen (PR #9, samenwerken/vergelijk-en-schrijf, openaec_storage/      *)
(* api.py):                                                                *)
(*  - vergrendel(): _actief_slot() zonder FOR UPDATE, dan                  *)
(*    frappe.db.delete("Opslagvergrendeling", {"bestand": ...}) en een     *)
(*    insert. Dit draait in de gewone transactie van het verzoek.          *)
(*  - ontgrendel(): _actief_slot(), dan delete van alle sloten.            *)
(*  - _registreer(): eigen transactie onder READ COMMITTED, eerst een slot *)
(*    op de regel van het bestand (_vergrendel_bestand), dan               *)
(*    _actief_slot(for_update=True).                                       *)
(*  - De DocType heeft in PR #9 een index op `bestand` (search_index).     *)
(*                                                                         *)
(* Isolatie van het verzoek (constante Isolatie):                          *)
(*  "RR"    REPEATABLE READ (standaard MariaDB): een gewone lezing komt    *)
(*          uit de momentopname van het begin van het verzoek; DELETE en   *)
(*          INSERT werken op de laatste vastgelegde stand.                 *)
(*  "RR_SI" als RR, met innodb_snapshot_isolation=ON (MariaDB 11.6+): een  *)
(*          DELETE van een regel die na de momentopname veranderde, geeft  *)
(*          fout 1020; het verzoek mislukt.                                *)
(*  "RC"    READ COMMITTED: elke lezing ziet de laatste vastgelegde stand. *)
(*                                                                         *)
(* Fix (constante Fix): het voorstel uit het rapport. vergrendel en        *)
(* ontgrendel lopen in _eigen_transactie(), nemen eerst het slot op de     *)
(* regel van het bestand (zoals _registreer) en lezen het slot daarna met  *)
(* FOR UPDATE. Controle en schrijven worden zo één stap, die wacht op een  *)
(* lopende _registreer.                                                    *)
(*                                                                         *)
(* Abstractie: DELETE + INSERT + commit van vergrendel is één stap. Onder  *)
(* RR met een index op `bestand` nemen twee gelijktijdige DELETEs elk een  *)
(* gap lock; hun INSERTs geven dan een deadlock en één verzoek mislukt.    *)
(* Die volgorde levert alleen een fout op, geen nieuwe toestand, en laten  *)
(* we weg.                                                                 *)
(***************************************************************************)
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS
    Gebruikers,
    Isolatie,
    Fix,
    MaxVerzoeken,   \* hoogstens zoveel verzoeken per gebruiker
    Geen            \* modelwaarde

ASSUME Isolatie \in {"RR", "RR_SI", "RC"}
ASSUME Fix \in BOOLEAN

VARIABLES
    rijen,      \* vastgelegde sloten: verzameling van [id, g]
    verlopen,   \* ids van sloten waarvan de tijd om is (tijd, geen wijziging in de database)
    volgId,     \* volgend id van een slotregel
    rijslot,    \* slot op de regel van het bestand: Geen of een gebruiker (alleen _registreer, of vergrendel met Fix)
    vz,         \* [Gebruikers -> lopend verzoek]
    aantal,     \* [Gebruikers -> Nat]: verzoeken tot nu toe
    houdt,      \* [Gebruikers -> BOOLEAN]: de client kreeg "vergrendeld" en weet niet van een einde
    gestolen,   \* geldige sloten die een verzoek van een ánder wiste (geen beheerder, niet verlopen)
    onderSlot   \* nieuwe versies die vastgelegd werden terwijl een ander een geldig slot had

vars == <<rijen, verlopen, volgId, rijslot, vz, aantal, houdt, gestolen, onderSlot>>

Rust == [soort |-> "geen", fase |-> "geen", snap |-> {}]

Geldig(R) == {r \in R : r.id \notin verlopen}

Ids(R) == {r.id : r \in R}

Init ==
    /\ rijen = {}
    /\ verlopen = {}
    /\ volgId = 1
    /\ rijslot = Geen
    /\ vz = [u \in Gebruikers |-> Rust]
    /\ aantal = [u \in Gebruikers |-> 0]
    /\ houdt = [u \in Gebruikers |-> FALSE]
    /\ gestolen = {}
    /\ onderSlot = {}

\* Een nieuw verzoek; onder RR legt de eerste lezing van het verzoek de momentopname vast.
Begin(u, soort) ==
    /\ vz[u].soort = "geen"
    /\ aantal[u] < MaxVerzoeken
    /\ vz' = [vz EXCEPT ![u] = [soort |-> soort, fase |-> "controleer", snap |-> rijen]]
    /\ aantal' = [aantal EXCEPT ![u] = @ + 1]
    /\ UNCHANGED <<rijen, verlopen, volgId, rijslot, houdt, gestolen, onderSlot>>

\* Wat een gewone lezing ziet.
Zicht(u) == IF Isolatie = "RC" THEN rijen ELSE vz[u].snap

Klaar(u) == vz' = [vz EXCEPT ![u] = Rust]

\* Sloten van anderen die door dit verzoek verdwijnen (geldig, dus geen verlopen slot).
Gewist(u) == {r \in Geldig(rijen) : r.g # u}

(***************************************************************************)
(* vergrendel                                                              *)
(***************************************************************************)

\* Zonder Fix: controle uit een gewone lezing.
VergrendelControleer(u) ==
    /\ ~Fix
    /\ vz[u].soort = "vergrendel" /\ vz[u].fase = "controleer"
    /\ IF \E r \in Geldig(Zicht(u)) : r.g # u
          THEN Klaar(u)    \* "Dit bestand wordt bewerkt door ..."
          ELSE vz' = [vz EXCEPT ![u].fase = "schrijf"]
    /\ UNCHANGED <<rijen, verlopen, volgId, rijslot, aantal, houdt, gestolen, onderSlot>>

\* DELETE van alle sloten van het bestand, INSERT van het eigen slot, commit.
VergrendelSchrijf(u) ==
    /\ ~Fix
    /\ vz[u].soort = "vergrendel" /\ vz[u].fase = "schrijf"
    /\ IF Isolatie = "RR_SI" /\ Ids(rijen) # Ids(vz[u].snap)
          THEN \* fout 1020: een te wissen regel veranderde na de momentopname
               /\ Klaar(u)
               /\ UNCHANGED <<rijen, verlopen, volgId, houdt, gestolen>>
          ELSE /\ gestolen' = gestolen \cup Gewist(u)
               /\ rijen' = {[id |-> volgId, g |-> u]}
               /\ volgId' = volgId + 1
               /\ houdt' = [v \in Gebruikers |-> IF v = u THEN TRUE ELSE houdt[v]]
               /\ Klaar(u)
    /\ UNCHANGED <<verlopen, rijslot, aantal, onderSlot>>

\* Met Fix: eigen transactie, eerst het slot op de bestandsregel, dan een vergrendelende lezing.
VergrendelFix(u) ==
    /\ Fix
    /\ vz[u].soort = "vergrendel" /\ vz[u].fase = "controleer"
    /\ rijslot = Geen
    /\ IF \E r \in Geldig(rijen) : r.g # u
          THEN /\ Klaar(u)
               /\ UNCHANGED <<rijen, verlopen, volgId, houdt, gestolen>>
          ELSE /\ rijen' = {[id |-> volgId, g |-> u]}
               /\ volgId' = volgId + 1
               /\ houdt' = [houdt EXCEPT ![u] = TRUE]
               /\ gestolen' = gestolen \cup Gewist(u)
               /\ Klaar(u)
    /\ UNCHANGED <<verlopen, rijslot, aantal, onderSlot>>

(***************************************************************************)
(* ontgrendel                                                              *)
(***************************************************************************)

OntgrendelControleer(u) ==
    /\ ~Fix
    /\ vz[u].soort = "ontgrendel" /\ vz[u].fase = "controleer"
    /\ LET zicht == Geldig(Zicht(u)) IN
         IF zicht = {}
            THEN /\ Klaar(u)                              \* geen slot: niets te doen
                 /\ houdt' = [houdt EXCEPT ![u] = FALSE]
            ELSE IF \E r \in zicht : r.g # u
                 THEN /\ Klaar(u)                         \* "Alleen wie het bestand vergrendelde ..."
                      /\ UNCHANGED houdt
                 ELSE /\ vz' = [vz EXCEPT ![u].fase = "schrijf"]
                      /\ UNCHANGED houdt
    /\ UNCHANGED <<rijen, verlopen, volgId, rijslot, aantal, gestolen, onderSlot>>

OntgrendelSchrijf(u) ==
    /\ ~Fix
    /\ vz[u].soort = "ontgrendel" /\ vz[u].fase = "schrijf"
    /\ IF Isolatie = "RR_SI" /\ Ids(rijen) # Ids(vz[u].snap)
          THEN /\ Klaar(u)
               /\ UNCHANGED <<rijen, houdt, gestolen>>
          ELSE /\ gestolen' = gestolen \cup Gewist(u)
               /\ rijen' = {}
               /\ houdt' = [houdt EXCEPT ![u] = FALSE]
               /\ Klaar(u)
    /\ UNCHANGED <<verlopen, volgId, rijslot, aantal, onderSlot>>

OntgrendelFix(u) ==
    /\ Fix
    /\ vz[u].soort = "ontgrendel" /\ vz[u].fase = "controleer"
    /\ rijslot = Geen
    /\ IF \E r \in Geldig(rijen) : r.g # u
          THEN /\ Klaar(u)
               /\ UNCHANGED <<rijen, houdt>>
          ELSE /\ rijen' = {}
               /\ houdt' = [houdt EXCEPT ![u] = FALSE]
               /\ Klaar(u)
    /\ UNCHANGED <<verlopen, volgId, rijslot, aantal, gestolen, onderSlot>>

(***************************************************************************)
(* Upload van een nieuwe versie: _registreer (eigen transactie, RC)        *)
(***************************************************************************)

\* _vergrendel_bestand + _actief_slot(for_update=True).
UploadControleer(u) ==
    /\ vz[u].soort = "upload" /\ vz[u].fase = "controleer"
    /\ rijslot = Geen
    /\ IF \E r \in Geldig(rijen) : r.g # u
          THEN /\ Klaar(u)                \* conflictkopie (of bestand_vergrendeld): geen nieuwe versie
               /\ UNCHANGED rijslot
          ELSE /\ vz' = [vz EXCEPT ![u].fase = "schrijf"]
               /\ rijslot' = u
    /\ UNCHANGED <<rijen, verlopen, volgId, aantal, houdt, gestolen, onderSlot>>

\* _nieuwe_versie en commit; het slot op de bestandsregel gaat weg.
UploadSchrijf(u) ==
    /\ vz[u].soort = "upload" /\ vz[u].fase = "schrijf"
    /\ onderSlot' = onderSlot \cup {r \in Geldig(rijen) : r.g # u}
    /\ rijslot' = Geen
    /\ Klaar(u)
    /\ UNCHANGED <<rijen, verlopen, volgId, aantal, houdt, gestolen>>

(***************************************************************************)
(* Beheerder en tijd                                                       *)
(***************************************************************************)

\* forceer_ontgrendel: mag, en de client hoort het via de wijzigingenlijst.
Forceer ==
    /\ rijen # {}
    /\ rijen' = {}
    /\ houdt' = [u \in Gebruikers |-> FALSE]
    /\ UNCHANGED <<verlopen, volgId, rijslot, vz, aantal, gestolen, onderSlot>>

\* Een slot verloopt na 8 uur; de client weet dat ook.
Verloop(r) ==
    /\ r \in Geldig(rijen)
    /\ verlopen' = verlopen \cup {r.id}
    /\ houdt' = [houdt EXCEPT ![r.g] = FALSE]
    /\ UNCHANGED <<rijen, verlopen, volgId, rijslot, vz, aantal, gestolen, onderSlot>>

Next ==
    \/ \E u \in Gebruikers, s \in {"vergrendel", "ontgrendel", "upload"} : Begin(u, s)
    \/ \E u \in Gebruikers :
          \/ VergrendelControleer(u) \/ VergrendelSchrijf(u) \/ VergrendelFix(u)
          \/ OntgrendelControleer(u) \/ OntgrendelSchrijf(u) \/ OntgrendelFix(u)
          \/ UploadControleer(u) \/ UploadSchrijf(u)
    \/ Forceer
    \/ \E r \in rijen : Verloop(r)

Spec == Init /\ [][Next]_vars

(***************************************************************************)
(* Eigenschappen                                                           *)
(***************************************************************************)

\* L1. In de database staat hoogstens één geldig slot.
EenSlotInDatabase == Cardinality(Geldig(rijen)) <= 1

\* L2. Een geldig slot verdwijnt alleen door de houder zelf, door verlopen of door een beheerder.
GeenDiefstal == gestolen = {}

\* L3. Nooit twee clients die allebei "vergrendeld" kregen en niets van een einde weten.
EenHouder == \A u, v \in Gebruikers : (u # v) => ~(houdt[u] /\ houdt[v])

\* L4. Terwijl iemand een geldig slot heeft, legt een ander geen nieuwe versie vast.
SlotBeschermt == onderSlot = {}

=============================================================================
